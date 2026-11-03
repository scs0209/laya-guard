// Spike: what can a hook learn about the UI element at a screen point, right before an AI clicks it?
// usage: axprobe [x y]        (top-left screen points; default: current mouse position)
//        axprobe --watch N    (print the element under the mouse once a second for N seconds)
import AppKit
import ApplicationServices

let home = NSHomeDirectory()
func short(_ s: String, _ n: Int = 40) -> String {
    let t = s.replacingOccurrences(of: home, with: "~").replacingOccurrences(of: "\n", with: " ")
    return t.count > n ? String(t.prefix(n)) + "…" : t
}
func attr(_ e: AXUIElement, _ name: String) -> String? {
    var v: CFTypeRef?
    guard AXUIElementCopyAttributeValue(e, name as CFString, &v) == .success, let v = v else { return nil }
    if let s = v as? String { return s.isEmpty ? nil : s }
    if let u = v as? URL { return u.isFileURL ? u.path : (u.host ?? "url") }
    if CFGetTypeID(v) == AXUIElementGetTypeID() { return "<element>" }
    return String(describing: v)
}
func describe(_ e: AXUIElement) -> String {
    var parts: [String] = []
    for k in ["AXRole", "AXSubrole", "AXTitle", "AXDescription", "AXIdentifier", "AXURL", "AXFilename"] {
        if let v = attr(e, k) { parts.append("\(k.dropFirst(2))=\(short(v))") }
    }
    if let v = attr(e, "AXValue") { parts.append("Value(len=\(v.count))") }   // typed text: length only
    return parts.joined(separator: " ")
}
func probe(_ x: Double, _ y: Double) {
    let sys = AXUIElementCreateSystemWide()
    var el: AXUIElement?
    let err = AXUIElementCopyElementAtPosition(sys, Float(x), Float(y), &el)
    print(String(format: "point (%.0f, %.0f): ", x, y), terminator: "")
    guard err == .success, var cur = el else { print("no element (AXError \(err.rawValue))"); return }
    var pid: pid_t = 0
    AXUIElementGetPid(cur, &pid)
    let app = NSRunningApplication(processIdentifier: pid)
    print("app=\(app?.localizedName ?? "?") (\(app?.bundleIdentifier ?? "?"))")
    for depth in 0..<5 {                       // the element, then its ancestors (a button's label is often on a parent)
        print("  " + String(repeating: "  ", count: depth) + describe(cur))
        var p: CFTypeRef?
        guard AXUIElementCopyAttributeValue(cur, "AXParent" as CFString, &p) == .success, let pp = p else { break }
        cur = pp as! AXUIElement
    }
}
func mouse() -> (Double, Double) {
    let m = NSEvent.mouseLocation                      // bottom-left origin
    let h = NSScreen.screens.first?.frame.height ?? 0  // AX uses top-left origin of the main screen
    return (m.x, h - m.y)
}

print("AXIsProcessTrusted=\(AXIsProcessTrusted())")
if let front = NSWorkspace.shared.frontmostApplication {
    print("frontmost=\(front.localizedName ?? "?")")
    let a = AXUIElementCreateApplication(front.processIdentifier)
    var f: CFTypeRef?
    if AXUIElementCopyAttributeValue(a, "AXFocusedUIElement" as CFString, &f) == .success, let f = f {
        print("focused: " + describe(f as! AXUIElement))
    } else { print("focused: none / not readable") }
}
let args = CommandLine.arguments
if args.count >= 3, args[1] == "--watch", let n = Int(args[2]) {
    for _ in 0..<n { let (x, y) = mouse(); probe(x, y); Thread.sleep(forTimeInterval: 1) }
} else if args.count >= 3, let x = Double(args[1]), let y = Double(args[2]) {
    probe(x, y)
} else {
    let (x, y) = mouse(); probe(x, y)
}
