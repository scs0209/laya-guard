// The 3D stage: renderer, render loop, picking and hover. It owns no application state: it draws the world and the
// packets, reports what was clicked, and moves the camera where it is told.

import * as THREE from "../vendor/three.min.js";
import { createWorld } from "./world.js";
import { createCameraRig } from "./camera.js";
import { createPackets } from "./packets.js";
import { createLabels } from "./labels.js";

var RING = { terminal: 2.3, queue: 4.2, policy: 5, engine: 3.6, decision: 3.6, execution: 3.8, review: 3.8, incident: 3.8 };

export function createStage(canvas, labelsEl, config, hooks) {
  hooks = hooks || {};
  var renderer = new THREE.WebGLRenderer({ canvas: canvas, antialias: true, alpha: true, powerPreference: "high-performance" });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.setClearColor(0x000000, 0);
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.05;

  var world = createWorld(config);
  var packets = createPackets(world.scene);
  var labels = createLabels(labelsEl);
  labels.set(world.labels());
  var ray = new THREE.Raycaster();
  var running = false, raf = 0, last = 0, reduced = false, paused = false, size = { w: 1, h: 1 }, hoverId = null;

  var rig = createCameraRig(canvas, { onClick: onClick });

  function stationOf(id) { return String(id).split(":")[0]; }
  function pickAt(ev) {
    ray.setFromCamera(rig.ndc(ev), rig.camera);
    // calls (packets and the tokens in a zone) win over the stations they sit in or on: a zone's glass boundary
    // would otherwise be hit before the token inside it
    var runs = ray.intersectObjects(packets.pickables(), false);
    if (runs.length && runs[0].object.userData.pick) return runs[0].object.userData.pick;
    var hits = ray.intersectObjects(world.pickables, false);
    for (var i = 0; i < hits.length; i++) if (hits[i].object.userData.pick) return hits[i].object.userData.pick;
    return null;
  }
  function onClick(ev) { var pick = pickAt(ev); if (hooks.onPick) hooks.onPick(pick); }

  canvas.addEventListener("pointermove", function (ev) {
    if (ev.buttons) return;
    var pick = pickAt(ev), id = pick ? (pick.kind === "station" ? pick.id : pick.kind === "run" ? "run:" + pick.id : null) : null;
    canvas.style.cursor = pick ? "pointer" : "";
    if (id !== hoverId) { hoverId = id; labels.hover(id); }
  });
  canvas.addEventListener("pointerleave", function () { hoverId = null; labels.hover(null); canvas.style.cursor = ""; });
  canvas.addEventListener("webglcontextlost", function (ev) { ev.preventDefault(); stage.stop(); if (hooks.onLost) hooks.onLost(); });

  function resize() {
    var r = canvas.parentElement.getBoundingClientRect();
    var w = Math.max(1, Math.floor(r.width)), h = Math.max(1, Math.floor(r.height));
    if (w === size.w && h === size.h) return;
    size = { w: w, h: h };
    renderer.setSize(w, h, false);
    rig.resize(w, h);
  }
  var ro = typeof ResizeObserver === "function" ? new ResizeObserver(resize) : null;
  if (ro) ro.observe(canvas.parentElement);
  window.addEventListener("resize", resize);
  resize();

  function frame(now) {
    if (!running) return;
    raf = requestAnimationFrame(frame);
    var dt = Math.max(0, Math.min(50, now - last));      // the first frame's timestamp can precede the time start() noted
    last = now;
    resize();
    if (hooks.beforeFrame) hooks.beforeFrame(paused ? 0 : dt);
    world.update(paused ? 0 : dt, reduced);
    packets.update(paused ? 0 : dt, reduced);
    rig.update();
    labels.update(rig.camera, size.w, size.h);
    renderer.render(world.scene, rig.camera);
  }

  var stage = {
    world: world, packets: packets, rig: rig,
    start: function () { if (running) return; running = true; last = performance.now(); raf = requestAnimationFrame(frame); },
    stop: function () { running = false; cancelAnimationFrame(raf); },
    setReducedMotion: function (v) { reduced = !!v; rig.setReducedMotion(v); },
    setPaused: function (v) { paused = !!v; },
    /** Glide to a station, ring it and highlight its label. `id` is a station id ("policy", "terminal:1", ...). */
    focusStation: function (id) {
      var s = world.focusOf(id);
      if (!s) return;
      rig.follow(null);
      rig.glideTo(s.focus, s.zoom, 750);
      packets.select(s.focus.clone().setY(0), RING[stationOf(id)] || 3);
      labels.select(id);
    },
    focusPolicy: function (id) {
      var s = world.focusOf("policy");
      if (!s) return;
      rig.follow(null);
      rig.glideTo(s.focus, 2.2, 750);
      packets.select(s.focus.clone().setY(0), 5);
      labels.select("policy");
    },
    focusRun: function (id) {
      var pos = packets.positionOf(id);
      if (!pos) return;
      rig.glideTo(pos, 1.7, 650);
      rig.follow(function () { return packets.positionOf(id); });      // keeps it in view as it travels, until you move the view
      packets.select(id, 1.4);
      labels.select(null);
    },
    clearSelection: function () { rig.follow(null); packets.select(null); labels.select(null); },
    dispose: function () {
      stage.stop(); rig.destroy(); if (ro) ro.disconnect(); window.removeEventListener("resize", resize);
      world.scene.traverse(function (o) {
        if (o.geometry) o.geometry.dispose();
        if (o.material) { (Array.isArray(o.material) ? o.material : [o.material]).forEach(function (m) { if (m.map) m.map.dispose(); m.dispose(); }); }
      });
      renderer.dispose();
    },
  };
  return stage;
}

/** Can this browser make a WebGL context? (Creating one is the only reliable test.) */
export function webglAvailable() {
  try {
    var c = document.createElement("canvas"), gl = c.getContext("webgl2") || c.getContext("webgl");
    var lose = gl && gl.getExtension("WEBGL_lose_context");
    if (lose) lose.loseContext();                // don't keep a context alive just to have asked
    return !!gl;
  } catch (e) { return false; }
}
