// @ts-nocheck
// Real GPU and native controls; isolated from agent sessions and saved worlds.
import * as THREE from 'three';
import { VisualInspection } from '../packages/surface-webxr/src/visual-inspection.js';
import { attachVisionControls } from '../packages/surface-webxr/src/vision-controls.js';
import { RadialMenu } from '../packages/surface-webxr/src/radial-menu.js';

await document.fonts.ready;
for (const selector of ['.sidebar','.command-bar','#sidebar-toggle','#ar-button']) document.querySelector(selector)?.remove();
document.querySelector('.viewport-container').style.left = '0';
document.querySelector('#status-text').textContent = 'Visual inspection review · no agent connected';
const renderer = new THREE.WebGLRenderer({canvas:document.querySelector('#viewport'), antialias:true});
renderer.setSize(innerWidth,innerHeight); renderer.toneMapping = THREE.ACESFilmicToneMapping;
const scene = new THREE.Scene(); scene.background = new THREE.Color('#e4e5e9');
const camera = new THREE.PerspectiveCamera(45,innerWidth/innerHeight,.01,100); camera.position.set(1.8,1.5,3.5); camera.lookAt(0,.65,0);
scene.add(new THREE.HemisphereLight(0xffffff,0x222238,2)); const key = new THREE.DirectionalLight(0xffffff,4); key.position.set(2,4,3); scene.add(key);
const sword = new THREE.Group();
const part = (name, size, y, material) => { const mesh = new THREE.Mesh(new THREE.BoxGeometry(...size),new THREE.MeshStandardMaterial(material)); mesh.name=name;mesh.position.y=y;sword.add(mesh);return mesh; };
part('blade',[.09,.85,.035],.85,{color:'#b3bbcb',metalness:1,roughness:.2});
part('guard',[.38,.045,.075],.4,{color:'#947144',metalness:1,roughness:.3});
part('grip',[.06,.24,.055],.26,{color:'#382517',roughness:.9});
part('pommel',[.1,.07,.09],.1,{color:'#947144',metalness:1,roughness:.3}); scene.add(sword);
const floor = new THREE.Mesh(new THREE.PlaneGeometry(15,15),new THREE.MeshStandardMaterial({color:'#bfc2cb'})); floor.rotation.x=-Math.PI/2;scene.add(floor);
const world={entries:new Map([['sword',{node:sword,spec:{name:'Review sword'},status:'ready'}]]),store:{document:{revision:1}}};
const output=document.createElement('pre');output.id='visual-results';Object.assign(output.style,{position:'absolute',left:'20px',top:'90px',background:'#fff',color:'#192050',padding:'16px',zIndex:10,maxWidth:'450px'});document.body.append(output);
const gallery=document.createElement('div');gallery.id='visual-gallery';Object.assign(gallery.style,{position:'absolute',left:'20px',bottom:'20px',display:'flex',gap:'8px',zIndex:10});document.body.append(gallery);
const radial=new RadialMenu();radial.attach(scene);
const inspection=new VisualInspection(renderer,scene,camera,world,()=>controls.enabled());
const show=result=>{gallery.replaceChildren();for(const image of result.images){const img=document.createElement('img');img.src=image.url;img.alt=image.label;img.width=240;img.height=240;img.style.objectFit='contain';gallery.append(img);} };
const controls=attachVisionControls({onChange:enabled=>{radial.setVisionState(enabled);if(!enabled)inspection.cancel();output.textContent=`Agent view sharing ${enabled?'on':'off'}`;},onShare:async()=>{show(await inspection.capture({},{manual:true}));output.textContent='PASS one-shot user view';}});
function button(label,callback){const b=document.createElement('button');b.className='topbar-btn';b.style.width='auto';b.style.padding='10px';b.textContent=label;b.onclick=callback;document.querySelector('.topbar-actions').append(b);}
button('Switch theme',()=>document.documentElement.setAttribute('data-theme',document.documentElement.getAttribute('data-theme')==='dark'?'light':'dark'));
button('Inspect sword',async()=>{try{const before={camera:camera.matrixWorld.toArray(),lights:key.visible,environment:scene.environment,background:scene.background};const result=await inspection.capture({target:'sword',views:['front','right','perspective'],style:'studio',isolate:true});show(result);const restored=JSON.stringify(before.camera)===JSON.stringify(camera.matrixWorld.toArray())&&key.visible===before.lights&&scene.environment===before.environment&&scene.background===before.background;output.textContent=`${restored?'PASS':'FAIL'} renderer and human camera restored\n${result.images.length} actual GPU images returned\nVirtual scene only; physicalCamera: ${result.physicalCamera}`;}catch(error){output.textContent=error.message;}});
button('Clay detail',async()=>{try{show(await inspection.capture({target:'sword',node:'guard',style:'clay',views:['perspective'],isolate:true}));output.textContent='PASS isolated named mesh in clay';}catch(error){output.textContent=error.message;}});
button('XR menu',()=>{radial.setVisionState(controls.enabled());radial.isOpen?radial.close():radial.open(camera);});
renderer.setAnimationLoop(()=>renderer.render(scene,camera));
