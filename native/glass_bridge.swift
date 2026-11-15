import AppKit
import SwiftUI

public typealias LeapGlassAction = @convention(c) (UnsafePointer<CChar>?, UnsafeMutableRawPointer?) -> Void

/// One AppKit source for native controls and the Qt/QML appearance model.
@MainActor final class NativeAppearance: NSObject {
    static let shared = NativeAppearance()
    let selectionViews = NSHashTable<NativeSelectionView>.weakObjects()
    private(set) var reducedMotion = NSWorkspace.shared.accessibilityDisplayShouldReduceMotion
    private var appearanceObservation: NSKeyValueObservation?
    private var callbacks: [UUID: () -> Void] = [:]

    private override init() {
        super.init()
        NotificationCenter.default.addObserver(self, selector: #selector(systemOptionsChanged),
                                               name: NSColor.systemColorsDidChangeNotification, object: nil)
        NSWorkspace.shared.notificationCenter.addObserver(self, selector: #selector(systemOptionsChanged),
            name: NSWorkspace.accessibilityDisplayOptionsDidChangeNotification, object: nil)
        appearanceObservation = NSApplication.shared.observe(\.effectiveAppearance) { [weak self] _, _ in
            MainActor.assumeIsolated { self?.refresh() }
        }
    }
    @objc private func systemOptionsChanged() { refresh() }
    func refresh() {
        reducedMotion = NSWorkspace.shared.accessibilityDisplayShouldReduceMotion
        selectionViews.allObjects.forEach { $0.refreshAppearance() }
        for callback in callbacks.values { callback() }
    }
    func subscribe(_ callback: @escaping () -> Void) -> UUID {
        let token = UUID(); callbacks[token] = callback; return token
    }
    func unsubscribe(_ token: UUID) { callbacks.removeValue(forKey: token) }
    func snapshot() -> [String: Any] {
        let appearance = NSApplication.shared.effectiveAppearance
        var accent: NSColor?
        appearance.performAsCurrentDrawingAppearance { accent = NSColor.controlAccentColor.usingColorSpace(.sRGB) }
        guard let accent else { return ["error": "AppKit could not convert the system accent color to sRGB."] }
        return ["dark": appearance.bestMatch(from: [.aqua, .darkAqua]) == .darkAqua,
                "accent": [accent.redComponent, accent.greenComponent, accent.blueComponent, accent.alphaComponent],
                "reducedMotion": reducedMotion]
    }
}

@MainActor private final class AppearanceHandle {
    private var token: UUID?
    private var snapshotBuffer: UnsafeMutablePointer<CChar>?
    init(callback: LeapGlassAction?, context: UnsafeMutableRawPointer?) {
        token = NativeAppearance.shared.subscribe { [weak self] in
            guard let self, let json = self.snapshot() else { return }
            callback?(json, context)
        }
    }
    func snapshot() -> UnsafePointer<CChar>? {
        guard let data = try? JSONSerialization.data(withJSONObject: NativeAppearance.shared.snapshot()),
              let json = String(data: data, encoding: .utf8) else { return nil }
        free(snapshotBuffer); snapshotBuffer = strdup(json)
        return snapshotBuffer.map { UnsafePointer($0) }
    }
    func close() {
        if let token { NativeAppearance.shared.unsubscribe(token) }
        token = nil; free(snapshotBuffer); snapshotBuffer = nil
    }
}

@MainActor @_cdecl("leap_appearance_create")
public func leapAppearanceCreate(_ callback: LeapGlassAction?, _ context: UnsafeMutableRawPointer?) -> UnsafeMutableRawPointer? {
    guard Thread.isMainThread else { return nil }
    return Unmanaged.passRetained(AppearanceHandle(callback: callback, context: context)).toOpaque()
}
@MainActor @_cdecl("leap_appearance_snapshot")
public func leapAppearanceSnapshot(_ handle: UnsafeMutableRawPointer?) -> UnsafePointer<CChar>? {
    guard Thread.isMainThread, let handle else { return nil }
    return Unmanaged<AppearanceHandle>.fromOpaque(handle).takeUnretainedValue().snapshot()
}
@MainActor @_cdecl("leap_appearance_destroy")
public func leapAppearanceDestroy(_ handle: UnsafeMutableRawPointer?) {
    guard Thread.isMainThread, let handle else { return }
    let retained = Unmanaged<AppearanceHandle>.fromOpaque(handle)
    retained.takeUnretainedValue().close(); retained.release()
}

