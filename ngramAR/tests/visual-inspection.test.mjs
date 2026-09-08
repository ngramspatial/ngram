import assert from 'node:assert/strict';
import test, { after } from 'node:test';
import { build } from 'esbuild';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve, sep } from 'node:path';
import { pathToFileURL } from 'node:url';
import { OpenAIBinding } from '@ngram-ar/bindings';

const dir = await mkdtemp(join(tmpdir(), 'ngram-visual-test-'));
after(async () => { assert.ok(resolve(dir).startsWith(resolve(tmpdir()) + sep)); await rm(dir, { recursive: true, force: true }); });
await build({ stdin: { contents: `
export * from './packages/surface-webxr/src/visual-inspection.ts';
export {Scene,PerspectiveCamera,Group,Mesh,BoxGeometry,MeshBasicMaterial,Box3,Vector3} from 'three';`, resolveDir: resolve('.') }, bundle: true, platform: 'node', format: 'esm', outfile: join(dir, 'suite.mjs'), logLevel: 'silent' });
const m = await import(pathToFileURL(join(dir, 'suite.mjs')));
const image = 'data:image/jpeg;base64,/9j/eA==';

function fixture(enabled = true) {
  const scene = new m.Scene(), camera = new m.PerspectiveCamera(65, 1.7, .01, 100);
  camera.position.set(6, 2, 6); camera.lookAt(0, 1, 0); camera.updateMatrixWorld(true);
  const mesh = new m.Mesh(new m.BoxGeometry(.04, 1, .03), new m.MeshBasicMaterial()); mesh.name = 'blade'; scene.add(mesh);
  const world = { entries: new Map([['sword', { node: mesh, spec: { name: 'Sword' }, status: 'ready' }]]), store: { document: { revision: 7 } } };
  const inspection = new m.VisualInspection({ xr: { isPresenting: false } }, scene, camera, world, () => enabled);
  const cameras = []; inspection.render = (camera) => { cameras.push(camera); return image; };
  return { inspection, scene, camera, world, cameras };
}

test('inspection cameras fit tall geometry from every named view without moving human', () => {
  const { camera: human } = fixture();
  const before = human.matrixWorld.toArray();
  const bounds = new m.Box3(new m.Vector3(-.02, -.5, -.015), new m.Vector3(.02, .5, .015));
  for (const angle of ['front','back','left','right','top','bottom','perspective']) {
    const camera = m.inspectionCamera(m.inspectionOptions({}), bounds, human, angle);
    for (const x of [bounds.min.x, bounds.max.x]) for (const y of [bounds.min.y, bounds.max.y]) for (const z of [bounds.min.z, bounds.max.z]) {
      const p = new m.Vector3(x,y,z).project(camera);
      assert.ok(Math.abs(p.x) < 1 && Math.abs(p.y) < 1 && p.z > -1 && p.z < 1, angle);
    }
  }
  assert.deepEqual(human.matrixWorld.toArray(), before);
});

test('current-view camera preserves the actual user projection', () => {
  const { camera: source } = fixture();
  const capture = m.inspectionCamera(m.inspectionOptions(), null, source, null);
  assert.deepEqual(capture.projectionMatrix.toArray(), source.projectionMatrix.toArray());
  assert.deepEqual(capture.position.toArray(), source.position.toArray());
});

test('bad camera requests and degenerate poses fail explicitly', () => {
  for (const value of [{views:[]},{views:['fake']},{size:1},{size:1000.5},{isolate:true},{node:'blade'},{orbit:[0,91]},{position:[0,NaN,0]}]) assert.throws(() => m.inspectionOptions(value));
  assert.throws(() => m.inspectionCamera(m.inspectionOptions({position:[0,0,0],lookAt:[0,0,0]}), null, fixture().camera, null), /differ/);
});

test('capture returns correlated images, never a second camera-frame event', async () => {
  const { inspection, cameras } = fixture(); const sent = [];
  await inspection.dispatch({actionId:'capture-1',options:{target:'sword',views:['front','right']}}, message => sent.push(message));
  assert.equal(sent.length, 1); assert.equal(sent[0].type, 'event:action_completed'); assert.equal(sent[0].completedActionId, 'capture-1');
  assert.equal(sent[0].status, 'completed'); assert.equal(sent[0].result.images.length, 2);
  assert.equal(sent[0].result.physicalCamera, false); assert.notDeepEqual(cameras[0].position, cameras[1].position);
});

