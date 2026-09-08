// @ts-nocheck
// Isolated review surface: production styles, renderer, physics, sandbox and Objects UI.
// No harness, API key, remote agent or model requests are connected here.
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { CreationWorld } from "../packages/surface-webxr/src/creation-world.js";
import { CreationService } from "../packages/surface-webxr/src/creation-service.js";
import { CreationInput } from "../packages/surface-webxr/src/creation-input.js";
import { attachCreationStudio } from "../packages/surface-webxr/src/creation-studio.js";
import { initPhysics, stepPhysics } from "../packages/surface-webxr/src/physics-world.js";

await document.fonts.ready;
await initPhysics();
for (const selector of [".sidebar", ".command-bar", "#sidebar-toggle", "#notif-btn", "#settings-btn", "#ar-button"])
  document.querySelector(selector)?.remove();
document.querySelector(".viewport-container").style.left = "0";
document.querySelector("#status-text").textContent = "Figment review · local";
const canvas = document.querySelector("#viewport");
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.4;
renderer.shadowMap.enabled = true;
const scene = new THREE.Scene(); scene.background = new THREE.Color("#e4e5e2");
const camera = new THREE.PerspectiveCamera(45, 1, .01, 50);
camera.position.set(2.5, 2.1, 3.8);
const orbit = new OrbitControls(camera, canvas); orbit.target.set(0, .9, 0); orbit.update();
scene.add(new THREE.HemisphereLight(0xffffff, 0x6e7dff, 2));
const sun = new THREE.DirectionalLight(0xffffff, 3); sun.position.set(2,5,3); sun.castShadow = true; scene.add(sun);
const floor = new THREE.Mesh(new THREE.PlaneGeometry(30, 30), new THREE.MeshStandardMaterial({ color: "#dcddda", roughness: .9 }));
floor.rotation.x = -Math.PI / 2; floor.receiveShadow = true; scene.add(floor);
scene.add(new THREE.GridHelper(12, 48, 0xb8bcc9, 0xd2d4db));
const world = new CreationWorld(scene), service = new CreationService(world), input = new CreationInput(world, camera, canvas);
input.onDrag = dragging => { orbit.enabled = !dragging; };
const studio = attachCreationStudio(service, input, { origin: () => [0,0,0], focus: () => { orbit.target.set(0,1,0); orbit.update(); } });
await service.restore();
if (!world.entries.size && !service.restoreError) {
  await service.handle("apply", { requestId: "figment-demo", operations: [
    { op: "entity.create", entity: { id: "aster", name: "Aster", kind: "group", grabbable: true, transform: { position: [-.8,1,0] }, physics: { mode: "kinematic", colliders: [{ id: "body", shape: "cylinder", size: [.4,.5,.4] }, { id: "handle", size: [.32,.18,.05], position: [0,.34,0] }] } } },
    { op: "entity.create", entity: { id: "aster.bulb", name: "Light core", parent: "aster", geometry: { shape: "sphere", size: [.3,.36,.3] }, material: { color: "#ffffff", roughness: .2 } } },
    ...[-.22,.22].map((y, i) => ({ op: "entity.create", entity: { id: `aster.cap${i}`, name: "Ceramic shell", parent: "aster", transform: { position: [0,y,0] }, geometry: { shape: "cylinder", size: [.42,.05,.42] }, material: { color: "#ffffff", roughness: .18 } } })),
    ...Array.from({ length: 8 }, (_, i) => ({ op: "entity.create", entity: { id: `aster.rib${i}`, name: "Frame", parent: "aster", transform: { position: [Math.cos(i*Math.PI/4)*.19,0,Math.sin(i*Math.PI/4)*.19] }, geometry: { shape: "cylinder", size: [.012,.42,.012] }, material: { color: "#192050", metalness: .7 } } })),
    { op: "entity.create", entity: { id: "aster.handle", name: "Handle", parent: "aster", transform: { position: [0,.34,0] }, geometry: { shape: "torus", size: [.36,.3,.06] }, material: { color: "#192050", metalness: .5 } } },
    { op: "entity.create", entity: { id: "aster.light", name: "Light", kind: "light", parent: "aster", material: { color: "#6e7dff", glow: 2 }, geometry: { size: [.6,.6,.6] } } },
    { op: "entity.create", entity: { id: "crystal", name: "Impact crystal", transform: { position: [.2,1,0] }, geometry: { shape: "sphere", size: [.3,.3,.3] }, material: { color: "#6e7dff", roughness: .15, metalness: .4 }, physics: { restitution: .85, friction: .3, mass: .4, damping: .1 } } },
    { op: "entity.create", entity: { id: "sculpture", name: "Orbit", transform: { position: [1,1,0] }, geometry: { shape: "torus", size: [.55,.55,.55] }, material: { color: "#6e7dff", roughness: .2, metalness: .6 } } },
  ] });
  const presets = (await service.figments.handle({ command: "capabilities" })).presets;
  await service.figments.attach({ id: "aster", preset: "lantern", definition: {
    title: "Aster", parts: { bulb: "aster.bulb", light: "aster.light" },
    anchors: { handle: { position: [0,.44,0] } }, grips: { hold: { anchor: "handle" } },
    behavior: { ...presets.lantern.behavior, source: presets.lantern.behavior.source.replace('api.patch("self"', 'api.patch("bulb"') },
  } });
  await service.figments.attach({ id: "crystal", preset: "impact" });
  await service.figments.attach({ id: "sculpture", preset: "kinetic" });
  service.save();
}
input.select(world.entries.has("aster") ? "aster" : world.store.document.entities.find(e => e.figment)?.id);
const bar = document.querySelector(".topbar-actions");
function action(label, fn) {
  const button = document.createElement("button"); button.className = "modal-btn";
  button.textContent = label; button.onclick = fn; bar.append(button); return button;
}
action("Switch theme", () => {
  const dark = document.documentElement.getAttribute("data-theme") !== "dark";
  document.documentElement.setAttribute("data-theme", dark ? "dark" : "light");
  scene.background.set(dark ? "#121522" : "#e4e5e2");
  floor.material.color.set(dark ? "#181d2e" : "#dcddda");
});
action("Run lantern recipe", async () => {
  try {
    const calls = await (await fetch("/lantern-recipe.json")).json();
    for (const call of calls) {
      if (call.name === "ar_world") await service.handle(call.arguments.command, call.arguments.payload);
      else await service.figments.handle({ ...call.arguments.payload, command: call.arguments.command });
    }
    input.select("lantern");
    output.textContent = "PASS documented lantern recipe · running in the actual sandbox";
  } catch (error) { output.textContent = `FAIL recipe: ${error.message}`; }
});
action("Run runtime checks", async () => {
  const results = [];
  const check = (name, ok) => { results.push(`${ok ? "PASS" : "FAIL"} ${name}`); output.textContent = results.join("\n"); };
  try {
    const id = [...world.entries].find(([,e]) => e.spec.figment?.behavior)?.[0];
    if (!id) throw Error("Place a Figment with behavior before testing");
    await service.resume();
    await new Promise(resolve => setTimeout(resolve, 600));
    check("sandboxed Figment behavior advances", service.programs.inspect(service.figments.programId(id))[0].frames > 0);
    const record = service.programs.records.get(service.figments.programId(id));
    const before = record.frames;
    await service.pause();
    await new Promise(resolve => setTimeout(resolve, 200));
    check("Stop freezes local programs", record.frames === before);
    try {
      await service.programs.install({ id: "fixture.runaway", entityIds: [id], source: "while(true){}" });
      check("runaway code terminates", false);
    } catch { check("runaway code terminates", true); }
    await service.programs.install({ id: "fixture.network", entityIds: [id], source: `return {async tick(){try {await fetch("${location.origin}/sandbox-should-not-load");api.state.blocked=false;} catch {api.state.blocked=true;}}};` });
    world.pause(false);
    await new Promise(resolve => setTimeout(resolve, 600));
    check("worker network is blocked", service.programs.inspect("fixture.network")[0].state.blocked === true);
    await service.programs.command("remove", "fixture.network");
    await service.pause();
    service.save();
    check("persistent checkpoint saves", !service.storageError);
  } catch (error) { output.textContent += `\nFAIL ${error.message}`; }
});
const output = document.createElement("pre"); output.id = "figment-review-results"; output.setAttribute("role", "status");
output.style.cssText = "position:fixed;bottom:16px;left:20px;max-width:calc(100vw - 40px);font:11px var(--font-body);color:var(--text-secondary);pointer-events:none;white-space:pre-wrap;z-index:20";
output.textContent = "Isolated review · no agent or model connected"; document.body.append(output);
let last = performance.now();
renderer.setAnimationLoop(now => {
  const dt = Math.min(.05, (now - last)/1000); last = now;
  const { width, height } = canvas.parentElement.getBoundingClientRect();
  if (canvas.width !== Math.round(width * renderer.getPixelRatio()) || canvas.height !== Math.round(height * renderer.getPixelRatio())) {
    renderer.setSize(width, height, false); camera.aspect = width/height; camera.updateProjectionMatrix();
  }
  world.update(dt); stepPhysics(dt); input.update(); renderer.render(scene, camera);
});
