// laya-guard-ax: what is on the screen where an agent is about to click, read through macOS Accessibility.
//
//   laya-guard-ax at X Y      the element at (X, Y) in screen points (top-left of the main display is 0,0)
//   laya-guard-ax pointer     the element under the mouse pointer (a click that names no coordinate lands there)
//   laya-guard-ax find Q [PID]  the elements named like Q in those apps' windows (default: the frontmost app and Finder)
//   laya-guard-ax selection   what is selected in the frontmost app (Finder's selected files, for Return or cmd+O)
//   laya-guard-ax trusted     whether this process may read the screen (exit 0 yes, 1 no)
//
// It prints one JSON object: {"trusted", "app", "bundle", "role", "labels": [...], "paths": [...]}. paths are the
// files the element stands for (a Finder row's file, a desktop icon, a file in an Open dialog); labels are the titles
// and descriptions on it and its parents (a button's name). It only reads: it never clicks, types or moves anything.
import AppKit
import ApplicationServices
import Foundation

func attr(_ e: AXUIElement, _ name: String) -> CFTypeRef? {
    var v: CFTypeRef?
    return AXUIElementCopyAttributeValue(e, name as CFString, &v) == .success ? v : nil
}
func str(_ e: AXUIElement, _ name: String) -> String? {
    guard let s = attr(e, name) as? String, !s.isEmpty else { return nil }
    return s
}
func fileURL(_ e: AXUIElement) -> String? {
    if let u = attr(e, "AXURL") as? URL, u.isFileURL { return u.path }
    return nil
}
func elements(_ e: AXUIElement, _ name: String) -> [AXUIElement] {
    guard let v = attr(e, name) else { return [] }
    if CFGetTypeID(v) == AXUIElementGetTypeID() { return [v as! AXUIElement] }
    return (v as? [AnyObject] ?? []).compactMap { CFGetTypeID($0) == AXUIElementGetTypeID() ? ($0 as! AXUIElement) : nil }
}
func parent(_ e: AXUIElement) -> AXUIElement? { elements(e, "AXParent").first }

// The files under an element, a few levels down: a Finder list row holds its file's URL in the name cell.
func pathsBelow(_ e: AXUIElement, depth: Int, into out: inout [String]) {
    if let p = fileURL(e), !out.contains(p) { out.append(p) }
    guard depth > 0, out.count < 250 else { return }      // above the 200 a selection reads, so none is skipped
    for c in elements(e, "AXChildren").prefix(20) { pathsBelow(c, depth: depth - 1, into: &out) }
}

func describe(_ start: AXUIElement) -> [String: Any] {
    var pid: pid_t = 0
    AXUIElementGetPid(start, &pid)
    let app = NSRunningApplication(processIdentifier: pid)
    var labels: [String] = [], paths: [String] = []
    var cur: AXUIElement? = start
    var row: AXUIElement? = nil
    for _ in 0..<6 {                                   // the element and its parents: labels, and the row it sits in
        guard let e = cur else { break }
        for k in ["AXTitle", "AXDescription", "AXHelp"] {
            if let s = str(e, k), !labels.contains(s) { labels.append(String(s.prefix(200))) }
        }
        if let p = fileURL(e), !paths.contains(p) { paths.append(p) }
        let role = str(e, "AXRole") ?? ""
        if row == nil && role == "AXRow" { row = e }
        if role == "AXOutline" || role == "AXTable" || role == "AXList" || role == "AXBrowser" || role == "AXWindow" { break }
        cur = parent(e)
    }
    if paths.isEmpty, let r = row { pathsBelow(r, depth: 3, into: &paths) }
    if paths.isEmpty && (str(start, "AXRole") == "AXRow") { pathsBelow(start, depth: 3, into: &paths) }
    return ["trusted": true, "app": app?.localizedName ?? "", "bundle": app?.bundleIdentifier ?? "",
            "role": str(start, "AXRole") ?? "", "labels": labels, "paths": paths]
}

func selection() -> [String: Any] {
    guard let front = NSWorkspace.shared.frontmostApplication else { return ["trusted": true, "paths": [], "labels": []] }
    let appEl = AXUIElementCreateApplication(front.processIdentifier)
    var paths: [String] = [], labels: [String] = [], truncated = false
    var cur = elements(appEl, "AXFocusedUIElement").first
    for _ in 0..<6 {                                   // up from the focused element to whatever holds the selection
        guard let e = cur else { break }
        if let p = fileURL(e), !paths.contains(p) { paths.append(p) }
        for k in ["AXTitle", "AXDescription"] { if let s = str(e, k), !labels.contains(s) { labels.append(s) } }
        let selected = elements(e, "AXSelectedRows") + elements(e, "AXSelectedChildren")
        // more selected than read: say so, so a protected file among the rest is asked about rather than missed
        if selected.count > 200 { truncated = true }
        for s in selected.prefix(200) { pathsBelow(s, depth: 3, into: &paths) }
        if !selected.isEmpty { break }
        cur = parent(e)
    }
    return ["trusted": true, "app": front.localizedName ?? "", "bundle": front.bundleIdentifier ?? "",
            "role": "selection", "labels": labels, "paths": paths, "truncated": truncated]
}

