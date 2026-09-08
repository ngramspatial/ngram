/** A reference creation built entirely with the same API available to agents. */
export function kineticWorkshop(origin: number[] = [0, 0, -1.5]) {
  const pos = (x, y, z) => [origin[0] + x, origin[1] + y, origin[2] + z];
  const shape = (id, shape, size, position, color, physics?, extra = {}) => ({
    op: "entity.create",
    entity: {
      id: `kinetic.${id}`,
      name: id.replaceAll("-", " "),
      tags: ["kinetic-workshop"],
      kind: "shape",
      geometry: { shape, size },
      transform: { position },
      material: { color, metalness: 0.6, roughness: 0.2 },
      ...(physics ? { physics } : {}),
      ...extra,
    },
  });
  const fixed = { mode: "fixed" },
    dynamic = { mode: "dynamic", damping: 0.12, restitution: 0.65 };
  const operations = [
    shape("plinth", "box", [1.9, 0.1, 1.1], pos(0, 0.15, 0), "#E7E9FF", fixed, {
      grabbable: false,
    }),
    shape(
      "left-tower",
      "box",
      [0.075, 1.4, 0.075],
      pos(-0.65, 0.9, 0),
      "#6E7DFF",
      fixed,
      { grabbable: false },
    ),
    shape(
      "right-tower",
      "box",
      [0.075, 1.4, 0.075],
      pos(0.65, 0.9, 0),
      "#6E7DFF",
      fixed,
      { grabbable: false },
    ),
    shape(
      "crossbeam",
      "box",
      [1.4, 0.06, 0.06],
      pos(0, 1.6, 0),
      "#6E7DFF",
      fixed,
      { grabbable: false },
    ),
    shape("rotor", "sphere", [0.5, 0.5, 0.06], pos(0, 1.15, 0), "#6E7DFF", {
      ...dynamic,
      gravity: [0, 0, 0],
    }),
    shape(
      "rotor-marker",
      "sphere",
      [0.07, 0.07, 0.07],
      pos(0.2, 1.15, 0.06),
      "#FFFFFF",
      undefined,
      {
        material: { color: "#FFFFFF", emissive: "#6E7DFF", glow: 2 },
        grabbable: false,
      },
    ),
    shape(
      "pendulum",
      "sphere",
      [0.23, 0.23, 0.23],
      pos(-0.5, 0.75, 0.18),
      "#F0D3FF",
      { ...dynamic, mass: 1.2 },
    ),
    shape(
      "spring-ball",
      "sphere",
      [0.2, 0.2, 0.2],
      pos(0.55, 0.7, 0.18),
      "#BDCBFF",
      dynamic,
    ),
    shape(
      "pendulum-thread",
      "cylinder",
      [0.008, 1, 0.008],
      pos(-0.5, 1.2, 0.18),
      "#CACFFF",
      undefined,
      { grabbable: false },
    ),
    shape(
      "spring-thread",
      "cylinder",
      [0.012, 1, 0.012],
      pos(0.55, 1.2, 0.18),
      "#CACFFF",
      undefined,
      { grabbable: false },
    ),
    {
      op: "joint.create",
      joint: {
        id: "kinetic.motor",
        type: "hinge",
        a: "kinetic.crossbeam",
        b: "kinetic.rotor",
        anchorA: [0, -0.45, 0],
        anchorB: [0, 0, 0],
        axis: [0, 0, 1],
        velocity: 1.8,
        strength: 6,
      },
    },
    {
      op: "joint.create",
      joint: {
        id: "kinetic.pendulum-joint",
        type: "rope",
        a: "kinetic.crossbeam",
        b: "kinetic.pendulum",
        anchorA: [-0.5, 0, 0.18],
        anchorB: [0, 0, 0],
        length: 0.85,
      },
    },
    {
      op: "joint.create",
      joint: {
        id: "kinetic.spring-joint",
        type: "spring",
        a: "kinetic.crossbeam",
        b: "kinetic.spring-ball",
        anchorA: [0.55, 0, 0.18],
        anchorB: [0, 0, 0],
        length: 0.7,
        stiffness: 65,
        damping: 1.4,
      },
    },
    ...[
      ["speed", "slider", "Motor speed", 1.8, -5, 5, -0.6],
      ["kick", "button", "Give it a push", 0, 0, 1, 0],
      ["reverse", "button", "Reverse motor", 0, 0, 1, 0.6],
    ].map(([id, type, label, value, min, max, x]) => ({
      op: "entity.create",
      entity: {
        id: `kinetic.${id}`,
        kind: "control",
        tags: ["kinetic-workshop"],
        name: label,
        transform: { position: pos(x, 0.38, 0.62) },
        geometry: { size: [0.52, 0.17, 0.02] },
        control: { type, label, value, min, max, step: 0.1 },
      },
    })),
  ];
  const source = `
return {
 event(e) {
  if(e.type!=="control")return;
  if(e.target==="kinetic.speed")api.state.speed=e.data.value;
  if(e.target==="kinetic.reverse")api.state.speed=-(api.state.speed??1.8);
  if(e.target==="kinetic.kick")api.emit([{op:"body.impulse",id:"kinetic.pendulum",impulse:[1.6,.5,.4]},{op:"body.impulse",id:"kinetic.spring-ball",impulse:[-.6,.9,.4]}]);
  api.emit([{op:"joint.motor",id:"kinetic.motor",velocity:api.state.speed??1.8,strength:6}]);
 },
 tick() {
  const rotor=api.get("kinetic.rotor"),marker=api.get("kinetic.rotor-marker");
  if(rotor&&marker){const a=rotor.transform.rotation[2];const p=rotor.transform.position;api.emit([{op:"entity.patch",id:marker.id,patch:{transform:{position:[p[0]+Math.cos(a)*.2,p[1]+Math.sin(a)*.2,p[2]+.06]}}}]);}
  const beam=api.get("kinetic.crossbeam");
  for(const [ballId,threadId,offset] of [["kinetic.pendulum","kinetic.pendulum-thread",-.5],["kinetic.spring-ball","kinetic.spring-thread",.55]]){
   const ball=api.get(ballId);if(!beam||!ball)continue;
   const a=beam.transform.position.map((n,i)=>n+(i===0?offset:i===2?.18:0)),b=ball.transform.position;
   const d=a.map((n,i)=>n-b[i]),l=Math.max(.01,Math.hypot(...d));
   api.emit([{op:"entity.patch",id:threadId,patch:{transform:{position:a.map((n,i)=>(n+b[i])/2),rotation:[Math.atan2(d[2],d[1]),0,Math.asin(-d[0]/l)],scale:[1,l,1]}}}]);
  }
 }
};`;
  return {
    operations,
    program: {
      id: "kinetic.controller",
      name: "Kinetic workshop",
      source,
      entityIds: [
        "kinetic.rotor",
        "kinetic.rotor-marker",
        "kinetic.crossbeam",
        "kinetic.pendulum",
        "kinetic.spring-ball",
        "kinetic.pendulum-thread",
        "kinetic.spring-thread",
        "kinetic.speed",
        "kinetic.kick",
        "kinetic.reverse",
      ],
      state: { speed: 1.8 },
      params: {},
      hz: 20,
    },
  };
}
