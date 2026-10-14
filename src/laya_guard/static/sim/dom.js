// Tiny DOM helpers for the simulation view. Everything is built with createElement and textContent, never innerHTML,
// so nothing that reaches the page (a policy message, a command) can become markup.

var SVG_NS = "http://www.w3.org/2000/svg";

export function el(tag, props) {
  var node = document.createElement(tag);
  if (props) {
    Object.keys(props).forEach(function (k) {
      var v = props[k];
      if (v == null || v === false) return;
      if (k === "text") node.textContent = v;
      else if (k === "class") node.className = v;
      else if (k.slice(0, 2) === "on" && typeof v === "function") node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v === true ? "" : v);
    });
  }
  for (var i = 2; i < arguments.length; i++) append(node, arguments[i]);
  return node;
}

function append(node, child) {
  if (child == null || child === false) return;
  if (Array.isArray(child)) { child.forEach(function (c) { append(node, c); }); return; }
  node.appendChild(typeof child === "string" || typeof child === "number" ? document.createTextNode(String(child)) : child);
}

/** Set text only when it changed, so a value that is redrawn every frame doesn't cause a layout every frame. */
export function setText(node, text) { if (node.textContent !== text) node.textContent = text; }

export function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); return node; }

// icons: 24x24 stroke paths, drawn with the page's .ic class
var ICONS = {
  play: "M7 5v14l11-7z", pause: "M8 5v14M16 5v14", restart: "M4 12a8 8 0 1 0 3-6.2M4 4v5h5",
  back: "M18 5v14M16 12 7 5v14z", forward: "M6 5v14M8 12l9-7v14z", plus: "M12 5v14M5 12h14", minus: "M5 12h14",
  rotl: "M4 12a8 8 0 0 1 14-5M18 3v5h-5", rotr: "M20 12a8 8 0 0 0-14-5M6 3v5h5", home: "M4 11l8-7 8 7M6 10v9h12v-9",
  tour: "M12 3l2.2 5.6L20 9.3l-4.4 3.7L17 19l-5-3-5 3 1.4-6L4 9.3l5.8-.7z", flask: "M9 3h6M10 3v6l-5 9a2 2 0 0 0 2 3h10a2 2 0 0 0 2-3l-5-9V3",
};

export function icon(name) {
  var s = document.createElementNS(SVG_NS, "svg");
  s.setAttribute("class", "ic"); s.setAttribute("viewBox", "0 0 24 24"); s.setAttribute("aria-hidden", "true");
  var p = document.createElementNS(SVG_NS, "path");
  p.setAttribute("d", ICONS[name] || ICONS.plus);
  s.appendChild(p);
  return s;
}

export function fmtMs(ms) {
  if (ms == null || isNaN(ms)) return "–";
  return ms < 10 ? ms.toFixed(1) + " ms" : ms < 1000 ? Math.round(ms) + " ms" : (ms / 1000).toFixed(1) + " s";
}
export function fmtInt(n) { return Number(n || 0).toLocaleString(); }
export function fmtClock(iso) {
  var d = new Date(iso);
  return isNaN(d) ? "" : d.toLocaleTimeString([], { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" });
}
