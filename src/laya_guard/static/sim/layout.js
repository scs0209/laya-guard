// Where everything stands in the scene, and the colours it uses. Plain data, so it can be tested and shared.
// Units are arbitrary world units (a packet is about one unit wide). x runs along the pipeline, z across it.

export var PALETTE = {
  bg: 0x0a1016, base: 0x0f1820, top: 0x15212b, edge: 0x22333f, steel: 0x7b8f9e, dark: 0x0c141b,
  cyan: 0x22d3ee, ok: 0x34d399, warn: 0xf5b53a, bad: 0xf26464, glass: 0x8adff0,
};
export var CSS = { cyan: "#22d3ee", ok: "#34d399", warn: "#f5b53a", bad: "#f26464" };
export var ACTION_COLOR = { allow: PALETTE.ok, review: PALETTE.warn, block: PALETTE.bad };

// Each station has an `in` port where calls arrive, an `out` port where they leave, and a centre. A call dwelling at
// a gate passes from `in` to `out` through it; a call travelling follows the straight rail from one port to the next.
export var LAYOUT = {
  terminals: [
    { x: -18, z: -6.5, kind: "agent", name: "Agent terminal 1" },
    { x: -18, z: -1.5, kind: "agent", name: "Agent terminal 2" },
    { x: -18, z: 5.5, kind: "scenario", name: "Scenario terminal" },
  ],
  queue:     { center: [-10, 0], in: [-13.2, 0], out: [-6.8, 0] },
  policy:    { center: [-2.8, 0], in: [-5.6, 0], out: [0, 0] },
  engine:    { center: [4.5, -8.4], in: [2.2, -8.4], out: [6.8, -8.4] },
  decision:  { center: [11, 0], in: [9.2, 0], out: [12.8, 0] },
  execution: { center: [20.5, -8.5], in: [17.6, -8.5], out: [20.5, -8.5] },
  review:    { center: [20.5, 0], in: [17.6, 0], out: [20.5, 0] },
  incident:  { center: [20.5, 8.5], in: [17.6, 8.5], out: [20.5, 8.5] },
};

export var GROUND = { x0: -24, x1: 26, z0: -15, z1: 15 };
export var HOVER = 1.5;       // how high a packet floats above the ground

/** The {in, center, out} of a station as [x, z] pairs. Terminals are numbered (0 and 1 are agents, 2 the scenario). */
export function portsOf(station, terminalIndex) {
  if (station === "terminal") {
    var t = LAYOUT.terminals[terminalIndex == null ? 0 : terminalIndex];
    return { center: [t.x, t.z], in: [t.x, t.z], out: [t.x + 1.9, t.z] };
  }
  return LAYOUT[station];
}

/** The rails between stations, as [from station, to station, from-terminal-index?]. */
export var RAILS = [
  ["terminal", "queue", 0], ["terminal", "queue", 1], ["terminal", "queue", 2],
  ["queue", "policy"], ["policy", "decision"], ["policy", "engine"], ["engine", "decision"],
  ["decision", "execution"], ["decision", "review"], ["decision", "incident"],
];

/** Where a call is at a pose: [x, z] and a lift above the ground, for the replay `pose` of a call that leaves
 *  terminal `terminalIndex`. Travelling follows the rail from one station's out port to the next one's in port.
 *  Dwelling at a gate passes through it from its in port to its out port; at a terminal it leaves from the centre,
 *  and in a zone it settles from the in port to the centre. */
export function positionAt(pose, terminalIndex) {
  if (pose.phase === "travel") {
    var a = portsOf(pose.from.station, terminalIndex).out, b = portsOf(pose.to.station, terminalIndex).in, p = ease(pose.p);
    return { x: a[0] + (b[0] - a[0]) * p, z: a[1] + (b[1] - a[1]) * p, lift: Math.sin(Math.PI * pose.p) * 0.9 };
  }
  var station = pose.frame.station, ports = portsOf(station, terminalIndex), q = pose.phase === "end" ? 1 : pose.p, from, to;
  if (station === "terminal") { from = ports.center; to = ports.out; }
  else if (isZone(station)) { from = ports.in; to = ports.center; q = Math.min(1, q * 2); }
  else { from = ports.in; to = ports.out; }
  return { x: from[0] + (to[0] - from[0]) * q, z: from[1] + (to[1] - from[1]) * q, lift: 0 };
}

export function isZone(station) { return station === "execution" || station === "review" || station === "incident"; }
function ease(p) { return p < 0.5 ? 2 * p * p : 1 - Math.pow(-2 * p + 2, 2) / 2; }

/** Slots for the tokens that rest in a zone: a grid of `cols` per row, newest first, around the zone's centre. */
export function slotIn(station, n, cols) {
  cols = cols || 6;
  var c = LAYOUT[station].center, col = n % cols, row = Math.floor(n / cols);
  return { x: c[0] - 1.1 + col * 0.9 - (cols - 1) * 0.45 + 0.9, z: c[1] - 1.3 + row * 0.9 };
}
