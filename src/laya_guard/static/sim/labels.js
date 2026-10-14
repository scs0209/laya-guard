// Station labels: plain DOM text laid over the canvas and moved each frame to sit above their station. Real text is
// readable at any size, can be styled and read by assistive technology, and costs nothing next to canvas text.

import { el, clear } from "./dom.js";
import * as THREE from "../vendor/three.min.js";

export function createLabels(container) {
  var items = [], v = new THREE.Vector3();
  return {
    set: function (list) {
      clear(container);
      items = list.map(function (l) {
        var node = el("div", { "class": "sim-label", "data-id": l.id }, el("b", { text: l.text }), l.sub ? el("span", { text: l.sub }) : null);
        container.appendChild(node);
        return { spec: l, node: node };
      });
    },
    /** Move each label to its station's screen position (hidden when it is off screen). */
    update: function (camera, w, h) {
      items.forEach(function (it) {
        v.copy(it.spec.pos).project(camera);
        var x = (v.x * 0.5 + 0.5) * w, y = (-v.y * 0.5 + 0.5) * h, off = x < -40 || x > w + 40 || y < -20 || y > h + 40;
        it.node.hidden = off;
        if (!off) it.node.style.transform = "translate(" + x.toFixed(1) + "px," + y.toFixed(1) + "px) translate(-50%,-100%)";
      });
    },
    select: function (id) {
      items.forEach(function (it) { it.node.classList.toggle("sel", it.spec.id === id); });
    },
    hover: function (id) {
      items.forEach(function (it) { it.node.classList.toggle("hov", it.spec.id === id); });
    },
  };
}
