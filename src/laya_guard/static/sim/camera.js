// The camera: an orthographic, isometric-style view you can pan, rotate and zoom, that glides to what you select.
// Mouse: drag pans, right-drag (or shift-drag) rotates, the wheel zooms. Touch: drag pans, pinch zooms. Keyboard
// (canvas focused): arrows pan, + and - zoom, [ and ] rotate, 0 resets. The same moves are on screen as buttons.

import * as THREE from "../vendor/three.min.js";

var DEFAULT = { az: Math.PI / 4, el: 0.62, target: [2, 0, 0] };
var DIST = 80;
var MIN_ZOOM = 0.3, MAX_ZOOM = 4;
var BASE_HEIGHT = 33;          // world units visible top to bottom at zoom 1

function ease(p) { return p < 0.5 ? 4 * p * p * p : 1 - Math.pow(-2 * p + 2, 3) / 2; }

export function createCameraRig(canvas, hooks) {
  hooks = hooks || {};
  var camera = new THREE.OrthographicCamera(-10, 10, 10, -10, 0.1, 400);
  var view = { az: DEFAULT.az, el: DEFAULT.el, zoom: 1, target: new THREE.Vector3().fromArray(DEFAULT.target) };
  var userZoomed = false;
  var follow = null, tween = null, size = { w: 1, h: 1 }, reduced = false, pointers = new Map(), gesture = null, moved = 0, downAt = null;

  function apply() {
    var aspect = size.w / size.h, h = BASE_HEIGHT / view.zoom, w = h * aspect;
    camera.left = -w / 2; camera.right = w / 2; camera.top = h / 2; camera.bottom = -h / 2;
    camera.updateProjectionMatrix();
    var ce = Math.cos(view.el), se = Math.sin(view.el);
    camera.position.set(view.target.x + DIST * Math.sin(view.az) * ce, view.target.y + DIST * se, view.target.z + DIST * Math.cos(view.az) * ce);
    camera.lookAt(view.target);
    camera.updateMatrixWorld(true);
    if (hooks.onChange) hooks.onChange();
  }

  function screenToGround(dxPx, dyPx) {
    // how far the target moves in the ground plane for a drag of (dx, dy) pixels
    var unit = (BASE_HEIGHT / view.zoom) / size.h;
    var right = new THREE.Vector3(Math.cos(view.az), 0, -Math.sin(view.az));
    var forward = new THREE.Vector3(-Math.sin(view.az), 0, -Math.cos(view.az));
    return right.multiplyScalar(-dxPx * unit).add(forward.multiplyScalar(dyPx * unit / Math.sin(view.el)));
  }

  /** The zoom at which the whole diorama fits the canvas, for its width and height (about 52 x 40 world units on screen). */
  function fitZoom() { return Math.max(0.35, Math.min(1.1, BASE_HEIGHT * (size.w / size.h) / 52, BASE_HEIGHT / 40)); }

  function clampTarget() {
    view.target.x = Math.max(-26, Math.min(28, view.target.x));
    view.target.z = Math.max(-17, Math.min(17, view.target.z));
    view.zoom = Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, view.zoom));
  }

  var rig = {
    camera: camera,
    view: view,
    setReducedMotion: function (v) { reduced = !!v; },
    resize: function (w, h) { size.w = Math.max(1, w); size.h = Math.max(1, h); if (!userZoomed) view.zoom = fitZoom(); apply(); },
    /** Glide to a target point, zoom, azimuth and elevation (instantly under reduced motion). */
    glideTo: function (target, zoom, ms, az, el) {
      var to = { x: target.x, z: target.z, zoom: zoom || view.zoom, az: az == null ? view.az : az, el: el == null ? view.el : el };
      if (reduced || !ms) { view.target.set(to.x, 0, to.z); view.zoom = to.zoom; view.az = to.az; view.el = to.el; clampTarget(); apply(); return; }
      tween = { t0: performance.now(), ms: ms, to: to,
                from: { x: view.target.x, z: view.target.z, zoom: view.zoom, az: view.az, el: view.el } };
    },
    reset: function () { userZoomed = false; rig.glideTo(new THREE.Vector3().fromArray(DEFAULT.target), fitZoom(), 650, DEFAULT.az, DEFAULT.el); },
    rotateTo: function (az, ms) { rig.glideTo(view.target, view.zoom, ms, az); },
    rotateBy: function (deg) { rig.rotateTo(view.az + deg * Math.PI / 180, 350); },
    zoomBy: function (factor) { userZoomed = true; view.zoom *= factor; clampTarget(); apply(); },
    panBy: function (dx, dz) { view.target.x += dx; view.target.z += dz; clampTarget(); apply(); },
    /** Keep the view centred on a moving point (a function returning a Vector3 or null) until the user moves it. */
    follow: function (fn) { follow = fn || null; },
    /** Advance a glide or a follow; returns true while one is running. */
    update: function () {
      if (follow && !tween && !gesture) {
        var p = follow();
        if (!p) follow = null;
        else {
          var k = reduced ? 1 : 0.07;
          view.target.x += (p.x - view.target.x) * k; view.target.z += (p.z - view.target.z) * k;
          clampTarget(); apply();
          return true;
        }
      }
      if (!tween) return false;
      var p = Math.min(1, (performance.now() - tween.t0) / tween.ms), e = ease(p), f = tween.from, t = tween.to;
      view.target.x = f.x + (t.x - f.x) * e; view.target.z = f.z + (t.z - f.z) * e;
      view.zoom = f.zoom + (t.zoom - f.zoom) * e;
      view.az = f.az + (t.az - f.az) * e; view.el = f.el + (t.el - f.el) * e;
      clampTarget(); apply();
      if (p >= 1) tween = null;
      return true;
    },
    /** A pick ray's normalised device coordinates for a pointer event. */
    ndc: function (ev) {
      var r = canvas.getBoundingClientRect();
      return { x: ((ev.clientX - r.left) / r.width) * 2 - 1, y: -((ev.clientY - r.top) / r.height) * 2 + 1 };
    },
    destroy: function () {
      canvas.removeEventListener("pointerdown", onDown); canvas.removeEventListener("pointermove", onMove);
      canvas.removeEventListener("pointerup", onUp); canvas.removeEventListener("pointercancel", onUp);
      canvas.removeEventListener("wheel", onWheel); canvas.removeEventListener("keydown", onKey); canvas.removeEventListener("contextmenu", noMenu);
    },
  };

  function pinchState() {
    var pts = Array.from(pointers.values());
    return { d: Math.hypot(pts[0].x - pts[1].x, pts[0].y - pts[1].y), mx: (pts[0].x + pts[1].x) / 2, my: (pts[0].y + pts[1].y) / 2 };
  }
  function onDown(ev) {
    canvas.setPointerCapture && canvas.setPointerCapture(ev.pointerId);
    pointers.set(ev.pointerId, { x: ev.clientX, y: ev.clientY });
    tween = null; follow = null;
    if (pointers.size === 1) {
      moved = 0; downAt = { x: ev.clientX, y: ev.clientY };
      gesture = { mode: (ev.button === 2 || ev.shiftKey) ? "rotate" : "pan" };
    } else if (pointers.size === 2) {
      gesture = { mode: "pinch", last: pinchState() };
      downAt = null;
    }
  }
  function onMove(ev) {
    var prev = pointers.get(ev.pointerId);
    if (!prev || !gesture) return;
    var dx = ev.clientX - prev.x, dy = ev.clientY - prev.y;
    pointers.set(ev.pointerId, { x: ev.clientX, y: ev.clientY });
    if (gesture.mode === "pinch" && pointers.size >= 2) {
      var now = pinchState(), last = gesture.last;
      if (last.d > 0) { userZoomed = true; view.zoom *= now.d / last.d; }
      var mv = screenToGround(now.mx - last.mx, now.my - last.my);
      view.target.add(mv);
      gesture.last = now;
      clampTarget(); apply();
      return;
    }
    moved += Math.abs(dx) + Math.abs(dy);
    if (gesture.mode === "rotate") { view.az -= dx * 0.008; view.el = Math.max(0.35, Math.min(1.1, view.el + dy * 0.004)); apply(); }
    else { view.target.add(screenToGround(dx, dy)); clampTarget(); apply(); }
  }
  function onUp(ev) {
    var wasClick = pointers.size === 1 && moved < 6 && downAt && ev.type === "pointerup" && ev.button !== 2;
    pointers.delete(ev.pointerId);
    if (pointers.size === 0) { gesture = null; }
    else if (pointers.size === 1) { gesture = { mode: "pan" }; moved = 99; }
    if (wasClick && hooks.onClick) hooks.onClick(ev);
    downAt = null;
  }
  function onWheel(ev) {
    ev.preventDefault();
    var k = Math.exp(-ev.deltaY * (ev.ctrlKey ? 0.01 : 0.0015));
    userZoomed = true;
    view.zoom *= k; clampTarget(); apply();
  }
  function onKey(ev) {
    var step = 1.6 / view.zoom, handled = true;
    if (ev.key === "ArrowLeft") rig.panBy(-Math.cos(view.az) * step, Math.sin(view.az) * step);
    else if (ev.key === "ArrowRight") rig.panBy(Math.cos(view.az) * step, -Math.sin(view.az) * step);
    else if (ev.key === "ArrowUp") rig.panBy(-Math.sin(view.az) * step, -Math.cos(view.az) * step);
    else if (ev.key === "ArrowDown") rig.panBy(Math.sin(view.az) * step, Math.cos(view.az) * step);
    else if (ev.key === "+" || ev.key === "=") rig.zoomBy(1.2);
    else if (ev.key === "-" || ev.key === "_") rig.zoomBy(1 / 1.2);
    else if (ev.key === "[") rig.rotateBy(-15);
    else if (ev.key === "]") rig.rotateBy(15);
    else if (ev.key === "0") rig.reset();
    else handled = false;
    if (handled) { ev.preventDefault(); ev.stopPropagation(); }      // the dashboard's own shortcuts (0 = all calls) must not also fire
  }
  function noMenu(ev) { ev.preventDefault(); }

  canvas.addEventListener("pointerdown", onDown); canvas.addEventListener("pointermove", onMove);
  canvas.addEventListener("pointerup", onUp); canvas.addEventListener("pointercancel", onUp);
  canvas.addEventListener("wheel", onWheel, { passive: false }); canvas.addEventListener("keydown", onKey);
  canvas.addEventListener("contextmenu", noMenu);
  apply();
  return rig;
}
