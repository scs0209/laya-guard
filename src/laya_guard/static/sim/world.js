// The miniature: every station of the pipeline built from primitives (no models, no textures from outside).
// It only builds and lights things. What happens in it is decided elsewhere (packets.js draws the calls; index.js
// decides what is true).

import * as THREE from "../vendor/three.min.js";
import { PALETTE, LAYOUT, GROUND, RAILS, portsOf } from "./layout.js";

function mat(color, o) {
  return new THREE.MeshStandardMaterial(Object.assign({ color: color, roughness: 0.78, metalness: 0.08, flatShading: true }, o || {}));
}
function box(w, h, d, color, o) { return new THREE.Mesh(new THREE.BoxGeometry(w, h, d), mat(color, o)); }
function at(obj, x, y, z) { obj.position.set(x, y, z); return obj; }

var glowTexture = null;
function glowMap() {
  if (glowTexture) return glowTexture;
  var c = document.createElement("canvas");
  c.width = c.height = 128;
  var g = c.getContext("2d"), grad = g.createRadialGradient(64, 64, 0, 64, 64, 64);
  grad.addColorStop(0, "rgba(255,255,255,1)"); grad.addColorStop(0.35, "rgba(255,255,255,0.35)"); grad.addColorStop(1, "rgba(255,255,255,0)");
  g.fillStyle = grad; g.fillRect(0, 0, 128, 128);
  glowTexture = new THREE.CanvasTexture(c);
  glowTexture.colorSpace = THREE.SRGBColorSpace;
  return glowTexture;
}
/** A soft additive highlight: the scene's stand-in for bloom (no post-processing pass, so it stays cheap). */
export function glow(color, size, opacity) {
  var s = new THREE.Sprite(new THREE.SpriteMaterial({ map: glowMap(), color: color, transparent: true, opacity: opacity == null ? 0.5 : opacity,
                                                      blending: THREE.AdditiveBlending, depthWrite: false }));
  s.scale.set(size, size, 1);
  s.renderOrder = 5;
  return s;
}

function outline(mesh, color, opacity) {
  var e = new THREE.LineSegments(new THREE.EdgesGeometry(mesh.geometry), new THREE.LineBasicMaterial({ color: color, transparent: true, opacity: opacity == null ? 0.6 : opacity }));
  mesh.add(e);
  return e;
}

