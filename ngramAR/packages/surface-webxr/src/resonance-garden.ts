/** Procedural geometry, instancing and human input; all use public world tools. */
export function resonanceGarden(origin = [-2, 0, -1.5]) {
  const position = (x, y, z) => origin.map((n, i) => n + [x, y, z][i]);
  const operations = [
    {
      op: "entity.create",
      entity: {
        id: "garden.seed",
        name: "Attractor · grab me",
        tags: ["resonance-garden"],
        geometry: { shape: "sphere", size: [0.14, 0.14, 0.14] },
        transform: { position: position(0, 1.15, 0) },
        material: {
          color: "#FFFFFF",
          emissive: "#C2C9FF",
          glow: 2.8,
          metalness: 0.5,
          roughness: 0.05,
        },
      },
    },
    {
      op: "entity.create",
      entity: {
        id: "garden.halo",
        name: "Halo",
        tags: ["resonance-garden"],
        grabbable: false,
        geometry: { shape: "torus", size: [0.5, 0.5, 0.06] },
        transform: { position: position(0, 1.15, 0) },
        material: {
          color: "#6E7DFF",
          emissive: "#6E7DFF",
          glow: 1.1,
          metalness: 0.65,
          roughness: 0.1,
        },
      },
    },
    ...["#6E7DFF", "#D7AFFF", "#BFE6FF"].map((color, i) => ({
      op: "entity.create",
      entity: {
        id: `garden.stream${i}`,
        name: `Flow ${i + 1}`,
        tags: ["resonance-garden"],
        grabbable: false,
        geometry: {
          shape: "sphere",
          size: [0.022, 0.022, 0.022],
          instances: Array.from({ length: 180 }, (_, j) => [
            Math.cos(j * 0.17 + i * 2.1) * 0.7,
            0.2 + j / 150,
            Math.sin(j * 0.17 + i * 2.1) * 0.7,
          ]),
        },
        transform: { position: origin },
        material: {
          color,
          emissive: color,
          glow: 0.85,
          metalness: 0.3,
          roughness: 0.12,
        },
      },
    })),
    ...[
      ["energy", "slider", "Energy", 0.7, 0.1, 2, -0.6],
      ["bloom", "button", "Bloom", 0, 0, 1, 0],
      ["freeze", "toggle", "Freeze", 0, 0, 1, 0.6],
    ].map(([id, type, label, value, min, max, x]) => ({
      op: "entity.create",
      entity: {
        id: `garden.${id}`,
        kind: "control",
        name: label,
        tags: ["resonance-garden"],
        geometry: { size: [0.52, 0.17, 0.02] },
        transform: { position: position(x, 0.25, 0.85) },
        control: { type, label, value, min, max, step: 0.05 },
      },
    })),
  ];
  const source = `
return {
 event(e){if(e.type!=="control")return;if(e.target==="garden.energy")api.state.energy=e.data.value;if(e.target==="garden.bloom")api.state.bloom=1;if(e.target==="garden.freeze")api.state.frozen=e.data.value>0;},
 tick(){
  if(api.state.frozen)return;
  const seed=api.get("garden.seed");if(!seed)return;
  const center=seed.transform.position.map((n,i)=>n-api.params.origin[i]);
  const energy=api.state.energy??.7,t=api.time*energy;
  const bloom=Math.max(0,Math.min(1,api.state.bloom??0)-api.dt*1.6);api.state.bloom=bloom;
  for(let stream=0;stream<3;stream++){
   const points=[];
   for(let i=0;i<180;i++){
    const u=i/179,a=u*Math.PI*10+t*(stream%2?-1:1)+stream*2.1;
    const radius=(.12+Math.sin(u*Math.PI)*.8)*(1+bloom*1.3);
    points.push([center[0]+Math.cos(a)*radius,center[1]+(u-.5)*1.7+Math.sin(a*1.4+t)*.1,center[2]+Math.sin(a)*radius]);
   }
   api.emit([{op:"entity.patch",id:"garden.stream"+stream,patch:{geometry:{instances:points}}}]);
  }
  api.emit([{op:"entity.patch",id:"garden.halo",patch:{transform:{position:seed.transform.position,rotation:[t*.35,t*.6,0],scale:[1+bloom,1+bloom,1+bloom]}}}]);
 }
};`;
  return {
    operations,
    program: {
      id: "garden.flow",
      name: "Resonance garden",
      source,
      entityIds: [
        "garden.seed",
        "garden.halo",
        "garden.stream0",
        "garden.stream1",
        "garden.stream2",
        "garden.energy",
        "garden.bloom",
        "garden.freeze",
      ],
      params: { origin },
      state: { energy: 0.7, frozen: false },
      hz: 20,
    },
  };
}
