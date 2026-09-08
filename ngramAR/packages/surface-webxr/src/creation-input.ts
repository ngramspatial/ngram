// @ts-nocheck
import * as THREE from "three";

/** Same selection, ownership and control events for mouse, touch and XR rays. */
export class CreationInput {
  world;
  camera;
  canvas;
  onSelect = null;
  onDrag = null;
  selected = null;
  raycaster = new THREE.Raycaster();
  holds = new Map();
  outline;
  constructor(world, camera, canvas) {
    this.world = world;
    this.camera = camera;
    this.canvas = canvas;
    this.outline = new THREE.Box3Helper(new THREE.Box3(), 0x6e7dff);
    this.outline.visible = false;
    world.root.parent.add(this.outline);
    canvas.addEventListener("pointerdown", this.down, true);
    canvas.addEventListener("pointermove", this.move, true);
    canvas.addEventListener("pointerup", this.up, true);
    canvas.addEventListener("pointercancel", this.up, true);
    canvas.addEventListener("wheel", this.wheel, {
      capture: true,
      passive: false,
    });
    window.addEventListener("blur", () => this.releaseAll());
  }
  select(id) {
    this.selected = id;
    this.world.store.emit("select", "human", id);
    this.onSelect?.(id);
  }
  private ray(event) {
    const r = this.canvas.getBoundingClientRect();
    this.raycaster.setFromCamera(
      new THREE.Vector2(
        ((event.clientX - r.left) / r.width) * 2 - 1,
        (-(event.clientY - r.top) / r.height) * 2 + 1,
      ),
      this.camera,
    );
    return this.raycaster.ray;
  }
  private hit(ray) {
    this.raycaster.ray.copy(ray);
    this.world.root.updateMatrixWorld(true);
    return this.raycaster.intersectObjects(
      [...this.world.entries.values()]
        .filter(
          (e) =>
            e.spec.visible &&
            e.visual &&
            (e.spec.grabbable ||
              e.spec.kind === "control" ||
              this.grabbableParent(e.spec.id)),
        )
        .map((e) => e.visual),
      true,
    )[0];
  }
  private grabbableParent(id) {
    let parent = this.world.entries.get(id)?.spec.parent;
    while (parent) {
      const entry = this.world.entries.get(parent);
      if (entry?.spec.grabbable) return parent;
      parent = entry?.spec.parent;
    }
    return null;
  }
  private start(actor, ray) {
    const hit = this.hit(ray);
    if (!hit) return false;
    const hitId = hit.object.userData.creationId;
    const id = this.world.entries.get(hitId)?.spec.control
        ? hitId
        : (this.grabbableParent(hitId) ?? hitId),
      e = this.world.entries.get(id);
    this.select(id);
    if (e.spec.control) {
      this.world.activate(
        id,
        hit.uv ? THREE.MathUtils.clamp((hit.uv.x - 0.036) / 0.9, 0, 1) : 0.5,
        actor,
      );
      this.holds.set(actor, { id, control: true });
      return true;
    }
    const owner = this.world.store.locks.get(id);
    if (
      owner?.startsWith("xr:") &&
      actor.startsWith("xr:") &&
      owner !== actor &&
      this.holds.has(owner) &&
      ![...this.holds.values()].some((h) => h.primary === owner)
    ) {
      this.holds.set(actor, { id, primary: owner });
      return true;
    }
    if (!this.world.hold(id, actor)) return false;
    this.holds.set(actor, {
      id,
      distance: hit.distance,
      offset: e.node.getWorldPosition(new THREE.Vector3()).sub(hit.point),
      last: e.node.position.clone(),
      time: performance.now(),
      velocity: [0, 0, 0],
    });
    this.onDrag?.(true);
    return true;
  }
  private hold(actor, ray) {
    const hold = this.holds.get(actor);
    if (!hold) return;
    if (hold.control) {
      const hit = this.hit(ray);
      if (
        hit?.object.userData.creationId === hold.id &&
        this.world.entries.get(hold.id)?.spec.control.type === "slider"
      )
        this.world.activate(
          hold.id,
          THREE.MathUtils.clamp((hit.uv.x - 0.036) / 0.9, 0, 1),
          actor,
        );
      return;
    }
    const e = this.world.entries.get(hold.id);
    if (!e) {
      this.end(actor);
      return;
    }
    const p = ray.at(hold.distance, new THREE.Vector3()).add(hold.offset);
    e.node.parent.worldToLocal(p);
    const now = performance.now(),
      dt = Math.max(0.008, (now - hold.time) / 1000);
    hold.velocity = p.clone().sub(hold.last).divideScalar(dt).toArray();
    hold.last.copy(p);
    hold.time = now;
    this.world.setPose(hold.id, {
      position: p.toArray(),
      rotation: [e.node.rotation.x, e.node.rotation.y, e.node.rotation.z],
      scale: e.node.scale.toArray(),
    });
  }
  private end(actor) {
    const hold = this.holds.get(actor);
    if (!hold) return;
    if (hold.primary) {
      this.holds.delete(actor);
      this.end(hold.primary);
      return;
    }
    for (const [other, h] of this.holds)
      if (h.primary === actor) this.holds.delete(other);
    this.holds.delete(actor);
    if (!hold.control) this.world.release(hold.id, actor, hold.velocity);
    this.onSelect?.(this.selected);
    this.onDrag?.(this.holds.size > 0);
  }
  private down = (event) => {
    if (event.button !== 0) return;
    if (this.start(`pointer:${event.pointerId}`, this.ray(event))) {
      event.stopImmediatePropagation();
      this.canvas.setPointerCapture(event.pointerId);
      this.canvas.style.cursor = "grabbing";
    }
  };
  private move = (event) => {
    const actor = `pointer:${event.pointerId}`;
    if (this.holds.has(actor)) {
      event.stopImmediatePropagation();
      this.hold(actor, this.ray(event));
    }
  };
  private up = (event) => {
    const actor = `pointer:${event.pointerId}`;
    if (this.holds.has(actor)) {
      event.stopImmediatePropagation();
      this.end(actor);
      if (this.canvas.hasPointerCapture(event.pointerId))
        this.canvas.releasePointerCapture(event.pointerId);
      this.canvas.style.cursor = "";
    }
  };
  private wheel = (event) => {
    const hold = [...this.holds.values()].find((h) => !h.control);
    if (!hold) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    const entry = this.world.entries.get(hold.id);
    if (!entry) return;
    if (event.shiftKey) {
      entry.node.scale.multiplyScalar(Math.exp(-event.deltaY * 0.002));
      entry.node.scale.clampScalar(0.01, 20);
    } else if (event.altKey) {
      entry.node.rotation.y += event.deltaY * 0.005;
      if (entry.body) entry.body.setRotation(entry.node.quaternion, true);
    } else
      hold.distance = THREE.MathUtils.clamp(
        hold.distance * Math.exp(event.deltaY * 0.002),
        0.15,
        50,
      );
  };
  releaseAll() {
    for (const actor of [...this.holds.keys()]) this.end(actor);
  }
  updateXR(pointers) {
    const consumed = new Set(),
      active = new Set();
    for (const p of pointers) {
      const actor = `xr:${p.id}`;
      active.add(actor);
      if (p.isActive && !p.wasActive) this.start(actor, p.ray);
      if (this.holds.has(actor)) {
        consumed.add(p.id);
        if (!p.isActive) this.end(actor);
      }
    }
    for (const actor of this.holds.keys())
      if (actor.startsWith("xr:") && !active.has(actor)) this.end(actor);
    const paired = new Set();
    for (const [actor, hold] of this.holds)
      if (hold.primary) {
        const first = pointers.find((p) => `xr:${p.id}` === hold.primary),
          second = pointers.find((p) => `xr:${p.id}` === actor);
        if (!first?.isActive || !second?.isActive) continue;
        paired.add(actor);
        paired.add(hold.primary);
        const entry = this.world.entries.get(hold.id),
          vector = second.position.clone().sub(first.position),
          midpoint = first.position
            .clone()
            .add(second.position)
            .multiplyScalar(0.5);
        if (vector.length() < 0.04) continue;
        if (!hold.pair)
          hold.pair = {
            distance: vector.length(),
            direction: vector.clone().normalize(),
            midpoint: midpoint.clone(),
            position: entry.node.getWorldPosition(new THREE.Vector3()),
            rotation: entry.node.getWorldQuaternion(new THREE.Quaternion()),
            scale: entry.node.scale.clone(),
          };
        const ratio = THREE.MathUtils.clamp(
            vector.length() / hold.pair.distance,
            0.1,
            10,
          ),
          delta = new THREE.Quaternion().setFromUnitVectors(
            hold.pair.direction,
            vector.normalize(),
          );
        const position = hold.pair.position
          .clone()
          .sub(hold.pair.midpoint)
          .multiplyScalar(ratio)
          .applyQuaternion(delta)
          .add(midpoint);
        entry.node.parent.worldToLocal(position);
        const parentRotation = entry.node.parent
            .getWorldQuaternion(new THREE.Quaternion())
            .invert(),
          rotation = parentRotation
            .multiply(delta)
            .multiply(hold.pair.rotation),
          euler = new THREE.Euler().setFromQuaternion(rotation);
        this.world.setPose(hold.id, {
          position: position.toArray(),
          rotation: [euler.x, euler.y, euler.z],
          scale: hold.pair.scale
            .clone()
            .multiplyScalar(ratio)
            .clampScalar(0.01, 20)
            .toArray(),
        });
        this.holds.get(hold.primary).velocity = [0, 0, 0];
      }
    for (const p of pointers) {
      const actor = `xr:${p.id}`;
      if (p.isActive && this.holds.has(actor) && !paired.has(actor))
        this.hold(actor, p.ray);
    }
    return pointers.filter((p) => !consumed.has(p.id));
  }
  update() {
    const node = this.world.entries.get(this.selected)?.node;
    this.outline.visible = !!node;
    if (node) {
      node.updateWorldMatrix(true, true);
      this.outline.box.setFromObject(node);
    }
  }
}
