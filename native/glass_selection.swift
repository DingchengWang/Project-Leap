import AppKit
import QuartzCore

struct NativeChoice: Equatable {
    let id: String
    let title: String
    var symbol: String? = nil
    var detail = ""
    var hasError = false
    var enabled = true
    var tooltip = ""
}

@MainActor func nativeChoiceHeights(_ choices: [NativeChoice], width: CGFloat, minimum: CGFloat) -> [CGFloat] {
    choices.map { choice in
        guard !choice.detail.isEmpty else { return minimum }
        let height = (choice.title as NSString).boundingRect(
            with: NSSize(width: max(40, width-36), height: 10000),
            options: [.usesLineFragmentOrigin, .usesFontLeading],
            attributes: [.font: NSFont.systemFont(ofSize: 12, weight: .medium)]).height
        return max(minimum, ceil(height)+36)
    }
}

@MainActor private func drawText(_ value: String, rect: NSRect, size: CGFloat, weight: NSFont.Weight = .regular,
                                 color: NSColor = .labelColor, alignment: NSTextAlignment = .left) {
    let paragraph = NSMutableParagraphStyle()
    paragraph.alignment = alignment
    paragraph.lineBreakMode = .byWordWrapping
    (value as NSString).draw(in: rect, withAttributes: [.font: NSFont.systemFont(ofSize: size, weight: weight),
                                                       .foregroundColor: color, .paragraphStyle: paragraph])
}
@MainActor private final class LensView: NSGlassEffectView {
    override func hitTest(_ point: NSPoint) -> NSView? { nil }
}
@MainActor private final class OptionButton: NSButton {
    override func isAccessibilityElement() -> Bool { true }
    override func accessibilityRole() -> NSAccessibility.Role? { .radioButton }
    override func accessibilityChildren() -> [Any]? { [] }
    override func accessibilityValue() -> Any? { NSNumber(value: state == .on) }
    override func isAccessibilityEnabled() -> Bool { isEnabled }
    override func hitTest(_ point: NSPoint) -> NSView? { nil }
    override func accessibilityPerformPress() -> Bool {
        guard isEnabled, let action else { return false }
        return sendAction(action, to: target)
    }
}

