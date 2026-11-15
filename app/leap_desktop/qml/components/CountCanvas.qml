import QtQuick
import QtQuick.Controls

FocusScope {
    id: canvas
    required property var editor
    property bool interactionEnabled: true
    enabled: interactionEnabled
    objectName: "countCanvas"
    implicitHeight: 440
    clip: true
    activeFocusOnTab: true
    readonly property real fitScale: Math.min((width - 24) / Math.max(1, editor.width), (height - 24) / Math.max(1, editor.height))
    readonly property real zoom: editor.viewZoom
    readonly property real imageScale: Math.max(0.00001, fitScale * zoom)
    readonly property real panX: editor.viewPanX
    readonly property real panY: editor.viewPanY
    readonly property real imageLeft: (width - editor.width * imageScale) / 2 + panX
    readonly property real imageTop: (height - editor.height * imageScale) / 2 + panY
    property bool spaceHeld: false
    property var labels: []
    property var renderPoints: []
    property int activeBuffer: 0
    readonly property var activeImage: activeBuffer === 0 ? frameA : frameB
    property string queuedFrameToken: ""
    property bool committingFrame: false
    property int displayOwner: 0
    Accessible.role: Accessible.Canvas
    Accessible.name: qsTr("细胞计数编辑画布")
    Accessible.description: qsTr("单击 High Likelihood 加入 Astrocyte 计数；Unrecognized 和 Neurons 需选择后加入计数。Command 多选；A 添加，V 选择，M 合并，Delete 删除。捏合缩放，双指滚动或拖动平移，0 适应窗口。")
    function refreshLabels() {
        renderPoints = editor.available ? editor.renderingPoints() : []
        labels = editor.available ? editor.labelsForScale(imageScale) : []
        overlay.requestPaint()
    }
    function reportDisplayVisibility(active) {
        if (displayOwner === 0) return
        var targetEditor = editor
        var owner = displayOwner
        Qt.callLater(function() { if (targetEditor) targetEditor.setDisplayActive(active, owner) })
    }
    function bufferReady(buffer) {
        if (buffer.status !== Image.Ready) return
        var accepted = false
        if (buffer.frameToken !== "") {
            committingFrame = true
            accepted = editor.commitFrame(String(buffer.source), buffer.frameToken)
            committingFrame = false
        }
        if (accepted || (editor.frameReady && String(buffer.source) === editor.appliedImageUrl)) {
            activeBuffer = buffer.bufferIndex
            buffer.frameToken = ""
            queuedFrameToken = ""
            refreshLabels()
        }
    }
    function syncFrame() {
        if (committingFrame) return
        if (!editor.available) {
            frameA.source = ""; frameB.source = ""
            frameA.frameToken = ""; frameB.frameToken = ""
            activeBuffer = 0; queuedFrameToken = ""
            return
        }
        if (!editor.displayActive) {
            var inactive = activeBuffer === 0 ? frameB : frameA
            inactive.frameToken = ""
            inactive.source = ""
            queuedFrameToken = ""
            return
        }
        if (String(activeImage.source) === "" && editor.appliedImageUrl !== "") {
            activeImage.frameToken = ""
            activeImage.source = editor.appliedImageUrl
        }
        var token = editor.pendingFrameToken
        if (token === "" || token === queuedFrameToken) return
        queuedFrameToken = token
        var url = editor.imageUrl
        if (String(activeImage.source) === url && activeImage.status === Image.Ready) {
            activeImage.frameToken = token
            bufferReady(activeImage)
            return
        }
        var target = activeBuffer === 0 ? frameB : frameA
        target.frameToken = token
        if (String(target.source) === url && target.status === Image.Ready) bufferReady(target)
        else target.source = url
    }
    function fitImage() { editor.setView(1, 0, 0); forceActiveFocus() }
    function zoomAt(factor, x, y) {
        var oldScale = imageScale
        var ix = (x - imageLeft) / oldScale
        var iy = (y - imageTop) / oldScale
        var nextZoom = Math.max(0.25, Math.min(24, zoom * factor))
        var nextScale = Math.max(0.00001, fitScale * nextZoom)
        var nextPanX = x - (width - editor.width * nextScale) / 2 - ix * nextScale
        var nextPanY = y - (height - editor.height * nextScale) / 2 - iy * nextScale
        editor.setView(nextZoom, nextPanX, nextPanY)
    }
    function imagePoint(x, y) { return Qt.point((x - imageLeft) / imageScale, (y - imageTop) / imageScale) }
    function inside(point) { return point.x >= 0 && point.y >= 0 && point.x < editor.width && point.y < editor.height }
    function pointAt(x, y) {
        var candidates = []
        // Rendering snapshots are detached from QObject sequence properties.
        var points = renderPoints
        var selectedIds = editor.selectedIds
        for (var i = 0; i < points.length; ++i) {
            var p = points[i]
            var dx = imageLeft + p.x * imageScale - x
            var dy = imageTop + p.y * imageScale - y
            if (dx * dx + dy * dy <= 81) candidates.push(p.id)
        }
        if (!candidates.length && editor.showLabels) {
            for (var j = 0; j < labels.length; ++j) {
                var label = labels[j]
                var lx = imageLeft + label.left * imageScale
                var ly = imageTop + label.top * imageScale
                if (x >= lx && y >= ly && x <= lx + label.width * imageScale && y <= ly + label.height * imageScale)
                    candidates.push(label.id)
            }
        }
        var chosen = candidates.length ? candidates[0] : ""
        if (candidates.length > 1) {
            var selectedKey = selectedIds.length === 1 ? selectedIds[0] : ""
            var previous = candidates.indexOf(selectedKey)
            if (previous >= 0) chosen = candidates[(previous + 1) % candidates.length]
        }
        return chosen
    }
    function selectAt(x, y, additive) { editor.activatePoint(pointAt(x, y), additive) }
    onImageScaleChanged: refreshLabels()
    onImageLeftChanged: overlay.requestPaint()
    onImageTopChanged: overlay.requestPaint()
    onActiveFocusChanged: if (!activeFocus) { spaceHeld = false }
    onVisibleChanged: reportDisplayVisibility(visible)
    Component.onCompleted: { displayOwner = editor.registerDisplay(); reportDisplayVisibility(visible); syncFrame(); refreshLabels() }
    Component.onDestruction: reportDisplayVisibility(false)
    Connections {
        target: canvas.editor
        function onChanged() {
            if (!canvas.committingFrame) canvas.syncFrame()
        }
        function onOverlayChanged() {
            pointer.hoverHint = ""
            if (pointer.containsMouse && !pointer.pressed) hintHover.restart()
            canvas.refreshLabels()
        }
    }
    Rectangle { anchors.fill: parent; color: "#111722"; radius: 8; border.width: canvas.activeFocus ? 2 : 1; border.color: canvas.activeFocus ? "#6E9CFF" : "#293447" }
    Image {
        id: frameA
        readonly property int bufferIndex: 0
        property string frameToken: ""
        objectName: canvas.activeBuffer === 0 ? "countReviewImage" : "countReviewImageBack"
        visible: canvas.activeBuffer === 0 && canvas.editor.frameReady && status === Image.Ready
                 && String(source) === canvas.editor.appliedImageUrl
        x: canvas.imageLeft; y: canvas.imageTop
        width: canvas.editor.width * canvas.imageScale
        height: canvas.editor.height * canvas.imageScale
        asynchronous: true
        cache: false
        fillMode: Image.Stretch
        smooth: canvas.imageScale < 2
        onStatusChanged: {
            if (status === Image.Ready) canvas.bufferReady(frameA)
            else if (status === Image.Error) canvas.editor.imageFailed(String(source), frameToken)
        }
        Accessible.name: canvas.editor.displayedZIndex < 0 ? qsTr("全 Z 最大强度投影") : qsTr("第 %1 层").arg(canvas.editor.displayedZIndex + 1)
    }
    Image {
        id: frameB
        readonly property int bufferIndex: 1
        property string frameToken: ""
        objectName: canvas.activeBuffer === 1 ? "countReviewImage" : "countReviewImageBack"
        visible: canvas.activeBuffer === 1 && canvas.editor.frameReady && status === Image.Ready
                 && String(source) === canvas.editor.appliedImageUrl
        x: canvas.imageLeft; y: canvas.imageTop
        width: canvas.editor.width * canvas.imageScale
        height: canvas.editor.height * canvas.imageScale
        asynchronous: true
        cache: false
        fillMode: Image.Stretch
        smooth: canvas.imageScale < 2
        onStatusChanged: {
            if (status === Image.Ready) canvas.bufferReady(frameB)
            else if (status === Image.Error) canvas.editor.imageFailed(String(source), frameToken)
        }
        Accessible.name: canvas.editor.displayedZIndex < 0 ? qsTr("全 Z 最大强度投影") : qsTr("第 %1 层").arg(canvas.editor.displayedZIndex + 1)
    }
    Canvas {
        id: overlay
        objectName: "countPointOverlay"
        visible: canvas.editor.frameReady && canvas.activeImage.status === Image.Ready
                 && String(canvas.activeImage.source) === canvas.editor.appliedImageUrl
        anchors.fill: parent
        onPaint: {
            var context = getContext("2d")
            context.reset()
            context.lineWidth = 1.2
            var points = canvas.renderPoints
            var labels = canvas.labels
            var left = canvas.imageLeft, top = canvas.imageTop, scale = canvas.imageScale
            for (var i = 0; i < points.length; ++i) {
                var p = points[i]
                var px = left + p.x * scale
                var py = top + p.y * scale
                var selected = p.selected === true
                context.beginPath()
                if (p.status === "excluded") context.rect(px - 3.5, py - 3.5, 7, 7)
                else if (p.status === "review" && p.review_hint === "likely_astrocyte") {
                    context.moveTo(px, py - 6); context.lineTo(px + 6, py)
                    context.lineTo(px, py + 6); context.lineTo(px - 6, py); context.closePath()
                } else context.arc(px, py, selected ? 6 : 2.5, 0, 2 * Math.PI)
                context.strokeStyle = selected ? "#FFFFFF" : "#000000"
                context.fillStyle = p.status === "review" ? (p.review_hint === "likely_astrocyte" ? "#E4A43A" : "#F3D76A") : p.status === "excluded" ? "#B18AE3" : "#56C987"
                context.fill(); context.stroke()
            }
            if (canvas.editor.showLabels) {
                context.textBaseline = "alphabetic"
                context.lineJoin = "round"
                for (var k = 0; k < labels.length; ++k) {
                    var glyph = labels[k]
                    var cx = left + (glyph.left + glyph.width / 2) * scale
                    var cy = top + (glyph.top + glyph.height / 2) * scale
                    var radius = (Math.max(glyph.width, glyph.height) - glyph.strokeWidth) * scale / 2
                    context.beginPath(); context.arc(cx, cy, radius, 0, 2 * Math.PI)
                    context.fillStyle = "rgba(0, 0, 0, 0.39)"
                    context.strokeStyle = "#56C987"
                    context.lineWidth = Math.max(1.2, glyph.strokeWidth / 2) * scale
                    context.fill(); context.stroke()
                    context.font = "bold " + (glyph.fontSize * scale) + "px Arial"
                    context.lineWidth = Math.max(1.5, glyph.fontSize * 0.08) * scale
                    context.strokeStyle = "#000000"
                    context.fillStyle = "#56C987"
                    var gx = left + glyph.baselineX * scale
                    var gy = top + glyph.baselineY * scale
                    context.strokeText(String(glyph.text), gx, gy)
                    context.fillText(String(glyph.text), gx, gy)
                }
            }
        }
    }
    MouseArea {
        id: pointer
        objectName: "countCanvasPointer"
        enabled: canvas.interactionEnabled
        anchors.fill: parent
        acceptedButtons: Qt.LeftButton | Qt.MiddleButton
        preventStealing: true
        hoverEnabled: true
        cursorShape: canvas.spaceHeld || panning ? Qt.OpenHandCursor : canvas.editor.mode === "add" ? Qt.CrossCursor : Qt.ArrowCursor
        property bool panning: false
        property real startX: 0
        property real startY: 0
        property real endX: 0
        property real endY: 0
        property real startPanX: 0
        property real startPanY: 0
        property string hoverHint: ""
        ToolTip.visible: hoverHint !== "" && containsMouse && !pressed
        ToolTip.text: hoverHint
        ToolTip.delay: 0
        Timer {
            id: hintHover
            interval: 250
            onTriggered: {
                if (!pointer.containsMouse || pointer.pressed) return
                var points = canvas.renderPoints
                var text = ""
                for (var i = 0; i < points.length; ++i) {
                    var point = points[i]
                    if (point.status !== "review" || point.review_hint !== "likely_astrocyte") continue
                    var dx = canvas.imageLeft + point.x * canvas.imageScale - pointer.mouseX
                    var dy = canvas.imageTop + point.y * canvas.imageScale - pointer.mouseY
                    if (dx * dx + dy * dy <= 100) { text = qsTr("高Astrocyte可能性"); break }
                }
                pointer.hoverHint = text
            }
        }
        onEntered: hintHover.restart()
        onExited: { hintHover.stop(); hoverHint = "" }
        onPressed: function(mouse) {
            hintHover.stop(); hoverHint = ""
            canvas.forceActiveFocus()
            startX = endX = mouse.x; startY = endY = mouse.y
            startPanX = canvas.panX; startPanY = canvas.panY
            panning = canvas.spaceHeld || mouse.button === Qt.MiddleButton
        }
        onPositionChanged: function(mouse) {
            hoverHint = ""
            if (!pressed) { hintHover.restart(); return }
            endX = mouse.x; endY = mouse.y
            if (Math.abs(mouse.x - startX) + Math.abs(mouse.y - startY) > 5) panning = true
            if (panning) canvas.editor.setView(canvas.zoom, startPanX + mouse.x - startX, startPanY + mouse.y - startY)
        }
        onReleased: function(mouse) {
            if (!panning && !canvas.editor.busy && canvas.activeImage.status === Image.Ready) {
                var additive = !!(mouse.modifiers & Qt.ControlModifier)
                var p = canvas.imagePoint(mouse.x, mouse.y)
                if (canvas.editor.mode === "add" && canvas.inside(p)) {
                    var existing = canvas.pointAt(mouse.x, mouse.y)
                    if (existing !== "") canvas.editor.activatePoint(existing, additive)
                    else canvas.editor.addPoint(p.x, p.y)
                }
                else if (canvas.editor.mode === "select") canvas.selectAt(mouse.x, mouse.y, additive)
            }
            panning = false
        }
        onCanceled: { panning = false }
        onWheel: function(wheel) {
            var dx = wheel.pixelDelta.x !== 0 ? wheel.pixelDelta.x : wheel.angleDelta.x / 3
            var dy = wheel.pixelDelta.y !== 0 ? wheel.pixelDelta.y : wheel.angleDelta.y / 3
            canvas.editor.setView(canvas.zoom, canvas.panX + dx, canvas.panY + dy)
            wheel.accepted = true
        }
    }
    PinchHandler {
        id: pinch
        objectName: "countCanvasPinch"
        enabled: canvas.interactionEnabled
        target: null
        rotationAxis.enabled: false
        xAxis.enabled: false
        yAxis.enabled: false
        onScaleChanged: function(delta) { canvas.zoomAt(delta, centroid.position.x, centroid.position.y) }
    }
    Keys.priority: Keys.AfterItem
    Keys.enabled: canvas.interactionEnabled
    Keys.onPressed: function(event) {
        if (event.key === Qt.Key_Space) { spaceHeld = true; event.accepted = true }
        else if (!editor.busy && (event.key === Qt.Key_Delete || event.key === Qt.Key_Backspace)) { editor.deleteSelected(); event.accepted = true }
        else if (!editor.busy && event.key === Qt.Key_A && event.modifiers === Qt.NoModifier) { editor.setMode("add"); event.accepted = true }
        else if (!editor.busy && editor.canMerge && event.key === Qt.Key_M && event.modifiers === Qt.NoModifier) { editor.mergeSelected(); event.accepted = true }
        else if (event.key === Qt.Key_V && event.modifiers === Qt.NoModifier) { editor.setMode("select"); event.accepted = true }
        else if (event.key === Qt.Key_Plus || event.key === Qt.Key_Equal) { zoomAt(1.25, width / 2, height / 2); event.accepted = true }
        else if (event.key === Qt.Key_Minus) { zoomAt(0.8, width / 2, height / 2); event.accepted = true }
        else if (event.key === Qt.Key_0) { fitImage(); event.accepted = true }
    }
    Keys.onReleased: function(event) {
        if (event.key === Qt.Key_Space) { spaceHeld = false; event.accepted = true }
    }
    Shortcut { sequences: [StandardKey.Undo]; enabled: canvas.interactionEnabled && canvas.visible && canvas.activeFocus && !canvas.editor.busy; onActivated: canvas.editor.undo() }
    Shortcut { sequences: [StandardKey.Redo]; enabled: canvas.interactionEnabled && canvas.visible && canvas.activeFocus && !canvas.editor.busy; onActivated: canvas.editor.redo() }
}