@MainActor private final class ApplicationIconState {
    static let shared = ApplicationIconState()
    let models = NSHashTable<SidebarModel>.weakObjects()
    func apply(_ image: NSImage) {
        NSApplication.shared.applicationIconImage = image
        for model in models.allObjects { model.applicationIcon = image }
    }
}

@MainActor @_cdecl("leap_application_icon_update")
public func leapApplicationIconUpdate(_ png: UnsafePointer<UInt8>?, _ length: Int) -> Int32 {
    guard Thread.isMainThread, let png, length > 0,
          let image = NSImage(data: Data(bytes: png, count: length)), image.isValid else { return 0 }
    ApplicationIconState.shared.apply(image)
    return 1
}

private struct ModuleSpec: Decodable, Identifiable, Equatable {
    let id: String
    let name: String
    let hasError: Bool
    let statusLabel: String
    let tooltip: String
}

private struct SidebarState: Decodable {
    var workspaceHeading = ""
    var navigationAccessibilityLabel = ""
    var workspaceAccessibilityLabel = ""
    var navigation = ["", "", "", ""]
    var page = "analysis"
    var modulesHeading = ""
    var emptyModules = ""
    var modules: [ModuleSpec] = []
    var activeModuleId = ""
    var activityHeading = ""
    var activity = ""
    var busy = false
    var showProgress = false
    var cancelling = false
    var progress = 0.0
    var fijiAvailable = false
    var showFijiLabel = ""
    var cancelLabel = ""
    var version = ""
}

private struct Choice: Identifiable {
    let id: String
    let title: String
    let symbol: String?
    let module: ModuleSpec?
}

private struct RevealRequest: Equatable {
    var action = ""
    var sequence = 0
}

@MainActor private final class SidebarModel: ObservableObject {
    static let pages = ["analysis", "jobs", "maintenance", "settings"]
    static let symbols = ["square.grid.2x2", "clock.arrow.circlepath", "shippingbox", "gearshape"]
    @Published private(set) var state = SidebarState()
    @Published private(set) var reveal = RevealRequest()
    @Published var applicationIcon = NSApplication.shared.applicationIconImage
    var onAction: ((String) -> Void)?
    var tracking: Set<String> = []
    var nativeGroups: [String: NativeSelectionView] = [:]

    init() { ApplicationIconState.shared.models.add(self) }

    var navigation: [Choice] {
        Self.pages.indices.map { Choice(id: "page:" + Self.pages[$0], title: state.navigation[$0],
                                         symbol: Self.symbols[$0], module: nil) }
    }
    var modules: [Choice] {
        state.modules.map { Choice(id: "module:" + $0.id, title: $0.name, symbol: nil, module: $0) }
    }
    func choices(_ group: String) -> [Choice] { group == "navigation" ? navigation : modules }
    func selected(_ group: String) -> String {
        group == "navigation" ? "page:" + state.page : "module:" + state.activeModuleId
    }
    func apply(_ next: SidebarState) -> Bool {
        guard next.navigation.count == 4, Self.pages.contains(next.page), next.progress.isFinite,
              Set(next.modules.map(\.id)).count == next.modules.count,
              next.modules.allSatisfy({ !$0.id.isEmpty }) else { return false }
        if state.modules.map(\.id) != next.modules.map(\.id) { cancelDrags() }
        state = next
        return true
    }
    func cancelDrags() {
        for view in nativeGroups.values { view.cancelInteraction() }
    }
    func enabled(_ action: String) -> Bool {
        if navigation.contains(where: { $0.id == action }) || modules.contains(where: { $0.id == action }) { return true }
        if action == "showFiji" { return state.fijiAvailable }
        if action == "cancelAnalysis" { return state.busy && !state.cancelling }
        return false
    }
    @discardableResult func dispatch(_ action: String) -> Bool {
        guard enabled(action) else { return false }
        if action.hasPrefix("module:") {
            reveal = RevealRequest(action: action, sequence: reveal.sequence + 1)
        }
        // Controller state is authoritative. A rejected request must never
        // create a locally committed selection.
        onAction?(action)
        return true
    }
}

