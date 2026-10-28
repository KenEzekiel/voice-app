import AppKit
import ApplicationServices
import ScreenCaptureKit

struct WorkerError: LocalizedError {
    let message: String
    var errorDescription: String? { message }
}

/// Select one delivery path. A failed semantic action may already have taken effect.
func deliverClick(press: (() -> AXError)?, pointer: () throws -> Void) throws -> String {
    if let press = press {
        guard press() == .success else {
            throw WorkerError(message: "The control did not confirm Press. No mouse fallback was sent; observe before retrying.")
        }
        return "accessibility"
    }
    try pointer()
    return "sharedPointer"
}

private final class AppLaunch {
    let done = DispatchSemaphore(value: 0)
    private let lock = NSLock()
    private var succeeded = false
    func complete(_ success: Bool) { lock.lock(); succeeded = success; lock.unlock(); done.signal() }
    func wait() -> Bool {
        guard done.wait(timeout: .now() + 5) == .success else { return false }
        lock.lock(); defer { lock.unlock() }; return succeeded
    }
}

struct ScreenshotGeometry {
    let display: CGRect
    let pixels: CGSize
    func point(request: [String: Any]) throws -> CGPoint {
        guard let x = request["x"] as? Double, let y = request["y"] as? Double else {
            throw WorkerError(message: "This action requires numeric screenshot-pixel x and y. For a control ID use Press with element.")
        }
        return try point(x: x, y: y)
    }
    func point(x: Double, y: Double) throws -> CGPoint {
        guard pixels.width.isFinite, pixels.height.isFinite, pixels.width > 0, pixels.height > 0 else {
            throw WorkerError(message: "No screenshot geometry is available. Observe before acting.")
        }
        guard x.isFinite, y.isFinite,
              x >= 0, y >= 0, x < pixels.width, y < pixels.height else {
            throw WorkerError(message: "Coordinates are outside the latest screenshot. Use screenshot pixels: 0 <= x < \(Int(pixels.width)), 0 <= y < \(Int(pixels.height)). Do not use desktop points or guess a scale.")
        }
        return CGPoint(x: display.minX + x * display.width / pixels.width,
                       y: display.minY + y * display.height / pixels.height)
    }
}

func typingChunks(_ text: String) throws -> [[UniChar]] {
    guard !text.isEmpty, text.utf16.count <= 500 else {
        throw WorkerError(message: "Type between 1 and 500 UTF-16 units per action.")
    }
    var chunks: [[UniChar]] = []
    var chunk: [UniChar] = []
    // Quartz event consumers can truncate long strings. Never split a surrogate pair.
    for scalar in text.unicodeScalars {
        let units = Array(String(scalar).utf16)
        if chunk.count + units.count > 20 { chunks.append(chunk); chunk = [] }
        chunk += units
    }
    if !chunk.isEmpty { chunks.append(chunk) }
    return chunks
}

/// One serial observer/actuator. The gate covers every event dispatch, including releases.
final class DesktopWorker {
    let gate: ActionGate
    let cursor = AgentCursor()
    private let queue = DispatchQueue(label: "voice-app.desktop-worker")
    private let sharingLock = NSLock()
    private var sharing = false
    private var revision = ""
    private var observedAt = Date.distantPast
    private var observedPID: pid_t = 0
    private var displayBounds = CGRect.zero
    private var imageSize = CGSize.zero
    private var elements: [AXUIElement] = []
    private let protectedApps: Set<String> = ["com.apple.SecurityAgent", "com.apple.keychainaccess", "com.apple.systempreferences"]