test('sharing-off, Stop, loading and ambiguous nodes block captures', async () => {
  await assert.rejects(fixture(false).inspection.capture(), /off/);
  assert.equal((await fixture(false).inspection.capture({}, {manual:true})).images.length, 1);
  const { inspection, world } = fixture();
  const pending = inspection.capture(); inspection.cancel(); await assert.rejects(pending, /cancelled/);
  const entry = world.entries.get('sword'); entry.spec.asset = {}; entry.status = 'loading';
  await assert.rejects(inspection.capture({target:'sword'}), /not ready/);
  entry.status = 'ready';
  await assert.rejects(inspection.capture({target:'sword',node:'missing'}), /ambiguous/);
  assert.equal(inspection.busy, false);
});

test('XR current view uses the headset eye, with no camera mutation', async () => {
  const {inspection, cameras} = fixture(); const eye = new m.PerspectiveCamera(75, 1, .01, 50); eye.position.set(1,2,3); eye.updateMatrixWorld(true);
  inspection.renderer.xr = {isPresenting:true,getCamera:()=>({cameras:[eye]})};
  await inspection.capture(); assert.deepEqual(cameras[0].position.toArray(), [1,2,3]);
  assert.deepEqual(eye.position.toArray(), [1,2,3]);
});

test('isolation keeps children renderable and restores renderer state even after a GPU failure', () => {
  const {inspection,world,camera,scene} = fixture();
  const parent=world.entries.get('sword').node, child=parent.clone();parent.add(child);
  const state={target:null,viewport:[1,2,640,480],scissor:[3,4,400,300],scissorTest:true};
  const renderer={xr:{enabled:true},autoClear:false,
    getRenderTarget:()=>state.target,getViewport:v=>v.fromArray(state.viewport),getScissor:v=>v.fromArray(state.scissor),getScissorTest:()=>state.scissorTest,
    setRenderTarget:v=>state.target=v,setViewport:(...v)=>state.viewport=v.length===1?v[0].toArray():v,setScissor:v=>state.scissor=v.toArray(),setScissorTest:v=>state.scissorTest=v,
    render:()=>{assert.equal(parent.layers.mask,0);assert.equal(child.layers.mask,1);assert.equal(parent.visible,true);throw Error('GPU failure');}};
  inspection.renderer=renderer;delete inspection.render;
  const before=structuredClone(state),background=scene.background;
  assert.throws(()=>inspection.render(camera,m.inspectionOptions({target:'sword',isolate:true}),[child]),/GPU failure/);
  assert.deepEqual(state,before);assert.equal(parent.layers.mask,1);assert.equal(child.layers.mask,1);
  assert.equal(renderer.xr.enabled,true);assert.equal(renderer.autoClear,false);assert.equal(scene.background,background);
});

test('direct binding awaits images then makes exactly one follow-up, with options preserved', async () => {
  const binding = new OpenAIBinding({baseUrl:'https://example.invalid',apiKey:'test',model:'test'});
  const calls = [], actions = [];
  binding.callApi = async messages => {
    calls.push(structuredClone(messages));
    return calls.length === 1 ? {choices:[{message:{tool_calls:[{id:'view',function:{name:'request_capture',arguments:JSON.stringify({options:{target:'sword',style:'studio'}})}}]}}]} : {choices:[{message:{content:'I can see the fuller.'}}]};
  };
  binding.onProactiveAction(items => {
    actions.push(...items);
    queueMicrotask(() => binding.handleEvent({type:'event:action_completed',completedActionId:items[0].actionId,status:'completed',result:{images:[{url:image,label:'Sword'}]}}));
  });
  const result = await binding.handleEvent({type:'event:user_speech',text:'Inspect',isFinal:true,sessionId:'test'});
  assert.equal(calls.length, 2); assert.equal(actions.length, 1); assert.equal(actions[0].options.style, 'studio');
  assert.equal(calls[1].at(-1).content.at(-1).image_url.url, image);
  assert.equal(result[0].type, 'action:speak'); assert.ok(!JSON.stringify(binding.history).includes(image));
  await binding.stop();
});

test('direct binding does not retain manually shared image bytes for later turns', async () => {
  const binding = new OpenAIBinding({baseUrl:'https://example.invalid',apiKey:'test'}); const calls=[];
  binding.callApi = async messages => { calls.push(structuredClone(messages)); return {choices:[{message:{content:'Seen'}}]}; };
  await binding.handleEvent({type:'event:camera_frame',image,sessionId:'test'});
  await binding.handleEvent({type:'event:user_speech',text:'Next',isFinal:true,sessionId:'test'});
  assert.ok(JSON.stringify(calls[0]).includes(image)); assert.ok(!JSON.stringify(calls[1]).includes(image));
  await binding.stop();
});