private struct AppKitSelectionGroup: NSViewRepresentable {
    @ObservedObject var model: SidebarModel
    let group: String
    func makeNSView(context: Context) -> NativeSelectionView {
        let view = NativeSelectionView(rowHeight: group == "navigation" ? 38 : 72)
        model.nativeGroups[group] = view
        view.onRequest = { [weak model] in _ = model?.dispatch($0) }
        view.onTracking = { [weak model] active in
            if active { model?.tracking.insert(group) } else { model?.tracking.remove(group) }
        }
        return view
    }
    func updateNSView(_ view: NativeSelectionView, context: Context) {
        view.apply(choices: model.choices(group).map {
            NativeChoice(id: $0.id, title: $0.title, symbol: $0.symbol,
                         detail: $0.module?.statusLabel ?? "", hasError: $0.module?.hasError ?? false,
                         tooltip: $0.module?.tooltip ?? $0.title)
        }, selected: model.selected(group), label: group == "navigation" ? model.state.workspaceHeading : model.state.modulesHeading)
    }
    static func dismantleNSView(_ view: NativeSelectionView, coordinator: ()) { view.close() }
}

private struct GlassSelectionGroup: View {
    @ObservedObject var model: SidebarModel
    let group: String
    @State private var measuredWidth: CGFloat = 196
    private var rowHeight: CGFloat { group == "navigation" ? 38 : 72 }
    private var rowHeights: [CGFloat] {
        nativeChoiceHeights(model.choices(group).map { NativeChoice(id: $0.id, title: $0.title, detail: $0.module?.statusLabel ?? "") },
                            width: measuredWidth, minimum: rowHeight)
    }
    private var height: CGFloat { 18 + rowHeights.reduce(0, +) + CGFloat(max(0, model.choices(group).count-1))*6 }
    var body: some View {
        AppKitSelectionGroup(model: model, group: group)
            .frame(height: height)
            .overlay {
                VStack(spacing: 6) {
                    ForEach(Array(model.choices(group).enumerated()), id: \.element.id) { index, choice in
                        Color.clear.frame(height: rowHeights[index]).id(choice.id)
                    }
                }
                .padding(.horizontal, 7).padding(.vertical, 9).allowsHitTesting(false).accessibilityHidden(true)
            }
            .onGeometryChange(for: CGFloat.self) { $0.size.width } action: { measuredWidth = $0 }
    }
}

private struct SidebarMaterial: NSViewRepresentable {
    func makeNSView(context: Context) -> NSVisualEffectView {
        let view = NSVisualEffectView()
        view.material = .sidebar
        view.blendingMode = .withinWindow
        view.state = .followsWindowActiveState
        return view
    }
    func updateNSView(_ nsView: NSVisualEffectView, context: Context) {}
}

private struct SidebarRoot: View {
    @ObservedObject var model: SidebarModel