    init(gate: ActionGate) { self.gate = gate }
    func setSharing(_ enabled: Bool) {
        sharingLock.lock(); sharing = enabled; sharingLock.unlock()
    }
    private func checkSharing() throws {
        sharingLock.lock(); let enabled = sharing; sharingLock.unlock()
        guard enabled else { throw WorkerError(message: "Desktop sharing is off. Click Share desktop to enable it.") }
        guard AXIsProcessTrusted() else { throw WorkerError(message: "Allow Accessibility for Voice App, then retry.") }
        guard CGPreflightScreenCaptureAccess() else { throw WorkerError(message: "Allow Screen Recording for Voice App, then retry Share desktop.") }
    }
    private func value(_ element: AXUIElement, _ attribute: String) -> AnyObject? {
        var result: CFTypeRef?
        guard AXUIElementCopyAttributeValue(element, attribute as CFString, &result) == .success else { return nil }
        return result
    }
    private func frontApp() throws -> NSRunningApplication {
        guard let app = NSWorkspace.shared.frontmostApplication else { throw WorkerError(message: "No active application. Select an app and retry.") }
        guard !protectedApps.contains(app.bundleIdentifier ?? "") else {
            throw WorkerError(message: "A protected settings or credential app is active. Complete that step yourself, then return to your task.")
        }
        return app
    }
    private func describe(_ app: NSRunningApplication) -> [[String: Any]] {
        elements = []
        // Never expose or operate the controller's own controls through its worker.
        guard app.processIdentifier != ProcessInfo.processInfo.processIdentifier else { return [] }
        let root = AXUIElementCreateApplication(app.processIdentifier)
        AXUIElementSetMessagingTimeout(root, 0.15)
        var pending = [root]
        var descriptions: [[String: Any]] = []
        let deadline = Date().addingTimeInterval(1.5)
        while !pending.isEmpty && elements.count < 100 && Date() < deadline {
            let node = pending.removeFirst()
            AXUIElementSetMessagingTimeout(node, 0.15)
            let role = value(node, kAXRoleAttribute) as? String ?? ""
            // Secure fields are not included in text observations or actionable elements.
            if value(node, kAXSubroleAttribute) as? String == kAXSecureTextFieldSubrole { continue }
            var item: [String: Any] = ["id": elements.count, "role": role]
            for attribute in [kAXTitleAttribute, kAXDescriptionAttribute, kAXValueAttribute] {
                if let text = value(node, attribute) as? String { item[attribute] = String(text.prefix(1200)) }
            }
            var actions: CFArray?
            AXUIElementCopyActionNames(node, &actions)
            item["actions"] = actions as? [String] ?? []
            elements.append(node); descriptions.append(item)
            pending += value(node, kAXChildrenAttribute) as? [AXUIElement] ?? []
        }
        return descriptions
    }
    private func capture(displayID: CGDirectDisplayID?) throws -> (CGImage, CGRect) {
        let done = DispatchSemaphore(value: 0)
        var output: Result<(CGImage, CGRect), Error>?
        Task {
            do {
                let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
                guard let display = content.displays.first(where: { $0.displayID == (displayID ?? CGMainDisplayID()) }) else {
                    throw WorkerError(message: "Display unavailable. Observe again using an available display ID.")
                }
                let filter = SCContentFilter(display: display, excludingWindows: [])
                let config = SCStreamConfiguration()
                let bounds = CGDisplayBounds(display.displayID)
                let scale = min(1.0, 1600.0 / max(bounds.width, bounds.height))
                config.width = max(1, Int(bounds.width * scale))
                config.height = max(1, Int(bounds.height * scale))
                config.showsCursor = true
                let image = try await SCScreenshotManager.captureImage(contentFilter: filter, configuration: config)
                output = .success((image, bounds))
            } catch { output = .failure(error) }
            done.signal()
        }
        guard done.wait(timeout: .now() + 7) == .success, let result = output else {
            throw WorkerError(message: "Screen capture timed out. No action was dispatched; retry observation.")
        }
        return try result.get()
    }
    private func observe(displayID: CGDirectDisplayID? = nil) throws -> [String: Any] {
        try checkSharing()
        let app = try frontApp()
        let (image, bounds) = try capture(displayID: displayID)
        try checkSharing()
        guard NSWorkspace.shared.frontmostApplication?.processIdentifier == app.processIdentifier else {
            throw WorkerError(message: "The active app changed during capture. Observe again.")
        }
        let bitmap = NSBitmapImageRep(cgImage: image)
        guard let jpeg = bitmap.representation(using: .jpeg, properties: [.compressionFactor: 0.65]) else {
            throw WorkerError(message: "Screenshot encoding failed. No action was dispatched.")
        }
        let controls = describe(app)
        let browserURL = NSWorkspace.shared.urlForApplication(toOpen: URL(string: "https://example.com")!)
        var browser: [String: Any] = [:]
        if let url = browserURL, let bundle = Bundle(url: url), let identifier = bundle.bundleIdentifier {
            browser = ["bundleID": identifier, "name": bundle.object(forInfoDictionaryKey: "CFBundleName") as? String ?? "Browser"]
        }
        // Resolve installed browser identities locally; the model must not invent a fallback.
        let browsers: [[String: Any]] = NSWorkspace.shared.urlsForApplications(toOpen: URL(string: "https://example.com")!).compactMap { url in
            guard let bundle = Bundle(url: url), let identifier = bundle.bundleIdentifier else { return nil }
            // Deep-link handlers also register HTTPS. Only HTML viewers are browsers.
            let documents = bundle.object(forInfoDictionaryKey: "CFBundleDocumentTypes") as? [[String: Any]] ?? []
            guard documents.contains(where: {
                ($0["LSItemContentTypes"] as? [String] ?? []).contains("public.html") ||
                ($0["CFBundleTypeExtensions"] as? [String] ?? []).contains("html")
            }) else { return nil }
            return ["bundleID": identifier, "name": bundle.object(forInfoDictionaryKey: "CFBundleName") as? String ?? identifier]
        }
        revision = UUID().uuidString; observedAt = Date(); observedPID = app.processIdentifier
        displayBounds = bounds; imageSize = CGSize(width: image.width, height: image.height)
        var count: UInt32 = 0
        var displays = [CGDirectDisplayID](repeating: 0, count: 16)
        CGGetActiveDisplayList(16, &displays, &count)
        let (epoch, state) = gate.snapshot()
        return ["app": app.localizedName ?? "Unknown", "pid": app.processIdentifier,
                "bundleID": app.bundleIdentifier ?? "", "defaultBrowser": browser, "browsers": browsers,
                "revision": revision, "epoch": epoch, "state": state.rawValue,
                "observedAt": observedAt.timeIntervalSince1970, "elements": controls,
                "displays": Array(displays.prefix(Int(count))),
                "imageWidth": image.width, "imageHeight": image.height,
                "displayID": displayID ?? CGMainDisplayID(),
                "text": "Active app: \(app.localizedName ?? "Unknown"). Control labels and actions are in elements.",
                "screenshot": "data:image/jpeg;base64," + jpeg.base64EncodedString(),
                "visualContext": "One fresh screenshot of the requested display. Coordinates use screenshot pixels from top left. Other displays can be observed by displayID. No continuous stream."]
    }
    private func point(_ request: [String: Any]) throws -> CGPoint {
        try ScreenshotGeometry(display: displayBounds, pixels: imageSize).point(request: request)
    }
    private func targetElement(_ point: CGPoint) throws -> AXUIElement {
        var element: AXUIElement?
        let system = AXUIElementCreateSystemWide()
        AXUIElementSetMessagingTimeout(system, 0.15)
        guard AXUIElementCopyElementAtPosition(system, Float(point.x), Float(point.y), &element) == .success,
              let element = element else { throw WorkerError(message: "Cannot verify the pointer target. Observe again or use an accessibility control.") }
        try rejectProtectedElement(element)
        var pid: pid_t = 0
        guard AXUIElementGetPid(element, &pid) == .success, pid == observedPID else {
            throw WorkerError(message: "The pointer target belongs to a different app. Activate the intended app and observe before acting.")
        }
        return element
    }
    private func pressTarget(_ element: AXUIElement, at point: CGPoint) throws -> AXUIElement? {
        var candidate = element
        // Labels and icons can be children of the control that owns Press.
        for _ in 0..<4 {
            try rejectProtectedElement(candidate)
            var pid: pid_t = 0
            guard AXUIElementGetPid(candidate, &pid) == .success, pid == observedPID else { return nil }
            var actions: CFArray?
            if AXUIElementCopyActionNames(candidate, &actions) == .success,
               (actions as? [String] ?? []).contains(kAXPressAction) {
                return candidate
            }
            guard let parent = value(candidate, kAXParentAttribute), CFGetTypeID(parent) == AXUIElementGetTypeID() else { return nil }
            candidate = parent as! AXUIElement
            // Do not promote a point to an unrelated or unbounded ancestor.
            guard let rawPosition = value(candidate, kAXPositionAttribute),
                  let rawSize = value(candidate, kAXSizeAttribute),
                  CFGetTypeID(rawPosition) == AXValueGetTypeID(), CFGetTypeID(rawSize) == AXValueGetTypeID() else { return nil }
            var origin = CGPoint.zero, size = CGSize.zero
            guard AXValueGetValue(rawPosition as! AXValue, .cgPoint, &origin),
                  AXValueGetValue(rawSize as! AXValue, .cgSize, &size),
                  origin.x.isFinite, origin.y.isFinite, size.width.isFinite, size.height.isFinite,
                  size.width > 0, size.height > 0,
                  CGRect(origin: origin, size: size).contains(point) else { return nil }
        }
        return nil
    }
    private func rejectProtectedElement(_ element: AXUIElement) throws {
        AXUIElementSetMessagingTimeout(element, 0.15)
        var pid: pid_t = 0
        AXUIElementGetPid(element, &pid)
        let bundle = NSRunningApplication(processIdentifier: pid)?.bundleIdentifier ?? ""
        guard pid != ProcessInfo.processInfo.processIdentifier, !protectedApps.contains(bundle),
              value(element, kAXSubroleAttribute) as? String != kAXSecureTextFieldSubrole else {
            throw WorkerError(message: "Controller, settings, and credential controls require your direct interaction.")
        }
    }
    private func post(_ event: CGEvent?) throws {
        guard let event = event else { throw WorkerError(message: "Could not create an input event.") }
        event.post(tap: .cghidEventTap)
    }
    private func key(_ request: [String: Any]) throws {
        let keys: [String: CGKeyCode] = ["a":0, "s":1, "d":2, "f":3, "h":4, "g":5, "z":6, "x":7, "c":8, "v":9, "b":11, "q":12, "w":13, "e":14, "r":15, "y":16, "t":17, "o":31, "u":32, "i":34, "p":35, "l":37, "j":38, "k":40, "n":45, "m":46, "return":36, "tab":48, "space":49, "backspace":51, "escape":53, "delete":117, "home":115, "end":119, "pageup":116, "pagedown":121, "left":123, "right":124, "down":125, "up":126, "0":29, "1":18, "2":19, "3":20, "4":21, "5":23, "6":22, "7":26, "8":28, "9":25, "-":27, "=":24, ",":43, ".":47, "/":44, ";":41, "'":39, "[":33, "]":30, "\\":42, "`":50]
        guard let name = request["key"] as? String, let code = keys[name.lowercased()] else {
            throw WorkerError(message: "Use one supported key name and optional command, shift, option, control modifiers.")
        }
        let known: [String: CGEventFlags] = ["command":.maskCommand, "shift":.maskShift, "option":.maskAlternate, "control":.maskControl]
        var flags: CGEventFlags = []
        for modifier in request["modifiers"] as? [String] ?? [] {
            guard let flag = known[modifier] else { throw WorkerError(message: "Unknown keyboard modifier.") }
            flags.formUnion(flag)
        }
        guard let down = CGEvent(keyboardEventSource: nil, virtualKey: code, keyDown: true),
              let up = CGEvent(keyboardEventSource: nil, virtualKey: code, keyDown: false) else {
            throw WorkerError(message: "Could not create keyboard events.")
        }
        down.flags = flags; up.flags = flags
        try post(down); try post(up)
    }
    func perform(_ request: [String: Any], completion: @escaping ([String: Any]) -> Void) {
        queue.async {
            let operation = request["operation"] as? String ?? "observe"
            var inputMethod: String?
            do {
                let displayID = (request["displayID"] as? NSNumber)?.uint32Value
                if operation != "observe" {
                    var launch: AppLaunch?
                    var activationPID: pid_t?
                    var clickPress: AXUIElement?
                    try self.checkSharing()
                    guard let epoch = request["epoch"] as? Int else {
                        throw WorkerError(message: "The action omitted a numeric epoch. Copy it from the latest screenshot action context.")
                    }
                    guard let revision = request["revision"] as? String, !revision.isEmpty else {
                        throw WorkerError(message: "The action omitted a revision. Copy it from the latest screenshot action context.")
                    }
                    guard !self.revision.isEmpty, revision == self.revision else {
                        throw WorkerError(message: "The action used an outdated revision. Observe again, then copy the new action context.")
                    }
                    guard Date().timeIntervalSince(self.observedAt) < 20 else {
                        throw WorkerError(message: "The screenshot expired after 20 seconds. Observe again before acting.")
                    }
                    let chunks = operation == "type" ? try typingChunks(request["text"] as? String ?? "") : [[]]
                    if operation == "click" || operation == "scroll" {
                        let point = try self.point(request)
                        let element = try self.targetElement(point)
                        if operation == "click", request["button"] as? String != "right" {
                            clickPress = try self.pressTarget(element, at: point)
                        }
                        // Preview the target outside the gate. Stop can invalidate it immediately.
                        try self.cursor.move(to: point, gate: self.gate, epoch: epoch, waitForArrival: clickPress == nil)
                    } else if operation == "press", let index = request["element"] as? Int,
                              self.elements.indices.contains(index) {
                        let element = self.elements[index]
                        try self.rejectProtectedElement(element)
                        if let rawPosition = self.value(element, kAXPositionAttribute),
                           let rawSize = self.value(element, kAXSizeAttribute),
                           CFGetTypeID(rawPosition) == AXValueGetTypeID(), CFGetTypeID(rawSize) == AXValueGetTypeID() {
                            var origin = CGPoint.zero, size = CGSize.zero
                            if AXValueGetValue(rawPosition as! AXValue, .cgPoint, &origin),
                               AXValueGetValue(rawSize as! AXValue, .cgSize, &size),
                               origin.x.isFinite, origin.y.isFinite, size.width.isFinite, size.height.isFinite,
                               size.width > 0, size.height > 0 {
                                try self.cursor.move(to: CGPoint(x: origin.x + size.width / 2, y: origin.y + size.height / 2), gate: self.gate, epoch: epoch, waitForArrival: false)
                            }
                        }
                    }
                    for chars in chunks {
                      try self.gate.dispatch(epoch: epoch) {
                        try self.checkSharing()
                        let front = try self.frontApp()
                        guard front.processIdentifier == self.observedPID else { throw WorkerError(message: "The active app changed. Observe again before acting.") }
                        switch operation {
                        case "launchApp", "activate":
                            let url: URL
                            if operation == "activate" {
                                guard let number = request["pid"] as? Int,
                                      let pid = Int32(exactly: number), pid > 0 else {
                                    throw WorkerError(message: "App PID must be a positive process ID from the latest observation.")
                                }
                                guard let app = NSRunningApplication(processIdentifier: pid), !app.isTerminated else {
                                    throw WorkerError(message: "The target app process has exited. Observe again to refresh the app list.")
                                }
                                guard pid != ProcessInfo.processInfo.processIdentifier,
                                      !self.protectedApps.contains(app.bundleIdentifier ?? "") else {
                                    throw WorkerError(message: "Controller, settings, and credential apps require your direct interaction.")
                                }
                                guard app.activationPolicy == .regular else {
                                    throw WorkerError(message: "The target process is not a foreground app. Choose the main app from a fresh observation.")
                                }
                                guard let bundleURL = app.bundleURL else {
                                    throw WorkerError(message: "The running app has no application bundle. Select its visible window manually.")
                                }
                                activationPID = pid
                                url = bundleURL
                            } else {
                                guard let identifier = request["bundleID"] as? String,
                                      identifier != Bundle.main.bundleIdentifier, !self.protectedApps.contains(identifier),
                                      let installedURL = NSWorkspace.shared.urlForApplication(withBundleIdentifier: identifier) else {
                                    throw WorkerError(message: "Choose an installed, unprotected app bundle ID from the observation.")
                                }
                                url = installedURL
                            }
                            let pending = AppLaunch()
                            launch = pending
                            let configuration = NSWorkspace.OpenConfiguration()
                            configuration.activates = true
                            NSWorkspace.shared.openApplication(at: url, configuration: configuration) { app, error in
                                pending.complete(app != nil && error == nil)
                            }
                        case "click":
                            let point = try self.point(request)
                            let currentTarget = try self.targetElement(point)
                            if let selected = clickPress {
                                guard let current = try self.pressTarget(currentTarget, at: point), CFEqual(current, selected) else {
                                    throw WorkerError(message: "The control under the pointer target changed. Observe before acting.")
                                }
                            }
                            let right = request["button"] as? String == "right"
                            let button: CGMouseButton = right ? .right : .left
                            inputMethod = try deliverClick(press: clickPress.map { element in
                                { AXUIElementPerformAction(element, kAXPressAction as CFString) }
                            }, pointer: {
                              guard let down = CGEvent(mouseEventSource: nil, mouseType: right ? .rightMouseDown : .leftMouseDown, mouseCursorPosition: point, mouseButton: button),
                                  let up = CGEvent(mouseEventSource: nil, mouseType: right ? .rightMouseUp : .leftMouseUp, mouseCursorPosition: point, mouseButton: button) else {
                                throw WorkerError(message: "Could not create pointer events.")
                              }
                              try self.post(down); try self.post(up)
                            })
                        case "press":
                            guard let index = request["element"] as? Int, self.elements.indices.contains(index) else { throw WorkerError(message: "Use an element ID from the latest observation.") }
                            let element = self.elements[index]
                            try self.rejectProtectedElement(element)
                            guard AXUIElementPerformAction(element, kAXPressAction as CFString) == .success else { throw WorkerError(message: "The control rejected Press. Observe the current screen before retrying.") }
                            inputMethod = "accessibility"
                        case "type", "key":
                            guard front.processIdentifier != ProcessInfo.processInfo.processIdentifier else { throw WorkerError(message: "Select a target app before typing.") }
                            let root = AXUIElementCreateApplication(front.processIdentifier)
                            AXUIElementSetMessagingTimeout(root, 0.15)
                            if let focused = self.value(root, kAXFocusedUIElementAttribute) {
                                try self.rejectProtectedElement(focused as! AXUIElement)
                            } else { throw WorkerError(message: "No verified focused control. Click a target first.") }
                            if operation == "key" { try self.key(request) }
                            else {
                                guard let down = CGEvent(keyboardEventSource: nil, virtualKey: 0, keyDown: true),
                                      let up = CGEvent(keyboardEventSource: nil, virtualKey: 0, keyDown: false) else { throw WorkerError(message: "Could not create typing events.") }
                                down.keyboardSetUnicodeString(stringLength: chars.count, unicodeString: chars)
                                up.keyboardSetUnicodeString(stringLength: chars.count, unicodeString: chars)
                                try self.post(down); try self.post(up)
                            }
                        case "scroll":
                            let point = try self.point(request)
                            _ = try self.targetElement(point)
                            guard let amount = request["amount"] as? Int, (-500...500).contains(amount), amount != 0,
                                  let event = CGEvent(scrollWheelEvent2Source: nil, units: .pixel, wheelCount: 1, wheel1: Int32(amount), wheel2: 0, wheel3: 0) else { throw WorkerError(message: "Scroll amount must be between -500 and 500 pixels, excluding zero.") }
                            event.location = point; try self.post(event)
                            inputMethod = "sharedPointer"
                        default: throw WorkerError(message: "Unsupported desktop action.")
                        }
                      }
                      // Each chunk reacquires the gate, allowing Stop to invalidate the remainder.
                      if chunks.count > 1 { Thread.sleep(forTimeInterval: 0.01) }
                    }
                    // Waiting for launch never holds the admission lock. Stop may
                    // invalidate the next command while this dispatched launch settles.
                    if let launch = launch {
                        guard launch.wait() else { throw WorkerError(message: "App launch did not settle. Observe before retrying.") }
                    }
                    if let pid = activationPID {
                        // LaunchServices completion means the request settled, not that focus moved.
                        // Waiting reads state only; it never dispatches another activation after Stop.
                        let deadline = Date().addingTimeInterval(1)
                        while NSWorkspace.shared.frontmostApplication?.processIdentifier != pid,
                              Date() < deadline { Thread.sleep(forTimeInterval: 0.02) }
                        guard NSWorkspace.shared.frontmostApplication?.processIdentifier == pid else {
                            throw WorkerError(message: "App activation settled, but the requested app did not become active. Observe before retrying.")
                        }
                    }
                    // Posting is synchronous; the OS may apply the event later. Always inspect its effect.
                    self.revision = ""
                    Thread.sleep(forTimeInterval: 0.2)
                }
                self.cursor.hide()
                var observation = try self.observe(displayID: displayID)
                observation["apps"] = NSWorkspace.shared.runningApplications.filter { $0.activationPolicy == .regular }.map {
                    ["pid": $0.processIdentifier, "name": $0.localizedName ?? "Unknown", "bundleID": $0.bundleIdentifier ?? ""] as [String: Any]
                }
                self.gate.noteReadback(epoch: observation["epoch"] as? Int ?? self.gate.snapshot().0,
                    operation: operation, success: true, revision: observation["revision"] as? String, inputMethod: inputMethod)
                var result: [String: Any] = ["success": true, "observation": observation,
                            "actionResult": operation == "observe" ? "Observed" : "Dispatched one action; inspect the fresh screenshot and accessibility state to verify the outcome."]
                if let inputMethod = inputMethod { result["inputMethod"] = inputMethod }
                completion(result)
            } catch {
                self.cursor.hide()
                self.revision = ""
                // Keep only our fixed rejection messages; never log screen text or request arguments.
                let diagnostic = (error as? WorkerError)?.message ?? (error is GateError ? "Work was paused or stopped." : "Native operation failed.")
                self.gate.noteReadback(epoch: self.gate.snapshot().0, operation: operation,
                    success: false, revision: nil, error: diagnostic)
                completion(["success": false, "error": error is GateError ? "Work was paused or stopped. Observe before retrying; dispatched inputs may still take effect." : error.localizedDescription,
                            "effect": "An action may already have been dispatched. Observe before retrying."])
            }
        }
    }
}
