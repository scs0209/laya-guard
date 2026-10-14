// What an AI scenario adds to the scene: a virtual repository (one block per file, kept in step with the run's files), the
// AI agent (a small figure that walks between its terminal and the repository), and a barrier on the way to the execution
// zone that drops when a command is blocked. It only draws what the run's events say happened; nothing here decides anything.
//
// Colours: files are neutral blues and greys, never the verdict colours. A file the agent changed or created glows in the
// accent colour; a file laya-guard flagged (a package.json script it matched) is outlined red, because that is a laya-guard
// finding and nothing else in the scene is red for any other reason.

import * as THREE from "../vendor/three.min.js";
import { PALETTE, REPO, AGENT_SPOTS, LAYOUT } from "./layout.js";
import { glow } from "./world.js";

var TYPE_COLOR = { json: 0x3f7388, js: 0x5f7488, ts: 0x5f7488, md: 0x7b8791, yml: 0x4f6b78, yaml: 0x4f6b78 };

function colorOf(path) { return TYPE_COLOR[path.split(".").pop().toLowerCase()] || 0x566673; }
function mat(color, o) { return new THREE.MeshStandardMaterial(Object.assign({ color: color, roughness: 0.78, metalness: 0.08, flatShading: true }, o || {})); }

export function createAiWorld(world) {
  var group = new THREE.Group();
  group.visible = false;
  world.scene.add(group);
  var files = new Map(), tweens = [], visible = false, flagged = Object.create(null);
  var cx = REPO.center[0], cz = REPO.center[1];

  // ---- the repository pad ---------------------------------------------------------------------------------------------
  var pad = new THREE.Mesh(new THREE.BoxGeometry(6.4, 0.3, 5.4), mat(PALETTE.dark));
  pad.position.set(cx, 0.15, cz);
  var plate = new THREE.Mesh(new THREE.BoxGeometry(6, 0.1, 5), mat(0x13222c, { emissive: PALETTE.cyan, emissiveIntensity: 0.05 }));
  plate.position.set(cx, 0.34, cz);
  var edge = new THREE.LineSegments(new THREE.EdgesGeometry(pad.geometry), new THREE.LineBasicMaterial({ color: PALETTE.cyan, transparent: true, opacity: 0.45 }));
  pad.add(edge);
  group.add(pad, plate);
  var padGlow = glow(PALETTE.cyan, 8, 0.1); padGlow.position.set(cx, 0.8, cz); group.add(padGlow);
  pad.userData.pick = { kind: "station", id: "repo" }; plate.userData.pick = { kind: "station", id: "repo" };
  world.pickables.push(pad, plate);
  world.stations.repo = { id: "repo", group: group, hidden: true, focus: new THREE.Vector3(cx, 0.8, cz), zoom: 2.1,
                          label: { id: "repo", text: "Virtual repository", sub: "simulated · in memory", pos: new THREE.Vector3(cx, 3.2, cz + 2.9) } };

  // ---- the agent --------------------------------------------------------------------------------------------------------
  var agent = new THREE.Group();
  var body = new THREE.Mesh(new THREE.CylinderGeometry(0.34, 0.46, 1.2, 8), mat(0x9fb4c0, { emissive: PALETTE.cyan, emissiveIntensity: 0.08 }));
  body.position.y = 0.75;
  var head = new THREE.Mesh(new THREE.IcosahedronGeometry(0.4, 0), mat(0xc3d3db, { emissive: PALETTE.cyan, emissiveIntensity: 0.1 }));
  head.position.y = 1.7;
  var visor = new THREE.Mesh(new THREE.BoxGeometry(0.5, 0.14, 0.2), mat(0x06222b, { emissive: PALETTE.cyan, emissiveIntensity: 0.9 }));
  visor.position.set(0.3, 1.72, 0);
  var aglow = glow(PALETTE.cyan, 3.2, 0.3); aglow.position.y = 1.2;
  agent.add(body, head, visor, aglow);
  agent.position.set(AGENT_SPOTS.terminal[0], 0.3, AGENT_SPOTS.terminal[1]);
  group.add(agent);
  [body, head, visor].forEach(function (m) { m.userData.pick = { kind: "agent", id: "agent" }; world.pickables.push(m); });
  var agentLabel = { id: "agent", text: "AI agent", sub: "role-played by the AI", pos: new THREE.Vector3() };
  world.stations.agent = { id: "agent", group: agent, hidden: true, focus: new THREE.Vector3(AGENT_SPOTS.terminal[0], 1, AGENT_SPOTS.terminal[1]), zoom: 2.4, label: agentLabel };

  // ---- the barrier on the way to the execution zone -----------------------------------------------------------------------
  var inPort = LAYOUT.execution["in"];
  var barrier = new THREE.Mesh(new THREE.BoxGeometry(0.3, 2.6, 3.6), new THREE.MeshStandardMaterial({ color: PALETTE.bad, emissive: PALETTE.bad, emissiveIntensity: 0.9, transparent: true, opacity: 0.55, roughness: 0.5 }));
  barrier.position.set(inPort[0] - 0.4, 1.3, inPort[1]);
  barrier.scale.y = 0.001;
  barrier.visible = false;
  group.add(barrier);

  // ---- animation ------------------------------------------------------------------------------------------------------------
  function tween(ms, fn) {
    return new Promise(function (resolve) {
      if (!visible || ms <= 0 || world.reducedMotion) { fn(1); resolve(); return; }
      tweens.push({ t: 0, ms: ms, fn: fn, resolve: resolve });
    });
  }
  function ease(p) { return p < 0.5 ? 2 * p * p : 1 - Math.pow(-2 * p + 2, 2) / 2; }

  function slot(i) {
    return { x: cx - ((REPO.cols - 1) * REPO.gap) / 2 + (i % REPO.cols) * REPO.gap, z: cz - 1.2 + Math.floor(i / REPO.cols) * REPO.gap };
  }

  function addFile(path, index, grow) {
    var s = slot(index), color = colorOf(path);
    var mesh = new THREE.Mesh(new THREE.BoxGeometry(0.9, 0.55, 0.9), mat(color, { emissive: PALETTE.cyan, emissiveIntensity: 0 }));
    mesh.position.set(s.x, 0.65, s.z);
    var outline = new THREE.LineSegments(new THREE.EdgesGeometry(mesh.geometry), new THREE.LineBasicMaterial({ color: PALETTE.cyan, transparent: true, opacity: 0.25 }));
    mesh.add(outline);
    var halo = glow(PALETTE.cyan, 2.4, 0); halo.position.y = 0.2; mesh.add(halo);
    mesh.userData.pick = { kind: "file", id: path };
    group.add(mesh);
    world.pickables.push(mesh);
    var entry = { path: path, mesh: mesh, outline: outline, halo: halo, base: color, index: index };
    files.set(path, entry);
    if (grow) { mesh.scale.y = 0.001; }
    return entry;
  }
  function flash(entry, color, ms) {
    entry.halo.material.color.setHex(color); entry.mesh.material.emissive.setHex(color);
    return tween(ms, function (p) {
      var k = 1 - ease(p);
      entry.halo.material.opacity = 0.75 * k; entry.mesh.material.emissiveIntensity = 0.7 * k;
    });
  }

  var api = {
    group: group,
    setVisible: function (v) {
      visible = !!v; group.visible = visible;
      world.stations.repo.hidden = !visible; world.stations.agent.hidden = !visible;
      if (!visible) { tweens.forEach(function (t) { t.fn(1); t.resolve(); }); tweens = []; }
    },
    get visible() { return visible; },
    /** Remove everything a run added (the repository's files, the barrier) and put the agent back at its terminal. */
    reset: function () {
      tweens.forEach(function (t) { t.fn(1); t.resolve(); }); tweens = [];
      files.forEach(function (f) {
        group.remove(f.mesh); f.mesh.geometry.dispose(); f.mesh.material.dispose(); f.outline.geometry.dispose(); f.outline.material.dispose(); f.halo.material.dispose();
        var i = world.pickables.indexOf(f.mesh); if (i >= 0) world.pickables.splice(i, 1);
      });
      files.clear(); flagged = {};
      barrier.visible = false; barrier.scale.y = 0.001;
      agent.position.set(AGENT_SPOTS.terminal[0], 0.3, AGENT_SPOTS.terminal[1]);
    },
    /** The files appear one after another. Resolves when the last one has landed. */
    setFiles: function (map) {
      var paths = Object.keys(map), chain = Promise.resolve();
      paths.forEach(function (p, i) {
        if (files.has(p)) return;
        var entry = addFile(p, files.size, true);
        chain = chain.then(function () { return tween(220, function (k) { entry.mesh.scale.y = Math.max(0.001, ease(k)); }); });
      });
      return chain;
    },
    has: function (path) { return files.has(path); },
    count: function () { return files.size; },
    paths: function () { return Array.from(files.keys()); },
    positionOf: function (path) { var f = files.get(path); return f ? f.mesh.position.clone() : null; },
    /** kind: "read" (a cyan pulse), "write" (a change), "create" (a new block grows). */
    touch: function (path, kind) {
      var entry = files.get(path);
      if (!entry && kind !== "create") return Promise.resolve();
      if (!entry) {
        entry = addFile(path, files.size, true);
        return tween(300, function (k) { entry.mesh.scale.y = Math.max(0.001, ease(k)); }).then(function () { return flash(entry, PALETTE.cyan, 700); });
      }
      if (kind === "write") {
        return tween(280, function (k) { entry.mesh.position.y = 0.65 + 0.35 * Math.sin(Math.PI * k); }).then(function () { return flash(entry, PALETTE.cyan, 700); });
      }
      return flash(entry, PALETTE.glass, 450);
    },
    /** Pulse every file (a directory listing). */
    pulseAll: function () { return Promise.all(Array.from(files.values()).map(function (f) { return flash(f, PALETTE.glass, 450); })); },
    /** A laya-guard finding in this file: outlined red until it is rewritten. */
    flag: function (path, on) {
      var f = files.get(path);
      if (!f) return;
      flagged[path] = !!on;
      f.outline.material.color.setHex(on ? PALETTE.bad : PALETTE.cyan); f.outline.material.opacity = on ? 1 : 0.25;
      f.mesh.material.color.setHex(on ? 0x6b3a3f : f.base);
    },
    moveAgent: function (spot) {
      var to = AGENT_SPOTS[spot] || AGENT_SPOTS.terminal, from = { x: agent.position.x, z: agent.position.z };
      if (Math.abs(from.x - to[0]) < 0.05 && Math.abs(from.z - to[1]) < 0.05) return Promise.resolve();
      agent.rotation.y = Math.atan2(-(to[1] - from.z), to[0] - from.x);
      return tween(700, function (k) {
        var e = ease(k);
        agent.position.x = from.x + (to[0] - from.x) * e; agent.position.z = from.z + (to[1] - from.z) * e;
        agent.position.y = 0.3 + 0.25 * Math.abs(Math.sin(Math.PI * k * 3)) * (k < 1 ? 1 : 0);
      });
    },
    /** Words over the agent's head (the AI's own message). */
    say: function (title, sub) {
      agentLabel.text = title; agentLabel.sub = sub || "";
    },
    /** The barrier drops across the way to the execution zone, then lifts again. */
    block: function () {
      barrier.visible = true;
      return tween(350, function (k) { barrier.scale.y = Math.max(0.001, ease(k)); })
        .then(function () { return new Promise(function (r) { setTimeout(r, world.reducedMotion ? 0 : 900); }); })
        .then(function () { return tween(350, function (k) { barrier.scale.y = Math.max(0.001, 1 - ease(k)); }); })
        .then(function () { barrier.visible = false; });
    },
    update: function (dtMs) {
      agentLabel.pos.set(agent.position.x, agent.position.y + 2.7, agent.position.z);
      if (!tweens.length || dtMs <= 0) return;
      var live = [];
      tweens.forEach(function (t) {
        t.t += dtMs;
        var k = Math.min(1, t.t / t.ms);
        t.fn(k);
        if (k >= 1) t.resolve(); else live.push(t);
      });
      tweens = live;
    },
  };
  world.extensions.push(api);
  return api;
}