    private func heading(_ text: String) -> some View {
        Text(text).font(.system(size: 10, weight: .semibold)).foregroundStyle(.secondary)
            .frame(maxWidth: .infinity, alignment: .leading).padding(.horizontal, 6)
    }
    private var moduleList: some View {
        ScrollViewReader { proxy in
            ScrollView(.vertical) {
                if model.state.modules.isEmpty {
                    Text(model.state.emptyModules).font(.system(size: 11)).foregroundStyle(.secondary)
                        .frame(maxWidth: .infinity, alignment: .leading).padding(.horizontal, 6)
                } else {
                    GlassSelectionGroup(model: model, group: "modules")
                }
            }
            .scrollIndicators(.visible)
            .onChange(of: model.reveal) { _, request in
                if !request.action.isEmpty { proxy.scrollTo(request.action, anchor: .center) }
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .layoutPriority(1)
    }
    private var footer: some View {
        VStack(alignment: .leading, spacing: 7) {
            if !model.state.activityHeading.isEmpty {
                heading(model.state.activityHeading)
                Text(model.state.activity).font(.system(size: 11, weight: .medium))
                    .foregroundStyle(.primary).lineLimit(2)
                    .frame(maxWidth: .infinity, alignment: .leading).padding(.horizontal, 6)
            }
            if model.state.showProgress {
                Group {
                    if model.state.progress < 0 { ProgressView() }
                    else { ProgressView(value: min(1, max(0, model.state.progress))) }
                }
                .progressViewStyle(.linear).controlSize(.small)
                .padding(.horizontal, 6)
            }
            if model.state.fijiAvailable {
                Button { model.dispatch("showFiji") } label: {
                    Label(model.state.showFijiLabel, systemImage: "arrow.up.forward.app")
                        .frame(maxWidth: .infinity).font(.system(size: 11))
                }
                .buttonStyle(.glass).controlSize(.small)
                .accessibilityIdentifier("showFiji")
            }
            if model.state.busy {
                Button { model.dispatch("cancelAnalysis") } label: {
                    Label(model.state.cancelLabel, systemImage: "stop.circle")
                        .frame(maxWidth: .infinity).font(.system(size: 11))
                }
                .buttonStyle(.glass).controlSize(.small)
                .disabled(model.state.cancelling)
                .accessibilityIdentifier("cancelAnalysis")
            }
            HStack(spacing: 6) {
                Text(model.state.version)
                Spacer(minLength: 0)
                Text("©2026 Dingcheng Wang")
            }
            .font(.system(size: 11)).foregroundStyle(.secondary)
            .lineLimit(1).fixedSize(horizontal: false, vertical: true)
            .padding(.horizontal, 6).padding(.top, 2)
        }
        .fixedSize(horizontal: false, vertical: true)
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 9) {
                if let icon = model.applicationIcon {
                    Image(nsImage: icon).resizable().frame(width: 30, height: 30).accessibilityHidden(true)
                }
                Text("Project Leap").font(.system(size: 14, weight: .semibold)).lineLimit(1)
            }
            .padding(.horizontal, 4).padding(.vertical, 8)
            VStack(alignment: .leading, spacing: 8) {
                heading(model.state.workspaceHeading)
                GlassSelectionGroup(model: model, group: "navigation")
            }
            VStack(alignment: .leading, spacing: 8) {
                heading(model.state.modulesHeading)
                moduleList
            }
            .frame(maxHeight: .infinity, alignment: .topLeading)
            footer
        }
        .padding(12)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .background(SidebarMaterial().clipShape(.rect(cornerRadius: 18)))
        .padding(8)
        .onExitCommand { model.cancelDrags() }
    }
}

@MainActor private final class AccessibilityAttachment {
    weak var window: NSWindow?
    weak var qtView: NSView?
    let windowChildren: [Any]?
    let qtElement: Bool
    let qtRole: NSAccessibility.Role?
    let qtLabel: String?
    let sidebarParent: Any?
    init(window: NSWindow, qtView: NSView, sidebar: NSView) {
        self.window = window
        self.qtView = qtView
        windowChildren = window.accessibilityChildren()
        qtElement = qtView.isAccessibilityElement()
        qtRole = qtView.accessibilityRole()
        qtLabel = qtView.accessibilityLabel()
        sidebarParent = sidebar.accessibilityParent()
    }
}

@MainActor private final class LeapHostingView: NSHostingView<SidebarRoot> {
    let model: SidebarModel
    var attachment: AccessibilityAttachment?
    private var keyMonitor: Any?

