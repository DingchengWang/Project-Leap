import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Surface {
    id: run
    required property var controller
    property var details: ({})
    property bool history: !!details.history
    property bool showTitle: true
    property bool displayActive: visible
    readonly property bool hasDetails: !!details.available || (!history && controller.logText.length > 0)
    readonly property var resources: details.resources || ({})
    readonly property var cpuCores: resources.cpu_cores || []
    readonly property var cpuCoreTypes: resources.cpu_core_types || []
    readonly property bool logActive: !history && displayActive && hasDetails && !details.loading
    padding: 18
    spacing: 14

    function known(value) { return value !== null && value !== undefined && isFinite(Number(value)) }
    function percent(value) { return known(value) ? Math.round(Number(value)) + "%" : "—" }
    function temperature(value) { return known(value) ? Number(value).toFixed(1) + " °C" : "—" }
    function coreLabel(index) {
        var type = cpuCoreTypes[index]
        return type === "E" ? qsTr("能效核心 %1").arg(index + 1) : type === "P" ? qsTr("性能核心 %1").arg(index + 1) : qsTr("核心 %1").arg(index + 1)
    }
    function bytes(value) { return known(value) ? (Number(value) / 1073741824).toFixed(1) + " GiB" : "—" }
    function capacity(used, total) { return bytes(used) + " / " + bytes(total) }
    function duration(value) {
        if (!known(value)) return "—"
        var seconds = Math.max(0, Math.floor(value))
        var minutes = Math.floor(seconds / 60)
        return minutes > 0 ? qsTr("%1 分 %2 秒").arg(minutes).arg(seconds % 60) : qsTr("%1 秒").arg(seconds)
    }

    Text { visible: run.showTitle; text: qsTr("运行细节"); color: Theme.text; font.pixelSize: 15; font.weight: Font.DemiBold; Layout.fillWidth: true }
    Text {
        objectName: "runDetailsEmpty"
        visible: !run.hasDetails || !!run.details.loading
        text: run.details.loading ? qsTr("正在载入运行细节…") : run.history ? qsTr("此任务没有已保存的运行细节。") : qsTr("运行时会显示程序内部进程")
        color: Theme.textSecondary; font.pixelSize: 12; Layout.fillWidth: true; wrapMode: Text.WordWrap
    }
    ColumnLayout {
        objectName: "historySummary"
        visible: run.history && run.hasDetails && !run.details.loading
        Layout.fillWidth: true
        spacing: 16
        Repeater {
            model: run.history ? run.details.summarySections || [] : []
            delegate: ColumnLayout {
                id: summarySection
                required property var modelData
                required property int index
                objectName: "historySummarySection_" + index
                Layout.fillWidth: true
                spacing: 6
                Text { objectName: "historySummaryTitle_" + summarySection.index; text: summarySection.modelData.title; color: Theme.text; font.pixelSize: 13; font.weight: Font.DemiBold; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                Repeater {
                    model: summarySection.modelData.rows || []
                    delegate: Text {
                        required property var modelData
                        required property int index
                        objectName: "historySummaryRow_" + summarySection.index + "_" + index
                        text: modelData.label + ": " + modelData.value
                        color: Theme.textSecondary
                        font.pixelSize: 12
                        Layout.fillWidth: true
                        wrapMode: Text.WrapAnywhere
                    }
                }
            }
        }
    }
    ColumnLayout {
        visible: !run.history && run.hasDetails && !run.details.loading
        Layout.fillWidth: true
        spacing: 14
        RowLayout {
            Layout.fillWidth: true
            spacing: 16
            Text { objectName: "runDetailsStage"; text: run.details.stage || "—"; color: Theme.text; font.pixelSize: 13; Layout.fillWidth: true; wrapMode: Text.WordWrap }
            Text { objectName: "runDetailsElapsed"; text: qsTr("已用时 %1").arg(run.duration(run.details.elapsedSeconds)); color: Theme.textSecondary; font.pixelSize: 12 }
        }
        Text { visible: !!run.details.elapsedIncludesReview; text: qsTr("已用时包含人工审核等待。"); color: Theme.textMuted; font.pixelSize: 11; Layout.fillWidth: true; wrapMode: Text.WordWrap }
        Text { visible: run.resources.mode === "stopped"; text: qsTr("系统资源 · 已停止采样"); color: Theme.textSecondary; font.pixelSize: 11; Layout.fillWidth: true }
        GridLayout {
            objectName: "runResourceGrid"
            Layout.fillWidth: true
            columns: width >= 680 ? 3 : 1
            columnSpacing: 18
            rowSpacing: 12
            ColumnLayout {
                Layout.fillWidth: true
                Layout.preferredWidth: 1
                Layout.alignment: Qt.AlignTop
                spacing: 7
                RowLayout {
                    Layout.fillWidth: true
                    Text { text: "CPU"; color: Theme.textSecondary; font.pixelSize: 11; Layout.fillWidth: true }
                    Text { objectName: "runCpuTotal"; text: run.percent(run.resources.cpu_percent); color: Theme.text; font.pixelSize: 12 }
                }
                Rectangle {
                    Layout.fillWidth: true; Layout.preferredHeight: 5; radius: 2; color: Theme.border
                    Rectangle { width: parent.width * (run.known(run.resources.cpu_percent) ? Math.max(0, Math.min(100, run.resources.cpu_percent)) / 100 : 0); height: parent.height; radius: 2; color: Theme.accent }
                }
                Flow {
                    objectName: "runCpuCoreBars"
                    Layout.fillWidth: true
                    spacing: 3
                    Repeater {
                        model: run.cpuCores.length
                        delegate: Rectangle {
                            required property int index
                            readonly property var coreSample: run.cpuCores[index]
                            objectName: "runCpuCore_" + index
                            width: 12; height: 22; radius: 2; color: Theme.border
                            Rectangle { objectName: "runCpuCoreFill_" + index; anchors.bottom: parent.bottom; width: parent.width; height: parent.height * (run.known(coreSample) ? Math.max(0, Math.min(100, coreSample)) / 100 : 0); radius: 2; color: run.cpuCoreTypes[index] === "E" ? Theme.success : Theme.accent }
                            HoverHandler { id: coreHover }
                            ToolTip.visible: coreHover.hovered
                            ToolTip.text: qsTr("%1：%2").arg(run.coreLabel(index)).arg(run.percent(coreSample))
                            Accessible.name: ToolTip.text
                        }
                    }
                }
                Text {
                    objectName: "runCpuTemperature"
                    text: qsTr("平均温度 %1").arg(run.temperature(run.resources.cpu_temperature_c))
                    color: Theme.textMuted; font.pixelSize: 11; Layout.fillWidth: true
                    HoverHandler { id: cpuTemperatureHover }
                    ToolTip.visible: cpuTemperatureHover.hovered
                    ToolTip.text: qsTr("CPU 有效温度传感器的平均值。")
                }
            }
            ColumnLayout {
                Layout.fillWidth: true
                Layout.preferredWidth: 1
                Layout.alignment: Qt.AlignTop
                spacing: 7
                RowLayout {
                    Layout.fillWidth: true
                    Text { text: "GPU"; color: Theme.textSecondary; font.pixelSize: 11; Layout.fillWidth: true }
                    Text { objectName: "runGpuTotal"; text: run.percent(run.resources.gpu_percent); color: Theme.text; font.pixelSize: 12 }
                }
                Rectangle {
                    Layout.fillWidth: true; Layout.preferredHeight: 5; radius: 2; color: Theme.border
                    Rectangle { width: parent.width * (run.known(run.resources.gpu_percent) ? Math.max(0, Math.min(100, run.resources.gpu_percent)) / 100 : 0); height: parent.height; radius: 2; color: Theme.accent }
                }
                Text {
                    objectName: "runGpuCores"
                    text: qsTr("%1 个核心 · %2 组").arg(run.known(run.resources.gpu_core_count) ? run.resources.gpu_core_count : "—").arg(run.known(run.resources.gpu_group_count) ? run.resources.gpu_group_count : "—")
                    color: Theme.textMuted; font.pixelSize: 11; Layout.fillWidth: true; wrapMode: Text.WordWrap
                    HoverHandler { id: gpuGroupsHover }
                    ToolTip.visible: gpuGroupsHover.hovered
                    ToolTip.text: qsTr("组数为驱动报告的 mGPU 分组；利用率为整个 GPU 的读数。")
                }
                Text {
                    objectName: "runGpuTemperature"
                    text: qsTr("平均温度 %1").arg(run.temperature(run.resources.gpu_temperature_c))
                    color: Theme.textMuted; font.pixelSize: 11; Layout.fillWidth: true
                    HoverHandler { id: gpuTemperatureHover }
                    ToolTip.visible: gpuTemperatureHover.hovered
                    ToolTip.text: qsTr("GPU 有效温度传感器的平均值。")
                }
            }
            ColumnLayout {
                Layout.fillWidth: true
                Layout.preferredWidth: 1
                Layout.alignment: Qt.AlignTop
                spacing: 7
                RowLayout {
                    Layout.fillWidth: true
                    Text { text: qsTr("内存"); color: Theme.textSecondary; font.pixelSize: 11; Layout.fillWidth: true }
                    Text { objectName: "runMemory"; text: run.capacity(run.resources.memory_used_bytes, run.resources.memory_total_bytes); color: Theme.text; font.pixelSize: 12 }
                }
                Rectangle {
                    Layout.fillWidth: true; Layout.preferredHeight: 5; radius: 2; color: Theme.border
                    Rectangle { width: parent.width * (run.known(run.resources.memory_used_bytes) && run.resources.memory_total_bytes > 0 ? Math.max(0, Math.min(1, run.resources.memory_used_bytes / run.resources.memory_total_bytes)) : 0); height: parent.height; radius: 2; color: Theme.accent }
                }
                Text { objectName: "runSwap"; text: "SWAP  " + run.bytes(run.resources.swap_used_bytes); color: Theme.textMuted; font.pixelSize: 11; Layout.fillWidth: true; wrapMode: Text.WordWrap }
            }
        }
        Text { visible: !!run.details.logTruncated || !!run.details.detailsTruncated; text: qsTr("运行记录较长，此处仅显示部分内容。"); color: Theme.textMuted; font.pixelSize: 11; Layout.fillWidth: true; wrapMode: Text.WordWrap }
        RowLayout {
            Layout.fillWidth: true
            Text { text: qsTr("运行日志"); color: Theme.textSecondary; font.pixelSize: 12; Layout.fillWidth: true }
            ActionButton { objectName: "runLogFollowLatest"; text: qsTr("跟随最新输出"); compact: true; kind: "subtle"; enabled: !logFlick.followLatest; onClicked: logFlick.followTail() }
        }
        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 190
            radius: 7
            color: Theme.surfaceAlt
            Flickable {
                id: logFlick
                objectName: "logFlick"
                anchors.fill: parent
                anchors.margins: 10
                clip: true
                contentWidth: width
                contentHeight: logText.height
                boundsBehavior: Flickable.StopAtBounds
                flickableDirection: Flickable.VerticalFlick
                ScrollBar.vertical: ScrollBar { }
                WheelHandler {
                    target: null
                    acceptedDevices: PointerDevice.Mouse | PointerDevice.TouchPad
                    onWheel: function(event) {
                        var delta = event.pixelDelta.y !== 0 ? event.pixelDelta.y : event.angleDelta.y / 120 * 48
                        var nextY = Math.max(0, Math.min(logFlick.contentHeight - logFlick.height, logFlick.contentY - delta))
                        event.accepted = nextY !== logFlick.contentY
                        if (delta > 0) logFlick.followLatest = false
                        logFlick.contentY = nextY
                    }
                }
                TextEdit {
                    id: logText
                    objectName: "logText"
                    width: logFlick.width - 12
                    height: Math.max(logFlick.height, implicitHeight)
                    readOnly: true
                    selectByMouse: true
                    wrapMode: TextEdit.WrapAnywhere
                    textFormat: TextEdit.PlainText
                    font.family: "Menlo"
                    font.pixelSize: 11
                    color: Theme.textSecondary
                    Accessible.name: qsTr("运行日志")
                }
                property bool dirty: true
                property bool followLatest: true
                property bool pendingLogUpdate: false
                property bool changingPosition: false
                property bool pendingFollow: true
                property real pendingY: 0
                onContentYChanged: {
                    if (!pendingLogUpdate && !changingPosition)
                        followLatest = contentY >= Math.max(0, contentHeight - height) - 5
                }
                function followTail() {
                    followLatest = true
                    if (pendingLogUpdate) pendingFollow = true
                    changingPosition = true
                    contentY = Math.max(0, contentHeight - height)
                    changingPosition = false
                }
                function rememberLogPosition() {
                    if (pendingLogUpdate) return
                    pendingFollow = followLatest
                    pendingY = contentY
                    pendingLogUpdate = true
                }
                function finishLogUpdate() {
                    changingPosition = true
                    contentY = pendingFollow ? Math.max(0, contentHeight - height) : Math.max(0, Math.min(pendingY, contentHeight - height))
                    changingPosition = false
                    followLatest = pendingFollow
                    pendingLogUpdate = false
                }
                function synchronize() {
                    if (!run.logActive || !dirty) return
                    rememberLogPosition()
                    var value = run.controller.logText
                    var anchorPosition = logText.positionAt(0, pendingY + 3)
                    var anchor = logText.text.substring(anchorPosition, anchorPosition + 80)
                    var anchorOffset = logText.positionToRectangle(anchorPosition).y - pendingY
                    logText.text = value
                    if (!pendingFollow && anchor.length > 0) {
                        var newPosition = value.indexOf(anchor)
                        if (newPosition >= 0) pendingY = logText.positionToRectangle(newPosition).y - anchorOffset
                    }
                    dirty = false
                    Qt.callLater(finishLogUpdate)
                }
                function updateLog(value, reset) {
                    if (reset) {
                        followLatest = true
                        if (pendingLogUpdate) pendingFollow = true
                    }
                    if (!run.logActive) { dirty = true; return }
                    // The backend already contains this event. A hidden view must
                    // synchronize once before accepting any further incremental text.
                    if (dirty) { synchronize(); return }
                    if (!reset && logText.text === run.controller.logText) return
                    rememberLogPosition()
                    if (reset) logText.text = value
                    else logText.insert(logText.length, value)
                    Qt.callLater(finishLogUpdate)
                }
                function trimLog(characters) {
                    if (!run.logActive) { dirty = true; return }
                    if (dirty) { synchronize(); return }
                    rememberLogPosition()
                    var previousHeight = logText.implicitHeight
                    logText.remove(0, characters)
                    pendingY = Math.max(0, pendingY - Math.max(0, previousHeight - logText.implicitHeight))
                    Qt.callLater(finishLogUpdate)
                }
                Connections {
                    target: run.history ? null : run.controller
                    function onLogAppended(value) { logFlick.updateLog(value, false) }
                    function onLogReset(value) { logFlick.updateLog(value, true) }
                    function onLogTrimmed(characters) { logFlick.trimLog(characters) }
                }
            }
        }
    }
    onLogActiveChanged: if (logActive) Qt.callLater(logFlick.synchronize)
    Component.onCompleted: Qt.callLater(logFlick.synchronize)
}
