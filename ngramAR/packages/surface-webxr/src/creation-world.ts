// @ts-nocheck
import * as THREE from "three";
import { WorldStore } from "@ngram-ar/core";
import { getWorld, getRapier, onPhysicsCollision, getGroundCollider } from "./physics-world.js";
import { colliderDescriptor, collisionDetail } from "./figment-physics.js";
import { resolveFigmentAnchors } from "./figment-anchors.js";
import { loadCreationAsset, disposeCreationAsset } from "./creation-assets.js";

const vec = (a) => ({ x: a[0], y: a[1], z: a[2] });
const equal = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const quaternion = (rotation) =>
  new THREE.Quaternion().setFromEuler(new THREE.Euler(...rotation));

/** Renderer adapter for the public world contract; independent of any model/harness. */
export class CreationWorld {
  root = new THREE.Group();
  entries = new Map();
  joints = new Map();
  store: WorldStore;
  paused = false;
  metrics = { frames: 0, frameTimeMs: 0, physicsBodies: 0, instances: 0 };
  constructor(scene) {
    this.root.name = "ngram-creations";
    scene.add(this.root);
    this.store = new WorldStore({
      commit: (before, after, effects) => this.commit(before, after, effects),
      sample: (e) => this.sample(e.id),
    });
    this.unsubscribePhysics = onPhysicsCollision((a, b, started) => this.collision(a, b, started));
  }
  private geometry(g) {
    const [x, y, z] = g.size;
    switch (g.shape) {
      case "sphere": {
        const geo = new THREE.SphereGeometry(0.5, 24, 16);
        geo.scale(x, y, z);
        return geo;
      }
      case "cylinder":
        return new THREE.CylinderGeometry(x / 2, x / 2, y, 32).scale(
          1,
          1,
          z / x,
        );
      case "cone":
        return new THREE.ConeGeometry(x / 2, y, 32).scale(1, 1, z / x);
      case "torus":
        return new THREE.TorusGeometry(0.35, 0.15, 12, 48).scale(x, y, z);
      case "mesh":
      case "line": {
        const geo = new THREE.BufferGeometry();
        geo.setAttribute(
          "position",
          new THREE.Float32BufferAttribute(g.vertices, 3),
        );
        if (g.indices) {
          geo.setIndex(g.indices);
          geo.computeVertexNormals();
        }
        geo.computeBoundingSphere();
        return geo;
      }
      default:
        return new THREE.BoxGeometry(x, y, z);
    }
  }
  private build(e) {
    const node = new THREE.Group();
    node.name = e.name;
    node.userData.creationId = e.id;
    let visual;
    if (e.kind === "light")
      visual = new THREE.PointLight(
        e.material.color,
        e.material.glow || 1,
        e.geometry.size[0] * 10,
        2,
      );
    else if (e.kind === "control") {
      visual = new THREE.Mesh(
        new THREE.PlaneGeometry(e.geometry.size[0], e.geometry.size[1]),
        new THREE.MeshBasicMaterial({
          map: this.controlTexture(e),
          side: THREE.DoubleSide,
        }),
      );
    } else if (e.kind === "shape" || e.kind === "asset") {
      const geo = this.geometry(e.geometry),
        m = e.material;
      const mat =
        e.geometry.shape === "line"
          ? new THREE.LineBasicMaterial({
              color: m.color,
              transparent: m.opacity < 1,
              opacity: m.opacity,
            })
          : new THREE.MeshStandardMaterial({
              color: m.color,
              roughness: m.roughness,
              metalness: m.metalness,
              transparent: m.opacity < 1,
              opacity: m.opacity,
              emissive: m.emissive,
              emissiveIntensity: m.glow,
              side: THREE.DoubleSide,
            });
      if (e.geometry.instances) {
        visual = new THREE.InstancedMesh(geo, mat, e.geometry.instances.length);
        e.geometry.instances.forEach((p, i) =>
          visual.setMatrixAt(i, new THREE.Matrix4().makeTranslation(...p)),
        );
        visual.instanceMatrix.needsUpdate = true;
        visual.computeBoundingSphere();
      } else
        visual =
          e.geometry.shape === "line"
            ? new THREE.Line(geo, mat)
            : new THREE.Mesh(geo, mat);
      visual.castShadow = true;
      visual.receiveShadow = true;
    }
    if (visual) {
      visual.userData.creationId = e.id;
      node.add(visual);
    }
    node.position.fromArray(e.transform.position);
    node.rotation.set(...e.transform.rotation);
    node.scale.fromArray(e.transform.scale);
    node.visible = e.visible;
    return {
      node,
      visual,
      body: null,
      collider: null,
      spec: e,
      status: e.asset ? "loading" : "ready",
      error: null,
      progress: null,
      abort: null,
    };
  }
  private startAsset(entry) {
    entry.abort?.abort();
    entry.status = "loading";
    entry.error = null;
    entry.progress = null;
    const controller = new AbortController();
    entry.abort = controller;
    loadCreationAsset(entry.spec.asset, controller.signal, (progress) => {
      entry.progress = progress;
    })
      .then((visual) => {
        if (
          this.entries.get(entry.spec.id) !== entry ||
          controller.signal.aborted
        ) {
          disposeCreationAsset(visual);
          return;
        }
        entry.visual.removeFromParent();
        disposeCreationAsset(entry.visual);
        visual.traverse((o) => {
          o.userData.creationId = entry.spec.id;
          if (o.isMesh) {
            o.castShadow = true;
            o.receiveShadow = true;
          }
        });
        entry.visual = visual;
        entry.mixer?.stopAllAction();
        entry.mixer?.uncacheRoot(entry.mixer.getRoot());
        entry.mixer = null;
        const clips = visual.userData.animationClips;
        if (clips?.length) {
          entry.mixer = new THREE.AnimationMixer(visual);
          // Multiple authored actions are alternatives; start the first clip.
          entry.mixer.clipAction(clips[0]).play();
        }
        if (!entry.spec.asset.preserveMaterials) this.applyMaterial(entry);
        entry.node.add(visual);
        entry.status = "ready";
        entry.abort = null;
        this.store.emit("asset.ready", "renderer", entry.spec.id, {
          bounds: this.sample(entry.spec.id).bounds,
        });
        this.store.onChange?.();
      })
      .catch((error) => {
        if (
          this.entries.get(entry.spec.id) !== entry ||
          controller.signal.aborted
        )
          return;
        entry.status = "failed";
        entry.error = String(error.message).slice(0, 300);
        entry.abort = null;
        this.store.emit("asset.failed", "renderer", entry.spec.id, {
          error: entry.error,
        });
        this.store.onChange?.();
      });
  }
  assetJob(command, id) {
    const entry = this.entries.get(id);
    if (!entry?.spec.asset) throw Error("Unknown asset entity");
    if (!["cancel", "retry"].includes(command))
      throw Error("Use cancel or retry");
    if (command === "cancel" && entry.status === "ready")
      return { id, status: "ready" };
    entry.abort?.abort();
    entry.error = null;
    entry.progress = null;
    if (command === "cancel") {
      entry.abort = null;
      entry.status = "cancelled";
      this.store.emit("asset.cancelled", "agent", id);
    } else {
      entry.status = "loading";
      this.startAsset(entry);
    }
    this.store.onChange?.();
    return { id, status: entry.status };
  }
  private controlTexture(e) {
    const canvas = document.createElement("canvas");
    canvas.width = 768;
    canvas.height = 224;
    const c = canvas.getContext("2d"),
      control = e.control;
    c.fillStyle = "#22284A";
    c.fillRect(8, 12, 760, 212);
    c.fillStyle = e.material.color;
    c.fillRect(0, 0, 752, 208);
    c.fillStyle = "#FFFFFF";
    c.font = "bold 30px Azeret Mono, monospace";
    c.fillText(control.label.slice(0, 34), 26, 54);
    if (control.type === "slider") {
      const fraction =
        (control.value - control.min) / (control.max - control.min);
      c.globalAlpha = 0.3;
      c.fillRect(28, 125, 690, 8);
      c.globalAlpha = 1;
      c.fillRect(28, 125, 690 * fraction, 8);
      c.beginPath();
      c.arc(28 + 690 * fraction, 129, 18, 0, Math.PI * 2);
      c.fill();
      c.font = "26px Azeret Mono, monospace";
      c.fillText(Number(control.value.toFixed(3)).toString(), 28, 95);
    } else {
      c.font = "24px Azeret Mono, monospace";
      c.fillText(
        control.type === "toggle"
          ? control.value > control.min
            ? "ON"
            : "OFF"
          : "PRESS",
        28,
        139,
      );
    }
    const texture = new THREE.CanvasTexture(canvas);
    texture.colorSpace = THREE.SRGBColorSpace;
    return texture;
  }
  private makeBody(entry) {
    const e = entry.spec,
      p = e.physics,
      R = getRapier(),
      world = getWorld();
    if (!p) return;
    if (!R || !world) throw new Error("Physics is not ready");
    const desc =
      p.mode === "fixed"
        ? R.RigidBodyDesc.fixed()
        : p.mode === "kinematic"
          ? R.RigidBodyDesc.kinematicPositionBased()
          : R.RigidBodyDesc.dynamic();
    desc
      .setTranslation(...e.transform.position)
      .setRotation(quaternion(e.transform.rotation))
      .setLinearDamping(p.linearDamping)
      .setAngularDamping(p.angularDamping)
      .setCcdEnabled(true);
    const body = world.createRigidBody(desc);
    entry.body = body;
    body.setEnabledTranslations(...p.translations, true);
    body.setEnabledRotations(...p.rotations, true);
    entry.colliders = [];
    if (p.colliders.length) {
      const solids = p.colliders.filter(c => !c.sensor).length;
      for (const c of p.colliders) {
        const descriptor = colliderDescriptor(R, c, e.transform.scale)
          .setMass(c.sensor ? 0 : p.mass / solids)
          .setRestitution(p.restitution).setFriction(p.friction);
        entry.colliders.push({ collider: world.createCollider(descriptor, body), id: c.id });
      }
      // Sensor-only bodies still have adjustable mass and respond to impulses.
      if (!solids) body.setAdditionalMass(p.mass, true);
      entry.collider = entry.colliders[0].collider;
      if (this.paused) body.setEnabled(false);
      return;
    }
    const [x, y, z] = e.geometry.size.map((n, i) => n * e.transform.scale[i]);
    // Elliptical curved primitives use a convex hull so collider and visible scale agree.
    let colliderDesc;
    if (e.geometry.shape === "box")
      colliderDesc = R.ColliderDesc.cuboid(x / 2, y / 2, z / 2);
    else if (e.geometry.shape === "sphere" && x === y && y === z)
      colliderDesc = R.ColliderDesc.ball(x / 2);
    else {
      const geo = this.geometry(e.geometry);
      geo.scale(...e.transform.scale);
      colliderDesc = R.ColliderDesc.convexHull(
        new Float32Array(geo.getAttribute("position").array),
      );
      geo.dispose();
    }
    if (!colliderDesc) {
      world.removeRigidBody(body);
      throw new Error(`Cannot construct collider for ${e.id}`);
    }
    colliderDesc
      .setMass(p.mass)
      .setActiveEvents(R.ActiveEvents.COLLISION_EVENTS)
      .setRestitution(p.restitution)
      .setFriction(p.friction);
    entry.body = body;
    entry.collider = world.createCollider(colliderDesc, body);
    entry.colliders = [{ collider: entry.collider, id: "body" }];
    if (this.paused) body.setEnabled(false);
  }
  private removeEntry(id) {
    const entry = this.entries.get(id);
    if (!entry) return;
    entry.abort?.abort();
    entry.mixer?.stopAllAction();
    entry.mixer?.uncacheRoot(entry.mixer.getRoot());
    if (entry.body?.isValid()) getWorld().removeRigidBody(entry.body);
    // Child entities own their resources and will be reparented by the commit.
    for (const child of [...entry.node.children])
      if (child.userData.creationId && child !== entry.visual)
        child.removeFromParent();
    entry.node.removeFromParent();
    disposeCreationAsset(entry.node);
    this.entries.delete(id);
  }
  private updateEntry(entry, e) {
    const old = entry.spec;
    if (!equal(old.geometry, e.geometry)) {
      const g = e.geometry;
      if (entry.visual?.isInstancedMesh && g.instances) {
        g.instances.forEach((p, i) =>
          entry.visual.setMatrixAt(
            i,
            new THREE.Matrix4().makeTranslation(...p),
          ),
        );
        entry.visual.instanceMatrix.needsUpdate = true;
        entry.visual.computeBoundingSphere();
      } else if (g.vertices && entry.visual?.geometry) {
        const geo = entry.visual.geometry;
        geo.getAttribute("position").array.set(g.vertices);
        geo.getAttribute("position").needsUpdate = true;
        if (g.indices) geo.computeVertexNormals();
        geo.computeBoundingSphere();
        geo.computeBoundingBox();
      }
    }
    if (!equal(old.transform, e.transform)) {
      entry.node.position.fromArray(e.transform.position);
      entry.node.rotation.set(...e.transform.rotation);
      entry.node.scale.fromArray(e.transform.scale);
      if (entry.body) {
        entry.body.setTranslation(vec(e.transform.position), true);
        entry.body.setRotation(quaternion(e.transform.rotation), true);
      }
    }
    entry.node.name = e.name;
    entry.node.visible = e.visible;
    if (
      e.asset &&
      !e.asset.preserveMaterials &&
      (old.asset.preserveMaterials || !equal(old.material, e.material))
    )
      this.applyMaterial({ ...entry, spec: e });
    if (!equal(old.material, e.material)) {
      const m = entry.visual?.material,
        p = e.material;
      if (m) {
        m.color.set(p.color);
        m.opacity = p.opacity;
        m.transparent = p.opacity < 1;
        if (m.emissive) {
          m.emissive.set(p.emissive);
          m.emissiveIntensity = p.glow;
          m.roughness = p.roughness;
          m.metalness = p.metalness;
        }
        m.needsUpdate = true;
      }
      if (entry.visual?.isLight) {
        entry.visual.color.set(p.color);
        entry.visual.intensity = p.glow || 1;
      }
    }
    if (
      e.kind === "control" &&
      (!equal(old.control, e.control) || !equal(old.material, e.material))
    ) {
      entry.visual.material.map.dispose();
      entry.visual.material.map = this.controlTexture(e);
      entry.visual.material.needsUpdate = true;
    }
    entry.spec = e;
  }
  private applyMaterial(entry) {
    const p = entry.spec.material;
    entry.visual?.traverse((node) => {
      for (const material of Array.isArray(node.material)
        ? node.material
        : [node.material])
        if (material) {
          material.color?.set(p.color);
          material.opacity = p.opacity;
          material.transparent = p.opacity < 1;
          if (material.emissive) {
            material.emissive.set(p.emissive);
            material.emissiveIntensity = p.glow;
            material.roughness = p.roughness;
            material.metalness = p.metalness;
          }
          material.needsUpdate = true;
        }
    });
  }
  private makeJoint(j, entries = this.entries) {
    const R = getRapier(),
      a = entries.get(j.a)?.body,
      b = entries.get(j.b)?.body;
    if (!a || !b) throw new Error(`Missing bodies for joint ${j.id}`);
    const aa = vec(j.anchorA),
      bb = vec(j.anchorB),
      axis = vec(j.axis);
    const data =
      j.type === "hinge"
        ? R.JointData.revolute(aa, bb, axis)
        : j.type === "slider"
          ? R.JointData.prismatic(aa, bb, axis)
          : j.type === "spring"
            ? R.JointData.spring(j.length, j.stiffness, j.damping, aa, bb)
            : j.type === "rope"
              ? R.JointData.rope(j.length, aa, bb)
              : R.JointData.spherical(aa, bb);
    const joint = getWorld().createImpulseJoint(data, a, b, true);
    joint.setContactsEnabled(false);
    if (j.limits) joint.setLimits(...j.limits);
    if (["hinge", "slider"].includes(j.type))
      joint.configureMotorVelocity(j.velocity, j.strength);
    return { joint, spec: j };
  }
  private commit(before, after, effects) {
    const changedBodies = new Set();
    const changedAssets = new Set();
    for (const e of after.entities) {
      const old = this.entries.get(e.id)?.spec;
      const assetChanged =
        old?.asset && e.asset
          ? old.asset.url !== e.asset.url ||
            old.asset.format !== e.asset.format ||
            old.asset.fit !== e.asset.fit ||
            old.asset.normalize !== e.asset.normalize ||
            (!old.asset.preserveMaterials && e.asset.preserveMaterials)
          : !equal(old?.asset, e.asset);
      if (old?.asset && e.asset && assetChanged) changedAssets.add(e.id);
      const dynamicGeometry =
        old &&
        !e.physics &&
        equal(
          { ...old.geometry, instances: undefined, vertices: undefined },
          { ...e.geometry, instances: undefined, vertices: undefined },
        ) &&
        old.geometry.instances?.length === e.geometry.instances?.length &&
        old.geometry.vertices?.length === e.geometry.vertices?.length;
      if (
        !old ||
        (assetChanged && !(old?.asset && e.asset)) ||
        (!dynamicGeometry && !equal(old.geometry, e.geometry)) ||
        !equal(old.physics, e.physics) ||
        (e.physics && !equal(old.transform.scale, e.transform.scale))
      )
        changedBodies.add(e.id);
    }
    // Allocate fallible resources before changing the visible graph. Physics
    // cannot step during this synchronous commit, so staged bodies stay inert.
    const staged = new Map();
    const stagedJoints = new Map();
    try {
      for (const e of after.entities)
        if (changedBodies.has(e.id)) {
          const entry = this.build(e);
          staged.set(e.id, entry);
          this.makeBody(entry);
        }
      const prospectiveEntries = new Map([...this.entries, ...staged]);
      for (const j of after.joints) {
        const previous = this.joints.get(j.id)?.spec;
        if (
          !previous ||
          changedBodies.has(j.a) ||
          changedBodies.has(j.b) ||
          !equal(
            { ...previous, velocity: 0, strength: 0 },
            { ...j, velocity: 0, strength: 0 },
          )
        ) {
          stagedJoints.set(j.id, this.makeJoint(j, prospectiveEntries));
        }
      }
    } catch (error) {
      for (const { joint } of stagedJoints.values())
        if (joint.isValid()) getWorld().removeImpulseJoint(joint, true);
      for (const entry of staged.values()) {
        if (entry.body?.isValid()) getWorld().removeRigidBody(entry.body);
        disposeCreationAsset(entry.node);
      }
      throw error;
    }
    for (const [id, { joint, spec }] of this.joints) {
      const next = after.joints.find((j) => j.id === id);
      if (
        !next ||
        changedBodies.has(spec.a) ||
        changedBodies.has(spec.b) ||
        !equal(
          { ...spec, velocity: 0, strength: 0 },
          { ...next, velocity: 0, strength: 0 },
        )
      ) {
        if (joint.isValid()) getWorld().removeImpulseJoint(joint, true);
        this.joints.delete(id);
      }
    }
    for (const id of [...this.entries.keys()])
      if (!after.entities.some((e) => e.id === id)) this.removeEntry(id);
    for (const e of after.entities) {
      let entry = this.entries.get(e.id);
      if (changedBodies.has(e.id)) {
        // Keep the actual simulated pose when only appearance/physics changes.
        const live =
          entry && equal(entry.spec.transform, e.transform)
            ? this.sample(e.id)?.transform
            : null;
        const velocity = entry?.body?.linvel(),
          angularVelocity = entry?.body?.angvel();
        const retained = e.asset && entry?.status === "ready" && !changedAssets.has(e.id)
          ? { visual: entry.visual, mixer: entry.mixer } : null;
        if (retained) {
          retained.visual.removeFromParent();
          entry.mixer = null;
        }
        this.removeEntry(e.id);
        entry = staged.get(e.id);
        this.entries.set(e.id, entry);
        if (live) this.setPose(e.id, live);
        if (entry.body && velocity) {
          entry.body.setLinvel(vec([velocity.x, velocity.y, velocity.z].map((n, i) => e.physics.translations[i] ? n : 0)), true);
          entry.body.setAngvel(vec([angularVelocity.x, angularVelocity.y, angularVelocity.z].map((n, i) => e.physics.rotations[i] ? n : 0)), true);
        }
        if (retained) {
          disposeCreationAsset(entry.visual);
          entry.visual?.removeFromParent();
          entry.visual = retained.visual;
          entry.mixer = retained.mixer;
          entry.node.add(retained.visual);
          entry.status = "ready";
          entry.progress = 1;
          if (!e.asset.preserveMaterials) this.applyMaterial(entry);
        } else if (e.asset) this.startAsset(entry);
      } else {
        this.updateEntry(entry, e);
        // Keep the previous visible mesh and live placement until the newest
        // revision finishes loading. Aborted/failed loads never blank the object.
        if (changedAssets.has(e.id)) this.startAsset(entry);
      }
    }
    for (const e of after.entities) {
      const node = this.entries.get(e.id).node;
      const parent = e.parent ? this.entries.get(e.parent).node : this.root;
      if (node.parent !== parent) parent.add(node);
    }
    this.root.updateMatrixWorld(true);
    for (const j of after.joints) {
      const existing = this.joints.get(j.id);
      if (!existing) this.joints.set(j.id, stagedJoints.get(j.id));
      else if (
        j.velocity !== existing.spec.velocity ||
        j.strength !== existing.spec.strength
      ) {
        existing.joint.configureMotorVelocity(j.velocity, j.strength);
        existing.spec = j;
      }
    }
    for (const effect of effects) {
      const body = this.entries.get(effect.id)?.body;
      body.applyImpulse(vec(effect.impulse), true);
      if (effect.torque) body.applyTorqueImpulse(vec(effect.torque), true);
    }
  }
  sample(id) {
    const entry = this.entries.get(id);
    if (!entry) return {};
    const node = entry.node;
    if (entry.body && !this.store.locks.has(id)) {
      const p = entry.body.translation(),
        q = entry.body.rotation();
      node.position.set(p.x, p.y, p.z);
      node.quaternion.set(q.x, q.y, q.z, q.w);
    }
    node.updateWorldMatrix(true, true);
    const bounds = new THREE.Box3().setFromObject(node);
    return {
      transform: {
        position: node.position.toArray(),
        rotation: [node.rotation.x, node.rotation.y, node.rotation.z],
        scale: node.scale.toArray(),
      },
      worldPosition: node.getWorldPosition(new THREE.Vector3()).toArray(),
      bounds: bounds.isEmpty()
        ? null
        : { min: bounds.min.toArray(), max: bounds.max.toArray() },
      ready: entry.status === "ready",
      status: entry.status,
      error: entry.error,
      progress: entry.progress,
      ...(entry.spec.figment ? { anchors: resolveFigmentAnchors(this, id) } : {}),
      ...(entry.body
        ? {
            velocity: Object.values(entry.body.linvel()),
            angularVelocity: Object.values(entry.body.angvel()),
            mass: entry.body.mass(),
            sleeping: entry.body.isSleeping(),
          }
        : {}),
    };
  }
  setPose(id, transform) {
    const entry = this.entries.get(id);
    if (!entry) return;
    entry.node.position.fromArray(transform.position);
    entry.node.rotation.set(...transform.rotation);
    entry.node.scale.fromArray(transform.scale);
    if (entry.body) {
      entry.body.setTranslation(vec(transform.position), true);
      entry.body.setRotation(entry.node.quaternion, true);
      if (entry.body.isKinematic())
        entry.body.setNextKinematicTranslation(vec(transform.position));
    }
  }
  hold(id, actor) {
    const entry = this.entries.get(id);
    if (!entry?.spec.grabbable || this.store.heldBy(id)) return false;
    for (const held of this.store.locks.keys()) {
      let node = this.entries.get(held)?.node;
      while (node) {
        if (node === entry.node) return false;
        node = node.parent;
      }
    }
    this.store.beginInteraction(actor);
    this.store.locks.set(id, actor);
    if (entry.body)
      entry.body.setBodyType(
        getRapier().RigidBodyType.KinematicPositionBased,
        true,
      );
    this.store.emit("grab", actor, id, this.sample(id));
    return true;
  }
  release(id, actor, velocity = [0, 0, 0]) {
    if (this.store.locks.get(id) !== actor) return;
    const entry = this.entries.get(id),
      transform = this.sample(id).transform;
    this.store.locks.delete(id);
    if (entry.body) {
      const R = getRapier();
      entry.body.setBodyType(
        entry.spec.physics.mode === "fixed"
          ? R.RigidBodyType.Fixed
          : entry.spec.physics.mode === "dynamic"
            ? R.RigidBodyType.Dynamic
            : R.RigidBodyType.KinematicPositionBased,
        true,
      );
      entry.body.setTranslation(vec(transform.position), true);
      entry.body.setRotation(entry.node.quaternion, true);
      entry.body.setLinvel(
        vec(velocity.map((n, i) => entry.spec.physics.translations[i] ? THREE.MathUtils.clamp(n, -8, 8) : 0)),
        true,
      );
    }
    this.store.apply(
      {
        requestId: crypto.randomUUID(),
        operations: [{ op: "entity.patch", id, patch: { transform } }],
      },
      actor,
    );
    this.store.emit("release", actor, id, { transform, velocity });
  }
  activate(id, fraction = 0.5, actor = "human") {
    const entry = this.entries.get(id),
      c = entry?.spec.control;
    if (!c) {
      const action = Object.keys(entry?.spec.figment?.actions ?? {})[0];
      if (action) this.store.emit("action", actor, id, { action });
      return;
    }
    const value =
      c.type === "toggle"
        ? c.value > c.min
          ? c.min
          : c.max
        : c.type === "button"
          ? c.max
          : THREE.MathUtils.clamp(
              c.min +
                Math.round((fraction * (c.max - c.min)) / c.step) * c.step,
              c.min,
              c.max,
            );
    if (c.type === "slider" && Math.abs(value - c.value) < c.step * 0.01)
      return;
    if (c.type !== "button")
      this.store.apply(
        {
          requestId: crypto.randomUUID(),
          operations: [
            { op: "entity.patch", id, patch: { control: { value } } },
          ],
        },
        actor,
      );
    this.store.emit("control", actor, id, { value });
  }
  pause(paused = true) {
    this.paused = paused;
    for (const e of this.entries.values()) e.body?.setEnabled(!paused);
    this.store.emit(
      paused ? "simulation.paused" : "simulation.resumed",
      "human",
    );
  }
  update(dt) {
    this.metrics.frames++;
    this.metrics.frameTimeMs = this.metrics.frameTimeMs
      ? this.metrics.frameTimeMs * 0.95 + dt * 1000 * 0.05
      : dt * 1000;
    this.metrics.physicsBodies = 0;
    this.metrics.instances = 0;
    for (const [id, e] of this.entries) {
      this.metrics.instances += e.spec.geometry.instances?.length ?? 0;
      if (!this.paused) e.mixer?.update(Math.min(dt, 0.1));
      if (!e.body) continue;
      this.metrics.physicsBodies++;
      if (
        !this.paused &&
        e.spec.physics.mode === "dynamic" &&
        !this.store.locks.has(id)
      ) {
        e.body.resetForces(false);
        const mass = e.body.mass();
        e.body.addForce(
          vec(e.spec.physics.gravity.map((n) => n * mass)),
          false,
        );
      }
      if (!this.store.locks.has(id)) {
        const p = e.body.translation(),
          q = e.body.rotation();
        e.node.position.set(p.x, p.y, p.z);
        e.node.quaternion.set(q.x, q.y, q.z, q.w);
      }
    }
  }
  private collision(aHandle, bHandle, started) {
    if (this.paused) return;
    const find = handle => {
      for (const [id, e] of this.entries) {
        const c = e.colliders?.find(c => c.collider.handle === handle);
        if (c) return { id, collider: c.collider, idInBody: c.id };
      }
      const collider = getWorld().getCollider(handle);
      return { id: handle === getGroundCollider()?.handle ? "ground" : null, collider, idInBody: null };
    };
    const a = find(aHandle), b = find(bHandle);
    if (!a.collider || !b.collider) return;
    const sensor = a.collider.isSensor() || b.collider.isSensor();
    const detail = started && !sensor ? collisionDetail(getWorld(), a.collider, b.collider) : {};
    for (const [own, other, flipped] of [[a, b, false], [b, a, true]]) {
      if (!this.entries.has(own.id)) continue;
      this.store.emit(sensor ? (started ? "sensor.enter" : "sensor.exit") : (started ? "collision" : "collision.end"), "physics", own.id, {
        ...detail, normal: detail.normal?.map(n => flipped ? -n : n) ?? null,
        other: other.id, collider: own.idInBody ?? own.id, otherCollider: other.idInBody ?? other.id,
      });
    }
  }
}