    init(model: SidebarModel) {
        self.model = model
        super.init(rootView: SidebarRoot(model: model))
        frame = NSRect(x: 0, y: 0, width: 236, height: 820)
        sizingOptions = []
        keyMonitor = NSEvent.addLocalMonitorForEvents(matching: .keyDown) { [weak self] event in
            guard let self, event.window === self.window, event.keyCode == 53,
                  !self.model.tracking.isEmpty else { return event }
            self.model.cancelDrags()
            return nil
        }
    }
    required init(rootView: SidebarRoot) { fatalError("Use init(model:)") }
    required init?(coder: NSCoder) { nil }
    override func isAccessibilityElement() -> Bool { true }
    override func accessibilityRole() -> NSAccessibility.Role? { .group }
    override func accessibilityChildren() -> [Any]? {
        var children = super.accessibilityChildren() ?? []
        for key in ["navigation", "modules"] {
            if let group = model.nativeGroups[key], group.window != nil {
                group.setAccessibilityParent(self)
                if !children.contains(where: { ($0 as AnyObject) === group }) { children.append(group) }
            }
        }
        return children
    }
    override func viewWillMove(toWindow newWindow: NSWindow?) {
        if newWindow !== window { model.cancelDrags(); detachAccessibility() }
        super.viewWillMove(toWindow: newWindow)
    }
    override func viewDidMoveToWindow() {
        super.viewDidMoveToWindow()
        guard let window, let qtView = window.contentView,
              qtView !== self, isDescendant(of: qtView),
              NSStringFromClass(type(of: qtView)).contains("QNSView"),
              attachment == nil else { return }
        attachment = AccessibilityAttachment(window: window, qtView: qtView, sidebar: self)
        qtView.setAccessibilityElement(true)
        qtView.setAccessibilityRole(.group)
        refreshAccessibilityLabels()
        setAccessibilityParent(window)
        // Qt's default window children are per-control proxies. Retaining that
        // snapshot leaves hidden-page controls in AX after a StackLayout switch.
        // Expose their live parent instead; preserve native window decorations.
        var children = (window.accessibilityChildren() ?? []).filter {
            !NSStringFromClass(type(of: $0 as AnyObject)).hasPrefix("QMacAccessibilityElement")
        }
        children.removeAll { ($0 as AnyObject) === self }
        if !children.contains(where: { ($0 as AnyObject) === qtView }) { children.append(qtView) }
        children.insert(self, at: 0)
        window.setAccessibilityChildren(children)
        NSAccessibility.post(element: window, notification: .layoutChanged)
    }
    func refreshAccessibilityLabels() {
        setAccessibilityLabel(model.state.navigationAccessibilityLabel)
        attachment?.qtView?.setAccessibilityLabel(model.state.workspaceAccessibilityLabel)
    }
    func detachAccessibility() {
        guard let previous = attachment else { return }
        attachment = nil
        previous.qtView?.setAccessibilityElement(previous.qtElement)
        previous.qtView?.setAccessibilityRole(previous.qtRole)
        previous.qtView?.setAccessibilityLabel(previous.qtLabel)
        previous.window?.setAccessibilityChildren(previous.windowChildren)
        setAccessibilityParent(previous.sidebarParent)
    }
    func close() {
        model.cancelDrags()
        detachAccessibility()
        if let keyMonitor { NSEvent.removeMonitor(keyMonitor); self.keyMonitor = nil }
    }
}

@MainActor private final class SidebarHandle {
    let model: SidebarModel
    let view: LeapHostingView
    init(callback: LeapGlassAction?, context: UnsafeMutableRawPointer?) {
        model = SidebarModel()
        view = LeapHostingView(model: model)
        model.onAction = { action in action.withCString { callback?($0, context) } }
    }
    func close() {
        view.close()
        model.nativeGroups.values.forEach { $0.close() }
        model.nativeGroups.removeAll()
        model.onAction = nil
        ApplicationIconState.shared.models.remove(model)
    }
}