/// Labels remain behind the clear system lens to supply refraction.
@MainActor final class NativeSelectionView: NSView {
    let horizontal: Bool
    let rowHeight: CGFloat
    private(set) var choices: [NativeChoice] = []
    private(set) var interaction = SelectionInteraction()
    private let lens = LensView()
    private var options: [OptionButton] = []
    private var timer: Timer?
    private var keyMonitor: Any?
    private weak var accessibilityWindow: NSWindow?
    private var lastFrameTime = CACurrentMediaTime()
    private var visualPosition = 0.0
    private var velocity = 0.0
    private var lift = 0.0
    private var liftVelocity = 0.0
    private var releaseStarted = 0.0
    private var releaseLift = 0.0
    private var previousSize = NSSize.zero
    private var confirmedSelection = ""
    var onRequest: ((String) -> Void)?
    var onTracking: ((Bool) -> Void)?
    override var isFlipped: Bool { true }
    override var acceptsFirstResponder: Bool { true }
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
    var idealHeight: CGFloat { horizontal ? 48 : 18 + nativeChoiceHeights(choices, width: bounds.width, minimum: rowHeight).reduce(0, +) + CGFloat(max(0, choices.count-1)) * 6 }
    var selected: String { interaction.selectedIndex.map { choices[$0].id } ?? "" }
    private var target: Double { interaction.center }
    var optionFrames: [NSRect] {
        guard !choices.isEmpty else { return [] }
        if horizontal {
            let width = max(1, (bounds.width-14) / CGFloat(choices.count))
            return choices.indices.map { NSRect(x: 7+CGFloat($0)*width, y: 9, width: width, height: max(1, bounds.height-18)) }
        }
        var y: CGFloat = 9
        return nativeChoiceHeights(choices, width: bounds.width, minimum: rowHeight).map { height in
            defer { y += height+6 }
            return NSRect(x: 7, y: y, width: max(1, bounds.width-14), height: height)
        }
    }
    init(horizontal: Bool = false, rowHeight: CGFloat = 38) {
        self.horizontal = horizontal; self.rowHeight = rowHeight
        super.init(frame: .zero)
        wantsLayer = true; layer?.masksToBounds = true
        NativeAppearance.shared.selectionViews.add(self)
        setAccessibilityElement(true); setAccessibilityRole(.group)
        lens.style = .clear; lens.effectIsInteractive = true
        lens.contentView = NSView(); lens.cornerRadius = 28; lens.isHidden = true
        lens.setAccessibilityElement(false); addSubview(lens)
        NotificationCenter.default.addObserver(self, selector: #selector(windowResigned(_:)), name: NSWindow.didResignKeyNotification, object: nil)
        NotificationCenter.default.addObserver(self, selector: #selector(windowResigned(_:)), name: NSWindow.didResizeNotification, object: nil)
        NotificationCenter.default.addObserver(self, selector: #selector(applicationResigned), name: NSApplication.didResignActiveNotification, object: nil)
        keyMonitor = NSEvent.addLocalMonitorForEvents(matching: .keyDown) { [weak self] event in
            guard let self, event.window === self.window, event.keyCode == 53, self.interaction.tracking else { return event }
            self.cancelInteraction(); return nil
        }
    }
    required init?(coder: NSCoder) { nil }
    func apply(choices next: [NativeChoice], selected: String, label: String) {
        setAccessibilityLabel(label)
        confirmedSelection = selected
        if next != choices {
            cancelInteraction(); choices = next
            options.forEach { $0.removeFromSuperview() }; options.removeAll()
            for (index, choice) in choices.enumerated() {
                let button = OptionButton()
                button.tag = index; button.target = self; button.action = #selector(choose(_:))
                button.title = choice.title; button.setButtonType(.radio); button.isTransparent = true
                button.isEnabled = choice.enabled; button.toolTip = choice.tooltip
                button.setAccessibilityLabel(choice.title); button.setAccessibilityIdentifier(choice.id)
                let help = [choice.detail, choice.tooltip == choice.title ? "" : choice.tooltip].filter { !$0.isEmpty }.joined(separator: "\n")
                button.setAccessibilityHelp(help.isEmpty ? nil : help)
                addSubview(button, positioned: .below, relativeTo: lens); options.append(button)
            }
            configure()
        }
        let oldSelection = self.selected
        interaction.confirm(index: choices.firstIndex { $0.id == selected })
        if oldSelection != self.selected && !interaction.tracking { startTimer() }
        synchronizeOptions(); needsDisplay = true
    }
    override func layout() {
        super.layout()
        if previousSize != bounds.size { configure() }
        updateLens()
    }
    private func configure() {
        guard bounds.width > 14, bounds.height > 18 else { return }
        interaction.cancel(); onTracking?(false); previousSize = bounds.size
        let frames = optionFrames
        interaction.configure(centers: frames.map { Double(horizontal ? $0.midX : $0.midY) },
                              enabled: choices.map(\.enabled), lowerBound: 0,
                              upperBound: Double(horizontal ? bounds.width : max(bounds.height, idealHeight)))
        interaction.confirm(index: choices.firstIndex { $0.id == confirmedSelection })
        for index in options.indices { options[index].frame = frames[index] }
        synchronizeOptions()
        visualPosition = target; velocity = 0; lift = 0; liftVelocity = 0
        lens.isHidden = true; stopTimer(); needsDisplay = true
    }
    override func viewWillMove(toWindow newWindow: NSWindow?) {
        if newWindow !== window { cancelInteraction(); detachAccessibility() }
        super.viewWillMove(toWindow: newWindow)
    }
    override func viewDidMoveToWindow() {
        super.viewDidMoveToWindow()
        attachAccessibility()
    }
    override func viewDidChangeEffectiveAppearance() {
        super.viewDidChangeEffectiveAppearance()
        needsDisplay = true
    }
    func refreshAppearance() {
        if NativeAppearance.shared.reducedMotion { settleMotion() }
        needsDisplay = true
    }
    override func viewDidHide() {
        cancelInteraction(); detachAccessibility()
        super.viewDidHide()
    }
    override func viewDidUnhide() {
        super.viewDidUnhide()
        attachAccessibility()
    }
    private func attachAccessibility() {
        guard horizontal, !isHiddenOrHasHiddenAncestor, let window, let root = window.contentView,
              NSStringFromClass(type(of: root)).contains("QNSView"), accessibilityWindow == nil else { return }
        accessibilityWindow = window
        setAccessibilityParent(window)
        var children = window.accessibilityChildren() ?? []
        children.removeAll { ($0 as AnyObject) === self }
        children.append(self)
        window.setAccessibilityChildren(children)
        NSAccessibility.post(element: window, notification: .layoutChanged)
    }
    private func detachAccessibility() {
        guard let window = accessibilityWindow else { return }
        let children = (window.accessibilityChildren() ?? []).filter { ($0 as AnyObject) !== self }
        window.setAccessibilityChildren(children)
        accessibilityWindow = nil; setAccessibilityParent(nil)
    }
    @objc private func windowResigned(_ note: Notification) {
        if let affected = note.object as? NSWindow, affected === window { cancelInteraction() }
    }
    @objc private func applicationResigned() { cancelInteraction() }
    override func draw(_ dirtyRect: NSRect) {
        let frames = optionFrames
        if horizontal {
            NSColor.controlBackgroundColor.setFill()
            NSBezierPath(roundedRect: bounds, xRadius: 20, yRadius: 20).fill()
        }
        if !interaction.tracking, let selected = interaction.selectedIndex, frames.indices.contains(selected) {
            NSColor.unemphasizedSelectedContentBackgroundColor.withAlphaComponent(max(0, 1-lift*1.5)).setFill()
            NSBezierPath(roundedRect: frames[selected], xRadius: 18, yRadius: 18).fill()
        }
        for (index, choice) in choices.enumerated() {
            let rect = frames[index]
            let highlighted = interaction.previewIndex == index
            let color: NSColor = !choice.enabled ? .disabledControlTextColor : highlighted ? .controlAccentColor : .labelColor
            if horizontal {
                drawText(choice.title, rect: NSRect(x: rect.minX+3, y: rect.midY-9, width: rect.width-6, height: 20), size: 13, weight: .medium, color: color, alignment: .center)
            } else if !choice.detail.isEmpty {
                drawText(choice.title, rect: NSRect(x: rect.minX+11, y: rect.minY+7, width: rect.width-22, height: rect.height-34), size: 12, weight: .medium, color: color)
                (choice.hasError ? NSColor.systemRed : NSColor.systemGreen).setFill()
                NSBezierPath(ovalIn: NSRect(x: rect.minX+12, y: rect.maxY-18, width: 5, height: 5)).fill()
                drawText(choice.detail, rect: NSRect(x: rect.minX+24, y: rect.maxY-24, width: rect.width-32, height: 17), size: 10, color: .secondaryLabelColor)
            } else {
                if let symbol = choice.symbol, let image = NSImage(systemSymbolName: symbol, accessibilityDescription: nil) {
                    image.draw(in: NSRect(x: rect.minX+11, y: rect.midY-7, width: 14, height: 14))
                }
                drawText(choice.title, rect: NSRect(x: rect.minX+33, y: rect.midY-9, width: rect.width-40, height: 22), size: 12, weight: .medium, color: color)
            }
        }
    }
    private func contains(_ point: NSPoint) -> Bool { bounds.contains(point) && visibleRect.contains(point) }
    private func position(_ point: NSPoint) -> Double { Double(horizontal ? point.x : point.y) }
    override func mouseDown(with event: NSEvent) {
        let point = convert(event.locationInWindow, from: nil)
        guard contains(point), interaction.begin(at: position(point)) else { return }
        window?.makeFirstResponder(self); onTracking?(true)
        visualPosition = target; velocity = 0; releaseStarted = 0
        lens.isHidden = false; lens.alphaValue = 1; startTimer(); updateLens(); needsDisplay = true
    }
    override func mouseDragged(with event: NSEvent) {
        guard interaction.tracking else { return }
        interaction.move(to: position(convert(event.locationInWindow, from: nil)))
        visualPosition = target; velocity = 0; updateLens(); needsDisplay = true
    }
    override func mouseUp(with event: NSEvent) {
        guard interaction.tracking else { return }
        let point = convert(event.locationInWindow, from: nil)
        let candidate = interaction.end(at: position(point), inside: contains(point))
        onTracking?(false)
        if let candidate { request(index: candidate) }
        releaseStarted = CACurrentMediaTime(); releaseLift = lift; startTimer()
    }
    func cancelInteraction() {
        guard interaction.tracking else { return }
        interaction.cancel(); onTracking?(false)
        releaseStarted = CACurrentMediaTime(); releaseLift = lift; startTimer()
    }
    @discardableResult func request(index: Int) -> Bool {
        guard choices.indices.contains(index), choices[index].enabled else { return false }
        cancelInteraction(); interaction.cancel(); onTracking?(false)
        onRequest?(choices[index].id); synchronizeOptions(); needsDisplay = true
        return true
    }
    @objc private func choose(_ button: NSButton) { _ = request(index: button.tag) }
    override func keyDown(with event: NSEvent) {
        switch event.keyCode {
        case 53: cancelInteraction()
        case horizontal ? 123 : 126: if let index = interaction.step(direction: -1) { _ = request(index: index) }
        case horizontal ? 124 : 125: if let index = interaction.step(direction: 1) { _ = request(index: index) }
        case 115: if let index = choices.firstIndex(where: \.enabled) { _ = request(index: index) }
        case 119: if let index = choices.lastIndex(where: \.enabled) { _ = request(index: index) }
        default: super.keyDown(with: event)
        }
    }
    private func synchronizeOptions() {
        for index in options.indices {
            let next: NSControl.StateValue = interaction.selectedIndex == index ? .on : .off
            if options[index].state != next {
                options[index].state = next
                NSAccessibility.post(element: options[index], notification: .valueChanged)
            }
        }
    }
    override func accessibilityChildren() -> [Any]? { isHiddenOrHasHiddenAncestor ? [] : options }
    private func updateLens() {
        guard !optionFrames.isEmpty else { lens.isHidden = true; return }
        let index = interaction.previewIndex ?? 0
        let first = optionFrames[min(index, optionFrames.count-1)]
        let width = first.width + 12*lift
        let height = first.height + 18*lift
        lens.frame = NSRect(x: (horizontal ? CGFloat(visualPosition) : first.midX)-width/2,
                            y: (horizontal ? first.midY : CGFloat(visualPosition))-height/2,
                            width: width, height: height)
        lens.cornerRadius = min(28+6*lift, height/2)
        if !interaction.tracking { lens.alphaValue = min(1, max(0, lift*1.8)) }
    }
    private func startTimer() {
        if NativeAppearance.shared.reducedMotion { settleMotion(); return }
        guard timer == nil else { return }
        lastFrameTime = CACurrentMediaTime()
        let next = Timer(timeInterval: 1.0/120, repeats: true) { [weak self] _ in MainActor.assumeIsolated { self?.tick() } }
        timer = next; RunLoop.main.add(next, forMode: .common)
    }
    private func stopTimer() { timer?.invalidate(); timer = nil }
    private func settleMotion() {
        stopTimer()
        visualPosition = target; velocity = 0; liftVelocity = 0
        lift = interaction.tracking ? 1 : 0
        lens.isHidden = !interaction.tracking
        updateLens(); needsDisplay = true
    }
    private func tick() {
        let now = CACurrentMediaTime(), dt = min(1.0/30, now-lastFrameTime)
        lastFrameTime = now
        if interaction.tracking {
            liftVelocity += (340*(1-lift)-29*liftVelocity)*dt; lift += liftVelocity*dt
        } else {
            velocity += (420*(target-visualPosition)-36*velocity)*dt; visualPosition += velocity*dt
            let progress = min(1, max(0, (now-releaseStarted-0.05)/0.23))
            lift = releaseLift*(1-progress*progress*(3-2*progress))
        }
        updateLens(); needsDisplay = true
        if !interaction.tracking && lift <= 0.001 && abs(visualPosition-target) < 0.2 {
            visualPosition = target; lift = 0; liftVelocity = 0; velocity = 0; lens.isHidden = true; stopTimer()
        } else if interaction.tracking && abs(1-lift) < 0.001 && abs(liftVelocity) < 0.01 { lift = 1; stopTimer() }
    }
    func close() {
        interaction.cancel(); stopTimer(); detachAccessibility(); onRequest = nil; onTracking = nil
        NativeAppearance.shared.selectionViews.remove(self)
        NotificationCenter.default.removeObserver(self)
        if let keyMonitor { NSEvent.removeMonitor(keyMonitor); self.keyMonitor = nil }
    }
}
