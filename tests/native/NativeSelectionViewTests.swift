import AppKit

@MainActor @main struct NativeSelectionViewTests {
    static func event(_ type: NSEvent.EventType, point: NSPoint, view: NSView, window: NSWindow, key: UInt16 = 0) -> NSEvent {
        if type == .keyDown {
            return NSEvent.keyEvent(with: type, location: .zero, modifierFlags: [], timestamp: 0,
                                   windowNumber: window.windowNumber, context: nil, characters: "", charactersIgnoringModifiers: "", isARepeat: false, keyCode: key)!
        }
        return NSEvent.mouseEvent(with: type, location: view.convert(point, to: nil), modifierFlags: [], timestamp: 0,
                                  windowNumber: window.windowNumber, context: nil, eventNumber: 1, clickCount: 1, pressure: 1)!
    }
    static func main() {
        _ = NSApplication.shared
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 400, height: 300), styleMask: [.titled], backing: .buffered, defer: false)
        window.alphaValue = 0
        let view = NativeSelectionView(horizontal: true)
        view.frame = NSRect(x: 10, y: 10, width: 360, height: 48)
        window.contentView?.addSubview(view)
        window.orderBack(nil)
        let choices = [NativeChoice(id: "age:auto", title: "Auto"), NativeChoice(id: "age:mature", title: "Mature"), NativeChoice(id: "age:neonatal", title: "Neonatal", enabled: false)]
        view.apply(choices: choices, selected: "age:auto", label: "Image age")
        view.layoutSubtreeIfNeeded()
        var requests: [String] = []
        view.onRequest = { requests.append($0) }
        let centers = view.optionFrames.map { NSPoint(x: $0.midX, y: $0.midY) }
        let lens = view.subviews.compactMap { $0 as? NSGlassEffectView }.first!
        view.mouseDown(with: event(.leftMouseDown, point: centers[0], view: view, window: window))
        view.mouseDragged(with: event(.leftMouseDragged, point: centers[1], view: view, window: window))
        precondition(view.interaction.tracking && view.selected == "age:auto")
        precondition(lens.effectIsInteractive && !lens.isHidden)
        view.mouseUp(with: event(.leftMouseUp, point: centers[1], view: view, window: window))
        precondition(requests == ["age:mature"] && view.selected == "age:auto")
        view.apply(choices: choices, selected: "age:mature", label: "Image age")
        precondition(view.selected == "age:mature")
        view.mouseDown(with: event(.leftMouseDown, point: centers[0], view: view, window: window))
        view.keyDown(with: event(.keyDown, point: .zero, view: view, window: window, key: 53))
        view.mouseUp(with: event(.leftMouseUp, point: centers[0], view: view, window: window))
        precondition(requests.count == 1 && view.selected == "age:mature")
        view.mouseDown(with: event(.leftMouseDown, point: centers[2], view: view, window: window))
        precondition(!view.interaction.tracking)
        view.mouseDown(with: event(.leftMouseDown, point: centers[0], view: view, window: window))
        view.frame.size.width = 380; view.layoutSubtreeIfNeeded()
        precondition(!view.interaction.tracking && requests.count == 1)
        view.mouseDown(with: event(.leftMouseDown, point: centers[0], view: view, window: window))
        NotificationCenter.default.post(name: NSWindow.didResignKeyNotification, object: window)
        precondition(!view.interaction.tracking && requests.count == 1)
        view.mouseDown(with: event(.leftMouseDown, point: centers[0], view: view, window: window))
        NotificationCenter.default.post(name: NSWindow.didResizeNotification, object: window)
        precondition(!view.interaction.tracking && requests.count == 1, "Whole window resize cancels even fixed-size controls")
        let buttons = view.accessibilityChildren() as! [NSButton]
        precondition(buttons.count == 3 && !buttons[2].isEnabled && buttons[1].state == .on)
        precondition(buttons[0].accessibilityPerformPress())
        precondition(requests.last == "age:auto" && view.selected == "age:mature")
        view.mouseDown(with: event(.leftMouseDown, point: centers[0], view: view, window: window))
        RunLoop.main.run(until: Date(timeIntervalSinceNow: 1.0))
        precondition(!lens.isHidden && lens.frame.width > view.optionFrames[0].width,
                     "Holding an option must visibly expand the native glass lens")
        precondition(view.request(index: 0))
        RunLoop.main.run(until: Date(timeIntervalSinceNow: 0.5))
        precondition(view.selected == "age:mature" && lens.isHidden,
                     "Rejected keyboard or AX request while held must settle the glass")
        let longModule = NativeChoice(id: "module:long", title: "Astrocyte Fluorescence Intensity Measurement with extended descriptive metadata", detail: "Ready")
        let compactHeight = nativeChoiceHeights([longModule], width: 142, minimum: 72)[0]
        let wideHeight = nativeChoiceHeights([longModule], width: 260, minimum: 72)[0]
        precondition(compactHeight > wideHeight && compactHeight > 72, "Long dynamic titles must grow rows instead of clipping")
        view.close(); window.orderOut(nil)
        print("NativeSelectionView: real NSWindow, AppKit mouse handlers, interactive system glass, controller confirmation, Escape, resize, focus loss, disabled options and AX press passed")
    }
}