function screenTexture(kind) {
  var c = document.createElement("canvas");
  c.width = 128; c.height = 128;
  var g = c.getContext("2d");
  g.fillStyle = "#06222b"; g.fillRect(0, 0, 128, 128);
  if (kind === "scenario") {
    g.strokeStyle = "rgba(138,223,240,0.55)"; g.lineWidth = 6;
    for (var i = -128; i < 256; i += 22) { g.beginPath(); g.moveTo(i, 128); g.lineTo(i + 128, 0); g.stroke(); }
  } else {
    g.fillStyle = "rgba(34,211,238,0.85)";
    [[14, 20, 40], [14, 40, 78], [26, 60, 52], [26, 80, 30], [14, 100, 60]].forEach(function (r) { g.fillRect(r[0], r[1], r[2], 8); });
  }
  var t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

function gridTexture() {
  var c = document.createElement("canvas");
  c.width = c.height = 256;
  var g = c.getContext("2d");
  g.fillStyle = "#15212b"; g.fillRect(0, 0, 256, 256);
  g.strokeStyle = "rgba(120,160,185,0.14)"; g.lineWidth = 2;
  g.strokeRect(1, 1, 254, 254);
  var t = new THREE.CanvasTexture(c);
  t.wrapS = t.wrapT = THREE.RepeatWrapping;
  t.repeat.set((GROUND.x1 - GROUND.x0) / 2, (GROUND.z1 - GROUND.z0) / 2);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

/** Mark a whole subtree as something a click can select. */
function pickable(root, pick, list) {
  root.traverse(function (o) {
    if (!o.isMesh) return;
    if (!o.userData.pickable) { o.userData.pickable = true; list.push(o); }
    o.userData.pick = pick;                     // a later call wins: a column inside a gate selects the column
  });
}

export function createWorld(config) {
  var scene = new THREE.Scene();
  var pickables = [], stations = {}, lamps = {}, pillars = {}, queueSlots = [], zoneInfo = {};
  var animated = { rings: [], beacons: [], core: null, coreLight: null, domeGlow: null };

  // --- light ---
  scene.add(new THREE.HemisphereLight(0x9fd6ee, 0x0a1218, 1.1));
  var sun = new THREE.DirectionalLight(0xdbeeff, 2.1);
  sun.position.set(14, 24, 10);
  scene.add(sun);
  var rim = new THREE.DirectionalLight(0x22d3ee, 0.7);
  rim.position.set(-16, 10, -14);
  scene.add(rim);

  // --- the base: a slab with a lighter inset, like a diorama on a shelf ---
  var w = GROUND.x1 - GROUND.x0, d = GROUND.z1 - GROUND.z0, cx = (GROUND.x0 + GROUND.x1) / 2, cz = (GROUND.z0 + GROUND.z1) / 2;
  var slab = at(box(w + 1.6, 0.9, d + 1.6, PALETTE.base), cx, -0.55, cz);
  outline(slab, PALETTE.edge, 0.9);
  scene.add(slab);
  var top = new THREE.Mesh(new THREE.PlaneGeometry(w, d), new THREE.MeshStandardMaterial({ map: gridTexture(), roughness: 0.95, metalness: 0 }));
  top.rotation.x = -Math.PI / 2; top.position.set(cx, 0.01, cz);
  scene.add(top);

  function station(id, focus, zoom) {
    var g = new THREE.Group();
    scene.add(g);
    stations[id] = { id: id, group: g, focus: new THREE.Vector3(focus[0], focus[1] || 0, focus[2]), zoom: zoom || 1.6, label: null };
    return g;
  }
  function lamp(id, mesh, glowSprite, base) {
    (lamps[id] = lamps[id] || []).push({ mesh: mesh, glow: glowSprite, base: base });
  }
  function label(id, text, sub, x, y, z) {
    stations[id].label = { id: id, text: text, sub: sub, pos: new THREE.Vector3(x, y, z) };
  }

  // --- terminals: where a tool call starts ---
  LAYOUT.terminals.forEach(function (t, i) {
    var id = "terminal:" + i, g = station(id, [t.x, 1, t.z], 2.1);
    at(g, t.x, 0, t.z);
    g.add(at(box(2.6, 0.45, 2.4, PALETTE.dark), 0, 0.22, 0));
    g.add(at(box(0.3, 0.7, 0.3, PALETTE.steel), -0.4, 0.8, 0));
    var screen = box(0.16, 1.5, 2.1, 0x06222b, { emissive: t.kind === "scenario" ? 0x7aa2c8 : PALETTE.cyan, emissiveIntensity: 0.55, emissiveMap: screenTexture(t.kind), map: screenTexture(t.kind) });
    at(screen, -0.35, 1.7, 0);
    outline(screen, t.kind === "scenario" ? PALETTE.glass : PALETTE.cyan, 0.8);
    g.add(screen);
    g.add(at(box(1.1, 0.1, 1.7, PALETTE.steel), 0.7, 0.5, 0));
    var halo = glow(t.kind === "scenario" ? 0x7aa2c8 : PALETTE.cyan, 4.2, 0.28);
    at(halo, -0.2, 1.7, 0); g.add(halo);
    lamp(id, screen, halo, 0.55);
    pickable(g, { kind: "station", id: id }, pickables);
    label(id, t.name, t.kind === "scenario" ? "simulation" : "", t.x, [4.9, 3.4, 3.2][i], t.z);
  });

  // --- the command queue: a short belt with slots that show how many calls are waiting to be drawn ---
  (function () {
    var q = LAYOUT.queue, g = station("queue", [q.center[0], 0.5, q.center[1]], 1.9);
    at(g, q.center[0], 0, q.center[1]);
    g.add(at(box(6.8, 0.3, 2.8, PALETTE.dark), 0, 0.15, 0));
    var belt = box(6.4, 0.18, 1.7, 0x1c2b36);
    g.add(at(belt, 0, 0.38, 0));
    for (var i = 0; i < 6; i++) {
      var slot = box(0.7, 0.34, 1.1, 0x233644, { emissive: PALETTE.cyan, emissiveIntensity: 0 });
      at(slot, -2.5 + i, 0.6, 0);
      g.add(slot);
      queueSlots.push(slot);
    }
    [-3.4, 3.4].forEach(function (x) { g.add(at(box(0.18, 0.9, 2.8, PALETTE.steel), x, 0.45, 0)); });
    pickable(g, { kind: "station", id: "queue" }, pickables);
    label("queue", "Command queue", "", q.center[0], 1.3, q.center[1] + 1.2);
  })();

  // --- the policy gate: a scanner tunnel flanked by one column per policy ---
  (function () {
    var p = LAYOUT.policy, g = station("policy", [p.center[0], 1, p.center[1]], 1.55);
    at(g, p.center[0], 0, p.center[1]);
    g.add(at(box(7.4, 0.3, 8.2, PALETTE.dark), 0, 0.15, 0));
    [-2.1, 0, 2.1].forEach(function (x) {
      [-1.7, 1.7].forEach(function (z) { g.add(at(box(0.4, 3.4, 0.4, PALETTE.steel), x, 1.85, z)); });
      var lintel = box(0.5, 0.4, 4.2, 0x0e2a33, { emissive: PALETTE.cyan, emissiveIntensity: 0.25 });
      at(lintel, x, 3.65, 0); g.add(lintel);
      lamp("policy", lintel, null, 0.25);
    });
    var gl = glow(PALETTE.cyan, 7, 0.2); at(gl, 0, 3.3, 0); g.add(gl);
    lamp("policy", null, gl, 0.2);
    var list = config.policies, perSide = Math.ceil(list.length / 2);
    list.forEach(function (pol, i) {
      var side = i < perSide ? -1 : 1, n = i < perSide ? i : i - perSide;
      var col = new THREE.Group();
      var color = pol.on_match === "block" ? PALETTE.bad : PALETTE.warn;
      var shaft = new THREE.Mesh(new THREE.CylinderGeometry(0.36, 0.4, 1.3, 8), mat(0x1b2a35));
      shaft.position.y = 0.65;
      var cap = new THREE.Mesh(new THREE.CylinderGeometry(0.4, 0.36, 0.28, 8), mat(color, { emissive: color, emissiveIntensity: 0.12 }));
      cap.position.y = 1.44;
      var g2 = glow(color, 2.4, 0);
      g2.position.y = 1.6;
      col.add(shaft, cap, g2);
      col.position.set(-2.5 + n * 1.0 - (perSide - 1) * 0.5 + 0.4, 0, side * 3.4);
      g.add(col);
      pillars[pol.id] = { cap: cap, glow: g2, color: color, base: 0.12, group: col };
    });
    pickable(g, { kind: "station", id: "policy" }, pickables);
    Object.keys(pillars).forEach(function (id) { pickable(pillars[id].group, { kind: "policy", id: id }, pickables); });
    label("policy", "Policy gate", "rules, no model", p.center[0], 5.9, p.center[1]);
  })();

  // --- the engine: the local model, off to the side of the main line ---
  (function () {
    var e = LAYOUT.engine, g = station("engine", [e.center[0], 1.4, e.center[1]], 1.75);
    at(g, e.center[0], 0, e.center[1]);
    g.add(at(new THREE.Mesh(new THREE.CylinderGeometry(2.9, 3.2, 0.6, 6), mat(PALETTE.dark)), 0, 0.3, 0));
    g.add(at(new THREE.Mesh(new THREE.CylinderGeometry(2.1, 2.4, 0.3, 6), mat(0x16303a, { emissive: PALETTE.cyan, emissiveIntensity: 0.08 })), 0, 0.72, 0));
    var dome = new THREE.Mesh(new THREE.OctahedronGeometry(1.7, 0), new THREE.MeshStandardMaterial({ color: PALETTE.glass, transparent: true, opacity: 0.16, roughness: 0.2, metalness: 0.1, flatShading: true, depthWrite: false }));
    dome.position.y = 2.6;
    outline(dome, PALETTE.cyan, 0.5);
    g.add(dome);
    var core = new THREE.Mesh(new THREE.IcosahedronGeometry(0.85, 0), mat(0x0d3a46, { emissive: PALETTE.cyan, emissiveIntensity: 0.25 }));
    core.position.y = 2.6;
    g.add(core);
    animated.core = core;
    [[2.2, 0], [1.8, Math.PI / 2.6]].forEach(function (r, i) {
      var ring = new THREE.Mesh(new THREE.TorusGeometry(r[0], 0.06, 6, 40), mat(PALETTE.cyan, { emissive: PALETTE.cyan, emissiveIntensity: 0.4 }));
      ring.position.y = 2.6;
      ring.rotation.x = Math.PI / 2 + r[1] * (i ? 1 : 0);
      g.add(ring);
      animated.rings.push({ mesh: ring, speed: i ? -0.7 : 0.5 });
    });
    var gl = glow(PALETTE.cyan, 6.5, 0.22); at(gl, 0, 2.6, 0); g.add(gl);
    lamp("engine", core, gl, 0.25);
    pickable(g, { kind: "station", id: "engine" }, pickables);
    label("engine", "Laya engine", "local model", e.center[0], 5.4, e.center[1]);
  })();

  // --- the decision gate: a gantry with one beacon per verdict ---
  (function () {
    var c = LAYOUT.decision, g = station("decision", [c.center[0], 1.2, c.center[1]], 1.7);
    at(g, c.center[0], 0, c.center[1]);
    g.add(at(box(4.4, 0.3, 9, PALETTE.dark), 0, 0.15, 0));
    [-4, 4].forEach(function (z) { g.add(at(box(0.5, 4.2, 0.5, PALETTE.steel), 0, 2.2, z)); });
    g.add(at(box(0.6, 0.5, 8.6, 0x1b2a35), 0, 4.3, 0));
    [["allow", PALETTE.ok, -2.4], ["review", PALETTE.warn, 0], ["block", PALETTE.bad, 2.4]].forEach(function (b) {
      var bulb = new THREE.Mesh(new THREE.CylinderGeometry(0.5, 0.5, 0.5, 10), mat(b[1], { emissive: b[1], emissiveIntensity: 0.12 }));
      bulb.position.set(0.1, 4.8, b[2]);
      var gl = glow(b[1], 4, 0);
      gl.position.set(0.1, 4.8, b[2]);
      g.add(bulb, gl);
      lamp("decision:" + b[0], bulb, gl, 0.12);
    });
    pickable(g, { kind: "station", id: "decision" }, pickables);
    label("decision", "Decision gate", "allow · review · block", c.center[0], 6.2, c.center[1]);
  })();

  // --- zones: where a verdict ends up ---
  function zone(id, color, title, sub, build) {
    var c = LAYOUT[id], g = station(id, [c.center[0], 0.8, c.center[1]], 1.8);
    at(g, c.center[0], 0, c.center[1]);
    g.add(at(box(6, 0.28, 6, PALETTE.dark), 0, 0.14, 0));
    g.add(at(box(5.6, 0.1, 5.6, color, { emissive: color, emissiveIntensity: 0.05, roughness: 0.9 }), 0, 0.33, 0));
    var edges = box(6, 1.7, 6, color, { transparent: true, opacity: 0.05, depthWrite: false });
    edges.position.y = 1.2;
    outline(edges, color, 0.55);
    g.add(edges);
    var gl = glow(color, 8, 0.1); at(gl, 0, 0.8, 0); g.add(gl);
    lamp(id, null, gl, 0.1);
    if (build) build(g);
    pickable(g, { kind: "station", id: id }, pickables);
    label(id, title, sub, c.center[0], 3, c.center[1]);
    zoneInfo[id] = { color: color };
  }
  zone("execution", 0x0f3a2b, "Execution zone", "agent runs the call", function (g) {
    [-2.6, 2.6].forEach(function (x) { g.add(at(box(0.15, 1.3, 0.15, PALETTE.ok, { emissive: PALETTE.ok, emissiveIntensity: 0.4 }), x, 0.9, -2.6)); });
  });
  zone("review", 0x3d2f0d, "Review queue", "a person decides", function (g) {
    for (var i = 0; i < 3; i++) g.add(at(box(1.1, 0.5, 0.8, 0x4a3a12), -1.6 + i * 1.6, 0.6, 2.2));
  });
  zone("incident", 0x3f1616, "Incident & audit", "stopped and recorded", function (g) {
    [0, 1, 2].forEach(function (i) { g.add(at(box(1.6, 0.32, 1.1, i % 2 ? 0x5a2222 : 0x4a1b1b), 1.8, 0.62 + i * 0.34, 2.1)); });
    var b = new THREE.Mesh(new THREE.CylinderGeometry(0.3, 0.3, 0.5, 8), mat(PALETTE.bad, { emissive: PALETTE.bad, emissiveIntensity: 0.3 }));
    b.position.set(-2.3, 0.7, 2.3);
    g.add(b);
  });

  // --- the event stream: thin rails between the stations, drawn along the paths calls follow ---
  var rails = new THREE.Group();
  scene.add(rails);
  RAILS.forEach(function (r) {
    var a = portsOf(r[0], r[2]).out, b = portsOf(r[1], r[2]).in;
    var dx = b[0] - a[0], dz = b[1] - a[1], len = Math.sqrt(dx * dx + dz * dz);
    var rail = box(len, 0.08, 0.14, PALETTE.cyan, { emissive: PALETTE.cyan, emissiveIntensity: 0.5, transparent: true, opacity: 0.4 });
    rail.position.set((a[0] + b[0]) / 2, 0.12, (a[1] + b[1]) / 2);
    rail.rotation.y = -Math.atan2(dz, dx);
    rails.add(rail);
  });
  // the offline bypass: what a hook does when the server isn't there (it never reaches the queue), drawn dim
  LAYOUT.terminals.slice(0, 2).forEach(function (t, i) {
    var a = portsOf("terminal", i).out, b = portsOf("policy").in;
    var dx = b[0] - a[0], dz = b[1] - a[1], len = Math.sqrt(dx * dx + dz * dz);
    var rail = box(len, 0.05, 0.08, PALETTE.steel, { transparent: true, opacity: 0.22 });
    rail.position.set((a[0] + b[0]) / 2, 0.08, (a[1] + b[1]) / 2);
    rail.rotation.y = -Math.atan2(dz, dx);
    rails.add(rail);
  });

  // --- state: what each station looks like right now ---
  var pulses = {};
  var STATE_COLOR = { idle: PALETTE.cyan, active: PALETTE.cyan, allow: PALETTE.ok, review: PALETTE.warn, block: PALETTE.bad, fail: 0x8a96a0 };

  function setLamp(entry, color, level) {
    if (entry.mesh && entry.mesh.material && entry.mesh.material.emissive) {
      entry.mesh.material.emissive.setHex(color);
      entry.mesh.material.emissiveIntensity = level;
    }
    if (entry.glow) { entry.glow.material.color.setHex(color); entry.glow.material.opacity = Math.min(0.9, level * 0.9); }
  }

  var world = {
    scene: scene, pickables: pickables, stations: stations, animated: animated,
    stationIds: Object.keys(stations),
    /** Light a station up in a state's colour for a moment (the light decays on its own). */
    pulse: function (id, state, ms) { pulses[id] = { color: STATE_COLOR[state] || PALETTE.cyan, left: ms || 900, total: ms || 900 }; },
    lightPolicy: function (policyId, on) {
      var p = pillars[policyId];
      if (!p) return;
      p.glow.material.opacity = on ? 0.9 : 0;
      p.cap.material.emissiveIntensity = on ? 1.1 : p.base;
    },
    clearPolicies: function () { Object.keys(pillars).forEach(function (id) { world.lightPolicy(id, false); }); },
    lightBeacon: function (action, on) {
      (lamps["decision:" + action] || []).forEach(function (l) { setLamp(l, STATE_COLOR[action], on ? 1.2 : l.base); });
    },
    setQueueDepth: function (n) {
      queueSlots.forEach(function (s, i) { s.material.emissiveIntensity = i < n ? 0.9 : 0; });
    },
    /** "active": the model is running; "idle"; "offline": it isn't being used (mock mode or an error). */
    setEngineMode: function (mode) { world.engineMode = mode; },
    engineMode: "idle",
    focusOf: function (id) { return stations[id] || null; },
    labels: function () { return Object.keys(stations).map(function (k) { return stations[k].label; }).filter(Boolean); },
    update: function (dtMs, reduced) {
      var dt = dtMs / 1000;
      Object.keys(pulses).forEach(function (id) {
        var p = pulses[id];
        p.left -= dtMs;
        if (p.left <= 0) delete pulses[id];
      });
      Object.keys(lamps).forEach(function (id) {
        if (id.indexOf("decision:") === 0) return;
        var p = pulses[id];
        lamps[id].forEach(function (l) {
          if (p) setLamp(l, p.color, l.base + 1.1 * (p.left / p.total));
          else setLamp(l, id === "engine" && world.engineMode === "offline" ? STATE_COLOR.fail : PALETTE.cyan, l.base);
        });
      });
      var active = world.engineMode === "active";
      if (animated.core) {
        animated.core.material.emissiveIntensity = active ? 1.4 : world.engineMode === "offline" ? 0.05 : 0.25;
        if (!reduced && active) animated.core.rotation.y += dt * 2.2;      // it turns only while the model really runs
      }
      animated.rings.forEach(function (r) { if (!reduced && active) r.mesh.rotation.z += dt * r.speed * 3; });
    },
  };
  return world;
}
