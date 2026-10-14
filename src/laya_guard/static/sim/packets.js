// The calls themselves: glowing packets that travel the rails, the tokens they leave in a zone, and the selection
// ring. A packet's place is always a function of its replay pose (see replay.js and layout.positionAt), never of its
// own state, so scrubbing, stepping and replaying can't drift from what the timeline says.

import * as THREE from "../vendor/three.min.js";
import { PALETTE, ACTION_COLOR, HOVER, positionAt, slotIn, isZone } from "./layout.js";
import { glow } from "./world.js";

var MAX_TOKENS = 24;
var TRAIL = 5;

function actionOf(run) { return run.verdict && run.verdict.action || null; }

/** Has the call reached its verdict yet? Until then it is drawn in the accent colour, not the verdict's. */
function decided(pose) {
  var station = pose.frame.station;
  return isZone(station) || (station === "decision" && pose.phase !== "travel");
}

export function createPackets(scene) {
  var layer = new THREE.Group();
  scene.add(layer);
  var packets = new Map(), tokens = new Map(), zones = { execution: [], review: [], incident: [] };
  var selected = null, ringMesh, ringTarget = null, clock = 0;

  ringMesh = new THREE.Mesh(new THREE.RingGeometry(0.86, 1, 48), new THREE.MeshBasicMaterial({ color: PALETTE.cyan, transparent: true, opacity: 0.85, side: THREE.DoubleSide, depthWrite: false, blending: THREE.AdditiveBlending }));
  ringMesh.rotation.x = -Math.PI / 2;
  ringMesh.position.y = 0.08;
  ringMesh.visible = false;
  ringMesh.renderOrder = 6;
  scene.add(ringMesh);

  function makePacket(run) {
    var g = new THREE.Group();
    var core = new THREE.Mesh(new THREE.OctahedronGeometry(0.7, 0), new THREE.MeshStandardMaterial({ color: PALETTE.cyan, emissive: PALETTE.cyan, emissiveIntensity: 1.1, roughness: 0.4, flatShading: true }));
    var halo = glow(PALETTE.cyan, 4.2, 0.75);
    g.add(core, halo);
    if (run.kind === "scenario") {          // a scenario run wears a thin ring: it is something you launched, not a recorded call
      var r = new THREE.Mesh(new THREE.TorusGeometry(0.95, 0.04, 5, 28), new THREE.MeshBasicMaterial({ color: PALETTE.glass, transparent: true, opacity: 0.9 }));
      r.rotation.x = Math.PI / 2;
      g.add(r);
    }
    var trail = [];
    for (var i = 0; i < TRAIL; i++) {
      var s = glow(PALETTE.cyan, 1.6 - i * 0.22, 0.3 - i * 0.05);
      layer.add(s); trail.push({ sprite: s, x: 0, y: 0, z: 0 });
    }
    layer.add(g);
    core.userData.pick = { kind: "run", id: run.id };
    var entry = { run: run, group: g, core: core, halo: halo, trail: trail, history: [], pos: new THREE.Vector3(), extras: g.children.filter(function (c) { return c !== core && c !== halo; }) };
    packets.set(run.id, entry);
    return entry;
  }

  function colorOf(run, pose) {
    var a = actionOf(run);
    return a && decided(pose) ? ACTION_COLOR[a] : PALETTE.cyan;
  }

  var api = {
    layer: layer,
    /** Draw `run` at `pose`; creates its packet on first use. */
    place: function (run, pose, reduced) {
      var tokenMesh = tokens.get(run.id);
      if (tokenMesh) tokenMesh.visible = false;           // the token is "in flight" while it replays
      var p = packets.get(run.id) || makePacket(run);
      var at = positionAt(pose, run.terminal);
      p.group.visible = true;
      p.pos.set(at.x, HOVER + at.lift, at.z);
      p.group.position.copy(p.pos);
      var c = colorOf(run, pose);
      p.core.material.color.setHex(c); p.core.material.emissive.setHex(c); p.halo.material.color.setHex(c);
      p.group.rotation.y = clock * 1.8;
      var showTrail = !reduced && pose.phase === "travel";
      p.history.unshift(p.pos.clone());
      if (p.history.length > TRAIL * 3) p.history.length = TRAIL * 3;
      p.trail.forEach(function (t, i) {
        var h = p.history[Math.min(p.history.length - 1, (i + 1) * 3)];
        t.sprite.visible = showTrail && !!h;
        if (h) t.sprite.position.copy(h);
        t.sprite.material.color.setHex(c);
      });
    },
    /** The call finished: its packet is replaced by a token resting in its zone. */
    settle: function (run, zone) {
      api.forget(run);                                   // the packet is gone for good; replaying one builds a new one
      var m = tokens.get(run.id);
      if (!m) {
        var a = actionOf(run), isIncident = zone === "incident";
        m = new THREE.Mesh(new THREE.BoxGeometry(0.62, isIncident ? 1.1 : 0.5, 0.62),
          new THREE.MeshStandardMaterial({ color: ACTION_COLOR[a] || PALETTE.cyan, emissive: ACTION_COLOR[a] || PALETTE.cyan, emissiveIntensity: 0.35, roughness: 0.6, flatShading: true }));
        m.userData.pick = { kind: "run", id: run.id };
        m.userData.run = run;
        m.userData.zone = zone;
        layer.add(m);
        tokens.set(run.id, m);
        zones[zone].unshift(run.id);
        while (zones[zone].length > MAX_TOKENS) {
          var old = zones[zone].pop(), om = tokens.get(old);
          if (om) { layer.remove(om); om.geometry.dispose(); om.material.dispose(); tokens.delete(old); }
        }
        arrange(zone);
      }
      m.visible = true;
    },
    /** Put a run straight into its zone as a token, with no flight (recorded calls found when the view opens). */
    rest: function (run, zone) { api.settle(run, zone); },
    forget: function (run) {
      var p = packets.get(run.id);
      if (!p) return;
      layer.remove(p.group);
      p.core.geometry.dispose(); p.core.material.dispose(); p.halo.material.dispose();       // (the glow texture is shared)
      p.extras.forEach(function (x) { if (x.geometry) x.geometry.dispose(); if (x.material) x.material.dispose(); });
      p.trail.forEach(function (t) { layer.remove(t.sprite); t.sprite.material.dispose(); });
      packets.delete(run.id);
    },
    /** Remove every packet and token of recorded calls (the time window changed, so they are a different set). */
    clearRecorded: function () { api.clearWhere(function (r) { return r.kind === "recorded"; }); },
    /** Remove every packet and token of the runs `pick(run)` says yes to. */
    clearWhere: function (pick) {
      Array.from(packets.keys()).forEach(function (id) { var p = packets.get(id); if (pick(p.run)) api.forget(p.run); });
      Array.from(tokens.keys()).forEach(function (id) {
        var m = tokens.get(id);
        if (!pick(m.userData.run)) return;
        layer.remove(m); m.geometry.dispose(); m.material.dispose(); tokens.delete(id);
        Object.keys(zones).forEach(function (z) { zones[z] = zones[z].filter(function (x) { return x !== id; }); });
      });
      Object.keys(zones).forEach(arrange);
    },
    countIn: function (zone) { return zones[zone].length; },
    inFlight: function () { var n = 0; packets.forEach(function (p) { if (p.group.visible) n += 1; }); return n; },
    has: function (id) { return packets.has(id) || tokens.has(id); },
    pickables: function () {
      var list = [];
      packets.forEach(function (p) { if (p.group.visible) list.push(p.core); });
      tokens.forEach(function (m) { if (m.visible) list.push(m); });
      return list;
    },
    positionOf: function (id) {
      var p = packets.get(id);
      if (p && p.group.visible) return p.pos.clone();
      var m = tokens.get(id);
      return m && m.visible ? m.position.clone() : null;
    },
    /** Ring around a place or a run. `target` is a Vector3 or a run id; radius in world units. */
    select: function (target, radius) {
      selected = target ? { target: target, radius: radius || 1.2 } : null;
      ringMesh.visible = !!selected;
    },
    update: function (dtMs, reduced) {
      clock += dtMs / 1000;
      if (!selected) return;
      var pos = typeof selected.target === "string" ? api.positionOf(selected.target) : selected.target;
      if (!pos) { ringMesh.visible = false; return; }
      ringMesh.visible = true;
      ringMesh.position.set(pos.x, 0.08, pos.z);
      var pulse = reduced ? 1 : 1 + 0.07 * Math.sin(clock * 4);
      ringMesh.scale.set(selected.radius * pulse, selected.radius * pulse, 1);
      ringMesh.material.opacity = reduced ? 0.85 : 0.6 + 0.25 * Math.sin(clock * 4);
    },
  };

  function arrange(zone) {
    zones[zone].forEach(function (id, n) {
      var m = tokens.get(id), s = slotIn(zone, n, 6);
      m.position.set(s.x, zone === "incident" ? 0.9 : 0.62, s.z);
    });
  }
  return api;
}