@_cdecl("leap_glass_available")
public func leapGlassAvailable() -> Int32 { if #available(macOS 27, *) { return 1 }; return 0 }

@MainActor @_cdecl("leap_glass_create")
public func leapGlassCreate(_ callback: LeapGlassAction?, _ context: UnsafeMutableRawPointer?) -> UnsafeMutableRawPointer? {
    guard Thread.isMainThread, leapGlassAvailable() != 0 else { return nil }
    return Unmanaged.passRetained(SidebarHandle(callback: callback, context: context)).toOpaque()
}

@MainActor @_cdecl("leap_glass_view")
public func leapGlassView(_ handle: UnsafeMutableRawPointer?) -> UInt {
    guard Thread.isMainThread, let handle else { return 0 }
    let view = Unmanaged<SidebarHandle>.fromOpaque(handle).takeUnretainedValue().view
    return UInt(bitPattern: Unmanaged.passUnretained(view).toOpaque())
}

@MainActor @_cdecl("leap_glass_update")
public func leapGlassUpdate(_ handle: UnsafeMutableRawPointer?, _ json: UnsafePointer<CChar>?) -> Int32 {
    guard Thread.isMainThread, let handle, let json,
          let string = String(validatingCString: json), let data = string.data(using: .utf8),
          let state = try? JSONDecoder().decode(SidebarState.self, from: data) else { return 0 }
    let host = Unmanaged<SidebarHandle>.fromOpaque(handle).takeUnretainedValue()
    guard host.model.apply(state) else { return 0 }
    host.view.refreshAccessibilityLabels()
    return 1
}

@MainActor @_cdecl("leap_glass_destroy")
public func leapGlassDestroy(_ handle: UnsafeMutableRawPointer?) {
    guard Thread.isMainThread, let handle else { return }
    let retained = Unmanaged<SidebarHandle>.fromOpaque(handle)
    retained.takeUnretainedValue().close()
    retained.release()
}


private struct AgeState: Decodable {
    let selected: String
    let labels: [String]
    let enabled: [Bool]
    let accessibilityLabel: String
}
@MainActor private final class AgeHandle {
    let view = NativeSelectionView(horizontal: true)
    init(callback: LeapGlassAction?, context: UnsafeMutableRawPointer?) {
        view.frame = NSRect(x: 0, y: 0, width: 360, height: 48)
        view.onRequest = { action in action.withCString { callback?($0, context) } }
    }
    func apply(_ state: AgeState) -> Bool {
        let keys = ["auto", "mature", "neonatal"]
        guard keys.contains(state.selected), state.labels.count == 3, state.enabled.count == 3 else { return false }
        view.apply(choices: keys.indices.map { NativeChoice(id: "age:"+keys[$0], title: state.labels[$0], enabled: state.enabled[$0]) },
                   selected: "age:"+state.selected, label: state.accessibilityLabel)
        return true
    }
    func close() { view.close() }
}
@MainActor @_cdecl("leap_glass_age_create")
public func leapGlassAgeCreate(_ callback: LeapGlassAction?, _ context: UnsafeMutableRawPointer?) -> UnsafeMutableRawPointer? {
    guard Thread.isMainThread, leapGlassAvailable() != 0 else { return nil }
    return Unmanaged.passRetained(AgeHandle(callback: callback, context: context)).toOpaque()
}
@MainActor @_cdecl("leap_glass_age_view")
public func leapGlassAgeView(_ handle: UnsafeMutableRawPointer?) -> UInt {
    guard Thread.isMainThread, let handle else { return 0 }
    return UInt(bitPattern: Unmanaged.passUnretained(Unmanaged<AgeHandle>.fromOpaque(handle).takeUnretainedValue().view).toOpaque())
}
@MainActor @_cdecl("leap_glass_age_update")
public func leapGlassAgeUpdate(_ handle: UnsafeMutableRawPointer?, _ json: UnsafePointer<CChar>?) -> Int32 {
    guard Thread.isMainThread, let handle, let json, let string = String(validatingCString: json),
          let data = string.data(using: .utf8), let state = try? JSONDecoder().decode(AgeState.self, from: data) else { return 0 }
    return Unmanaged<AgeHandle>.fromOpaque(handle).takeUnretainedValue().apply(state) ? 1 : 0
}
@MainActor @_cdecl("leap_glass_age_destroy")
public func leapGlassAgeDestroy(_ handle: UnsafeMutableRawPointer?) {
    guard Thread.isMainThread, let handle else { return }
    let retained = Unmanaged<AgeHandle>.fromOpaque(handle)
    retained.takeUnretainedValue().close(); retained.release()
}