// The elements whose name contains `query` in the given apps' windows: what a click by name (Peekaboo's `query`, or an
// element ID resolved to its label) lands on. When some element is named exactly `query`, only those count: a click
// on "Search" lands on the Search button, not on a line of page text that happens to contain "search" (and "post").
// Bounded per app, so a huge window can't stall the hook, nor use up the search before Finder's window is reached.
func find(_ query: String, pids: [pid_t]) -> [String: Any] {
    let q = query.lowercased().trimmingCharacters(in: .whitespaces)
    var matches: [(exact: Bool, names: [String], paths: [String])] = []
    for pid in pids {
        var visited = 0
        func walk(_ e: AXUIElement, _ depth: Int) {
            if depth > 25 || visited > 4000 || matches.count >= 40 { return }
            visited += 1
            var names: [String] = []
            for k in ["AXFilename", "AXTitle", "AXDescription", "AXValue"] { if let s = str(e, k) { names.append(s) } }
            let low = names.map { $0.lowercased().trimmingCharacters(in: .whitespaces) }
            if low.contains(where: { $0.contains(q) }) {
                var found: [String] = []
                pathsBelow(e, depth: 1, into: &found)
                if found.isEmpty, let p = parent(e) { pathsBelow(p, depth: 3, into: &found) }   // a list cell: its row has the file
                matches.append((low.contains(q), names, found))
            }
            for c in elements(e, "AXChildren").prefix(200) { walk(c, depth + 1) }
        }
        let app = AXUIElementCreateApplication(pid)
        AXUIElementSetMessagingTimeout(app, 0.5)
        for w in elements(app, "AXWindows").prefix(10) { walk(w, 0) }
    }
    let chosen = matches.contains(where: { $0.exact }) ? matches.filter { $0.exact } : matches
    var paths: [String] = [], labels: [String] = [], truncated = matches.count >= 40
    for m in chosen {
        for n in m.names where !labels.contains(n) && labels.count < 20 { labels.append(String(n.prefix(200))) }
        for f in m.paths where !paths.contains(f) {
            if paths.count < 200 { paths.append(f) } else { truncated = true }
        }
    }
    return ["trusted": true, "role": "find", "labels": labels, "paths": paths, "found": !labels.isEmpty,
            "truncated": truncated]
}

func emit(_ obj: [String: Any]) -> Never {
    let data = try! JSONSerialization.data(withJSONObject: obj, options: [])
    FileHandle.standardOutput.write(data)
    FileHandle.standardOutput.write("\n".data(using: .utf8)!)
    exit(0)
}

let args = CommandLine.arguments
let trusted = AXIsProcessTrusted()
if args.count >= 2 && args[1] == "trusted" { print(trusted); exit(trusted ? 0 : 1) }
if !trusted { emit(["trusted": false, "paths": [], "labels": []]) }
let system = AXUIElementCreateSystemWide()
AXUIElementSetMessagingTimeout(system, 0.5)
func pointer() -> (Float, Float) {               // AX counts from the top-left of the main display, AppKit from its bottom-left
    let m = NSEvent.mouseLocation
    let h = NSScreen.screens.first?.frame.height ?? 0
    return (Float(m.x), Float(h - m.y))
}
var point: (Float, Float)? = nil
if args.count >= 4 && args[1] == "at", let x = Float(args[2]), let y = Float(args[3]) { point = (x, y) }
if args.count >= 2 && args[1] == "pointer" { point = pointer() }
if let (x, y) = point {
    var el: AXUIElement?
    guard AXUIElementCopyElementAtPosition(system, x, y, &el) == .success, let e = el else {
        emit(["trusted": true, "found": false, "paths": [], "labels": []])
    }
    var d = describe(e)
    d["found"] = true
    emit(d)
}
if args.count >= 2 && args[1] == "selection" { emit(selection()) }
if args.count >= 3 && args[1] == "find" {
    // find QUERY [PID ...]: the named apps, or the frontmost app and Finder
    var pids = args.dropFirst(3).compactMap { pid_t($0) }
    if pids.isEmpty {
        if let f = NSWorkspace.shared.frontmostApplication { pids.append(f.processIdentifier) }
        for a in NSRunningApplication.runningApplications(withBundleIdentifier: "com.apple.finder") where !pids.contains(a.processIdentifier) {
            pids.append(a.processIdentifier)
        }
    }
    emit(find(args[2], pids: pids))
}
FileHandle.standardError.write("usage: laya-guard-ax at X Y | pointer | selection | find QUERY [PID...] | trusted\n".data(using: .utf8)!)
exit(2)
