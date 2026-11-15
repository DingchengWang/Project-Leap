import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Window
import "components" as UI

ApplicationWindow {
    id: window
    objectName: "projectLeapWindow"
    width: 1210
    height: 820
    minimumWidth: 900
    minimumHeight: 620
    visible: true
    title: "Project Leap"
    color: UI.Theme.background
    font.pixelSize: 13
    palette.window: UI.Theme.background
    palette.windowText: UI.Theme.text
    palette.base: UI.Theme.surface
    palette.alternateBase: UI.Theme.surfaceAlt
    palette.text: UI.Theme.text
    palette.button: UI.Theme.surface
    palette.buttonText: UI.Theme.text
    palette.brightText: UI.Theme.white
    palette.light: UI.Theme.surface
    palette.midlight: UI.Theme.border
    palette.mid: UI.Theme.border
    palette.dark: UI.Theme.textMuted
    palette.shadow: UI.Theme.textMuted
    palette.highlight: UI.Theme.accent
    palette.highlightedText: UI.Theme.white
    palette.toolTipBase: UI.Theme.surface
    palette.toolTipText: UI.Theme.text
    palette.link: UI.Theme.accent
    palette.linkVisited: UI.Theme.accent
    palette.placeholderText: UI.Theme.textSecondary
    property var moduleInputPages: ({fluorescence: Qt.resolvedUrl("pages/FluorescenceInput.qml"), counting: Qt.resolvedUrl("pages/CountingInput.qml")})
    onModuleInputPagesChanged: moduleInputLoader.loadInputPage()
    function warnMultiFileDrop() { inputDropDialog.open() }

    readonly property string currentPage: backend.page
    readonly property bool hasImage: !!backend.imageInfo.path
    readonly property bool moduleReady: backend.componentInfo.status === "ready"
    readonly property bool waitingReview: backend.status === "waiting_review" || backend.status === "review_ready"
    readonly property bool working: backend.activeModuleBusy
    readonly property bool editable: !backend.parametersLocked && !backend.maintenanceBusy
    readonly property bool wide: contentArea.width >= 810
    readonly property bool statusFirst: !window.wide && (backend.activeModuleBusy || ["failed", "completed", "cancelled", "rejected", "blocked", "interrupted"].indexOf(backend.status) >= 0)
    readonly property bool needsSeriesSelection: backend.imageInfo.series_count > 1 && !(backend.imageInfo.selected_series >= 0)
    readonly property bool needsTimeSelection: backend.imageInfo.time_count > 1 && !(backend.imageInfo.selected_time >= 0)
    readonly property bool hasSourcePreview: !!backend.imageInfo.thumbnail_url
    readonly property bool metadataReady: window.hasImage && !window.needsSeriesSelection && backend.imageInfo.channel_count > 0
    property bool jobSelectionMode: false
    property var selectedJobIds: []
    readonly property bool jobDeletionEnabled: backend.canDeleteJobs && !backend.maintenanceBusy

    function jobIsSelected(jobId) { return selectedJobIds.indexOf(jobId) >= 0 }
    function toggleJobSelection(jobId) {
        var ids = selectedJobIds.slice()
        var index = ids.indexOf(jobId)
        if (index >= 0) ids.splice(index, 1)
        else ids.push(jobId)
        selectedJobIds = ids
    }
    function selectAllJobs() {
        selectedJobIds = selectedJobIds.length === backend.jobs.length ? [] : backend.jobs.map(function(job) { return job.id })
    }
    function retainExistingJobSelections() {
        var existingIds = backend.jobs.map(function(job) { return job.id })
        var remaining = selectedJobIds.filter(function(id) { return existingIds.indexOf(id) >= 0 })
        if (remaining.length !== selectedJobIds.length) selectedJobIds = remaining
        if (existingIds.length === 0) jobSelectionMode = false
    }
    onCurrentPageChanged: {
        if (currentPage === "jobs") Qt.callLater(function() { backend.refreshStorageUsage() })
    }
    Connections {
        target: backend
        function onChanged() { window.retainExistingJobSelections() }
        function onDeleteConfirmationRequested(count) {
            deleteJobsDialog.jobCount = count
            skipDeleteConfirmation.checked = false
            deleteJobsDialog.open()
        }
    }

    function sourcePreviewLabel() {
        var info = backend.imageInfo
        var channel = info.preview_channel_index >= 0 ? qsTr("通道%1").arg(info.preview_channel_index + 1) : qsTr("通道未标记")
        var layer = info.preview_z_index >= 0 ? qsTr("Z层%1").arg(info.preview_z_index + 1) : qsTr("Z层未标记")
        return qsTr("%1 · %2 · 显示预览").arg(channel).arg(layer)
    }
    function jobTime(value) { return Qt.formatDateTime(new Date(value), "yyyy-MM-dd hh:mm") }
    function stateText(state, moduleId) {
        if ((state === "waiting_review" || state === "review_ready") && (moduleId || backend.activeModuleId) === "cell-counting") return qsTr("等待计数确认")
        var names = {idle:qsTr("等待导入"), inspecting:qsTr("读取元数据"), ready:qsTr("准备就绪"), importing:qsTr("导入图像"), running:qsTr("正在分析"), waiting_review:qsTr("等待 Fiji 审核"), review_ready:qsTr("等待 Fiji 审核"), completed:qsTr("已完成"), cancelled:qsTr("已取消"), failed:qsTr("执行失败"), rejected:qsTr("引擎已停止"), blocked:qsTr("暂不可用"), cancelling:qsTr("正在取消"), installing:qsTr("正在安装"), repairing:qsTr("正在修复"), not_installed:qsTr("尚未安装"), error:qsTr("需要处理"), damaged:qsTr("需要修复"), interrupted:qsTr("任务中断"), checking:qsTr("正在检查")}
        return names[state] || state || qsTr("等待导入")
    }
    function stateTone(state) {
        if (state === "completed" || state === "ready") return "success"
        if (state === "failed" || state === "error" || state === "damaged") return "error"
        if (state === "waiting_review" || state === "review_ready" || state === "rejected" || state === "blocked" || state === "interrupted") return "warning"
        if (["inspecting", "importing", "running", "installing", "repairing", "cancelling", "checking"].indexOf(state) >= 0) return "active"
        return "neutral"
    }
    function channelColor(role) {
        var colors = { DAPI:"#007AFF", eGFP:"#34C759", GFAP:"#FFD60A", Target:"#FF3B30", KCNN1:"#FF3B30", KCNN2:"#FF3B30", KCNN3:"#FF3B30", KCNJ10:"#FF3B30" }
        return colors[role] || "#B9BFC9"
    }
    function calibrationText() {
        var p = backend.imageInfo.physical_size_um
        if (!p || p.x === undefined || p.x === null || p.y === undefined || p.y === null) return qsTr("未提供")
        var s = Number(p.x).toFixed(3) + " × " + Number(p.y).toFixed(3)
        if (p.z !== undefined && p.z !== null) s += " × " + Number(p.z).toFixed(3)
        return s + " µm"
    }
    function shapeText() {
        var i = backend.imageInfo
        return i.width && i.height ? i.width + " × " + i.height + " px" : "—"
    }

    onClosing: function(close) {
        if (backend.busy || backend.maintenanceBusy) {
            close.accepted = false
            backend.requestQuit()
        }
    }
    Shortcut { objectName: "quitShortcut"; context: Qt.ApplicationShortcut; sequences: [StandardKey.Quit]; onActivated: backend.requestQuit() }
    Shortcut { sequences: [StandardKey.Open]; enabled: window.editable && !backend.busy; onActivated: backend.chooseImage() }
    Shortcut { sequence: "Ctrl+1"; onActivated: backend.setPage("analysis") }
    Shortcut { sequence: "Ctrl+2"; onActivated: backend.setPage("jobs") }
    Shortcut { sequence: "Ctrl+3"; onActivated: backend.setPage("maintenance") }
    Shortcut { sequence: "Ctrl+4"; onActivated: backend.setPage("settings") }

    RowLayout {
        anchors.fill: parent
        spacing: 0

        Item {
            objectName: "sidebar"
            Layout.fillHeight: true
            Layout.preferredWidth: 236
            WindowContainer { anchors.fill: parent; window: nativeStyle.navigationWindow }
        }

        ColumnLayout {
            id: contentArea
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 0
            StackLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                currentIndex: window.currentPage === "jobs" ? 1 : window.currentPage === "maintenance" ? 2 : window.currentPage === "settings" ? 3 : 0

                ScrollView {
                    id: analysisScroll
                    objectName: "analysisScroll"
                    clip: true
                    contentWidth: availableWidth
                    ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                    ColumnLayout {
                        width: analysisScroll.availableWidth
                        spacing: 22
                        Item { Layout.preferredHeight: 8 }
                        ColumnLayout {
                            Layout.fillWidth: true
                            Layout.leftMargin: 30
                            Layout.rightMargin: 30
                            spacing: 7
                            Text { text: backend.activeModuleName; color: UI.Theme.text; font.pixelSize: 28; font.weight: Font.DemiBold; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                        }

                        Loader {
                            id: moduleInputLoader
                            objectName: "moduleInputLoader"
                            property string inputPage: backend.activeModuleInputPage
                            Layout.fillWidth: true
                            Layout.leftMargin: 30
                            Layout.rightMargin: 30
                            Layout.preferredHeight: item ? item.implicitHeight : 0
                            onInputPageChanged: loadInputPage()
                            Component.onCompleted: loadInputPage()
                            function loadInputPage() {
                                var pageSource = window.moduleInputPages[inputPage]
                                if (pageSource) setSource(pageSource, {workspace: window})
                                else source = ""
                            }
                        }
                        UI.Surface {
                            visible: !window.moduleInputPages[backend.activeModuleInputPage] || moduleInputLoader.status === Loader.Error
                            Layout.fillWidth: true
                            Layout.leftMargin: 30
                            Layout.rightMargin: 30
                            Text { text: qsTr("此模块的输入页面尚不可用。"); color: UI.Theme.textSecondary; font.pixelSize: 13; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                        }
                        UI.RunDetails {
                            id: liveRunDetails
                            objectName: "liveRunDetails"
                            controller: backend
                            details: backend.activeRunDetails
                            Layout.fillWidth: true
                            Layout.leftMargin: 30
                            Layout.rightMargin: 30
                            // This pair of cards uses 18 px; retain the page's other gaps.
                            Layout.topMargin: -4
                            displayActive: {
                                // Track layout and scroll changes used by mapToItem below.
                                var scrollY = analysisScroll.contentItem ? analysisScroll.contentItem.contentY : 0
                                var cardY = y
                                var cardHeight = height
                                var viewportHeight = analysisScroll.height
                                if (window.currentPage !== "analysis" || !window.visible || window.visibility === Window.Minimized) return false
                                var top = mapToItem(analysisScroll, 0, 0).y
                                return top < viewportHeight && top + cardHeight > 0
                            }
                            onDisplayActiveChanged: backend.setRunDetailsVisible(displayActive)
                            Component.onCompleted: backend.setRunDetailsVisible(displayActive)
                            Component.onDestruction: backend.setRunDetailsVisible(false)
                        }
                        Item { Layout.preferredHeight: 14 }
                    }
                }

                ScrollView {
                    id: jobsScroll
                    objectName: "jobsScroll"
                    clip: true
                    contentWidth: availableWidth
                    ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                    ColumnLayout {
                        width: jobsScroll.availableWidth
                        spacing: 22
                        Item { Layout.preferredHeight: 8 }
                        ColumnLayout {
                            Layout.fillWidth: true
                            Layout.leftMargin: 30
                            Layout.rightMargin: 30
                            spacing: 7
                            Text { text: qsTr("任务与结果"); color: UI.Theme.text; font.pixelSize: 28; font.weight: Font.DemiBold }
                            Flow {
                                visible: backend.jobs.length > 0
                                Layout.fillWidth: true
                                spacing: 8
                                UI.ActionButton {
                                    objectName: "jobSelectionModeButton"
                                    text: window.jobSelectionMode ? qsTr("完成") : qsTr("多选")
                                    compact: true
                                    enabled: !backend.maintenanceBusy
                                    onClicked: {
                                        window.jobSelectionMode = !window.jobSelectionMode
                                        window.selectedJobIds = []
                                    }
                                }
                                UI.ActionButton {
                                    objectName: "selectAllJobsButton"
                                    visible: window.jobSelectionMode
                                    text: window.selectedJobIds.length === backend.jobs.length ? qsTr("取消全选") : qsTr("全选")
                                    compact: true
                                    enabled: window.jobDeletionEnabled
                                    onClicked: window.selectAllJobs()
                                }
                                UI.ActionButton {
                                    objectName: "deleteSelectedJobsButton"
                                    visible: window.jobSelectionMode
                                    text: qsTr("删除所选")
                                    symbol: "trash"
                                    kind: "danger"
                                    compact: true
                                    enabled: window.jobDeletionEnabled && window.selectedJobIds.length > 0
                                    onClicked: backend.requestDeleteJobs(window.selectedJobIds)
                                }
                                Text {
                                    objectName: "selectedJobsCount"
                                    visible: window.jobSelectionMode
                                    text: qsTr("已选 %1 项").arg(window.selectedJobIds.length)
                                    color: UI.Theme.textSecondary
                                    font.pixelSize: 12
                                    height: 32
                                    verticalAlignment: Text.AlignVCenter
                                }
                                UI.ActionButton {
                                    objectName: "refreshJobStorageButton"
                                    text: backend.storageUsageBusy ? qsTr("正在计算占用…") : qsTr("刷新占用")
                                    symbol: "refresh"
                                    kind: "subtle"
                                    compact: true
                                    enabled: !backend.maintenanceBusy && !backend.storageUsageBusy
                                    onClicked: backend.refreshStorageUsage()
                                }
                            }
                        }
                        UI.Surface {
                            visible: backend.maintenanceMessage.length > 0
                            Layout.fillWidth: true
                            Layout.leftMargin: 30
                            Layout.rightMargin: 30
                            padding: 16
                            color: backend.maintenanceMessageKind === "error" ? UI.Theme.errorSoft : backend.maintenanceMessageKind === "success" ? UI.Theme.successSoft : UI.Theme.accentSoft
                            border.color: UI.Theme.border
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 10
                                UI.Icon { kind: backend.maintenanceMessageKind === "error" ? "warning" : "info"; ink: backend.maintenanceMessageKind === "error" ? UI.Theme.error : UI.Theme.textSecondary; Layout.preferredWidth: 18; Layout.preferredHeight: 18; Layout.alignment: Qt.AlignTop }
                                Text { objectName: "jobsMaintenanceMessage"; text: backend.maintenanceMessage; color: UI.Theme.textSecondary; font.pixelSize: 12; lineHeight: 1.3; wrapMode: Text.Wrap; Layout.fillWidth: true }
                            }
                        }
                        UI.Surface {
                            visible: backend.jobs.length === 0
                            Layout.fillWidth: true
                            Layout.leftMargin: 30
                            Layout.rightMargin: 30
                            padding: 36
                            spacing: 14
                            Rectangle { width: 58; height: 58; radius: 17; color: UI.Theme.accentSoft; Layout.alignment: Qt.AlignHCenter; UI.Icon { anchors.centerIn: parent; width: 27; height: 27; kind: "jobs"; ink: UI.Theme.textMuted } }
                            Text { text: qsTr("还没有分析任务"); color: UI.Theme.textSecondary; font.pixelSize: 16; font.weight: Font.Medium; Layout.alignment: Qt.AlignHCenter }
                            Text { text: qsTr("开始一次分析后，任务记录会显示在这里。"); color: UI.Theme.textSecondary; font.pixelSize: 12; Layout.alignment: Qt.AlignHCenter }
                            UI.ActionButton { text: qsTr("前往分析工作台"); symbol: "arrow"; Layout.alignment: Qt.AlignHCenter; Layout.topMargin: 8; onClicked: backend.setPage("analysis") }
                        }
                        Repeater {
                            model: backend.jobGroups
                            delegate: ColumnLayout {
                                id: jobGroup
                                required property var modelData
                                Layout.fillWidth: true
                                Layout.leftMargin: 30
                                Layout.rightMargin: 30
                                spacing: 12
                                RowLayout {
                                    Layout.fillWidth: true
                                    Text { text: jobGroup.modelData.moduleName; color: UI.Theme.textSecondary; font.pixelSize: 15; font.weight: Font.DemiBold; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                                    UI.StatusBadge { visible: !jobGroup.modelData.installed; text: qsTr("模块已卸载"); tone: "neutral" }
                                    Text { text: qsTr("%1 项").arg(jobGroup.modelData.jobs.length); color: UI.Theme.textSecondary; font.pixelSize: 12 }
                                }
                                Repeater {
                                    model: jobGroup.modelData.jobs
                                    delegate: ColumnLayout {
                                        id: jobEntry
                                        required property var modelData
                                        readonly property bool expanded: backend.selectedJobId === modelData.id
                                        Layout.fillWidth: true
                                        spacing: 8
                                        Rectangle {
                                            Layout.fillWidth: true
                                            implicitHeight: jobRow.implicitHeight + 16
                                            radius: 11
                                            color: jobEntry.expanded ? UI.Theme.accentSoft : jobButton.hovered ? UI.Theme.surfaceHover : UI.Theme.surface
                                            border.width: jobButton.activeFocus ? 2 : 1
                                            border.color: jobButton.activeFocus ? UI.Theme.accent : jobEntry.expanded ? UI.Theme.accent : UI.Theme.border
                                            RowLayout {
                                                id: jobRow
                                                anchors.fill: parent
                                                anchors.margins: 8
                                                spacing: 6
                                                CheckBox {
                                                    objectName: "selectJob_" + jobEntry.modelData.id
                                                    visible: window.jobSelectionMode
                                                    checked: window.jobIsSelected(jobEntry.modelData.id)
                                                    enabled: window.jobDeletionEnabled
                                                    Accessible.name: qsTr("选择分析 %1").arg(jobEntry.modelData.name)
                                                    onClicked: window.toggleJobSelection(jobEntry.modelData.id)
                                                }
                                                Button {
                                                    id: jobButton
                                                    objectName: "taskRow_" + jobEntry.modelData.id
                                                    Layout.fillWidth: true
                                                    implicitHeight: jobContent.implicitHeight + 14
                                                    hoverEnabled: true
                                                    focusPolicy: Qt.StrongFocus
                                                    enabled: !backend.maintenanceBusy
                                                    Accessible.name: jobEntry.modelData.name
                                                    Accessible.description: window.stateText(jobEntry.modelData.status, jobEntry.modelData.module_id)
                                                    Accessible.role: Accessible.Button
                                                    leftPadding: 9; rightPadding: 9; topPadding: 7; bottomPadding: 7
                                                    onClicked: {
                                                        if (window.jobSelectionMode) {
                                                            if (window.jobDeletionEnabled) window.toggleJobSelection(jobEntry.modelData.id)
                                                        } else backend.selectJob(jobEntry.modelData.id)
                                                    }
                                                    background: Item { }
                                                    contentItem: RowLayout {
                                                        id: jobContent
                                                        spacing: 13
                                                        UI.Icon { kind: "image"; ink: UI.Theme.textMuted; Layout.preferredWidth: 20; Layout.preferredHeight: 20 }
                                                        ColumnLayout {
                                                            Layout.fillWidth: true
                                                            spacing: 5
                                                            Text { text: jobEntry.modelData.name; color: UI.Theme.text; font.pixelSize: 13; font.weight: Font.Medium; Layout.fillWidth: true; elide: Text.ElideMiddle }
                                                            Text { text: window.jobTime(jobEntry.modelData.created_at); color: UI.Theme.textSecondary; font.pixelSize: 11; Layout.fillWidth: true }
                                                            Text { objectName: "jobStorage_" + jobEntry.modelData.id; text: jobEntry.modelData.storageLabel || qsTr("正在计算占用…"); color: UI.Theme.textSecondary; font.pixelSize: 11; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                                                        }
                                                        Text { visible: jobEntry.modelData.module_id === "cell-counting" && jobEntry.modelData.count !== undefined && jobEntry.modelData.count !== null; text: qsTr("%1 个").arg(jobEntry.modelData.count || 0); color: UI.Theme.text; font.pixelSize: 13 }
                                                        UI.StatusBadge { text: window.stateText(jobEntry.modelData.status, jobEntry.modelData.module_id); tone: window.stateTone(jobEntry.modelData.status) }
                                                        Text { text: jobEntry.expanded ? "⌃" : "⌄"; color: UI.Theme.textSecondary; font.pixelSize: 17 }
                                                    }
                                                }
                                                UI.ActionButton {
                                                    objectName: "deleteJob_" + jobEntry.modelData.id
                                                    symbol: "trash"
                                                    kind: "danger"
                                                    compact: true
                                                    Layout.preferredWidth: 34
                                                    enabled: window.jobDeletionEnabled
                                                    Accessible.name: qsTr("删除分析 %1").arg(jobEntry.modelData.name)
                                                    ToolTip.visible: hovered
                                                    ToolTip.text: qsTr("删除分析")
                                                    onClicked: backend.requestDeleteJobs([jobEntry.modelData.id])
                                                }
                                            }
                                        }
                                        Loader {
                                            active: jobEntry.expanded && backend.resultInfo.jobId === jobEntry.modelData.id
                                            visible: active
                                            Layout.fillWidth: true
                                            Layout.preferredHeight: item ? item.implicitHeight : 0
                                            sourceComponent: Component {
                                                UI.JobDetails {
                                                    job: backend.resultInfo
                                                    controller: backend
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                        Item { Layout.preferredHeight: 14 }
                    }
                }

                ScrollView {
                    id: maintenanceScroll
                    objectName: "maintenanceScroll"
                    clip: true
                    contentWidth: availableWidth
                    ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                    ColumnLayout {
                        width: maintenanceScroll.availableWidth
                        spacing: 22
                        Item { Layout.preferredHeight: 8 }
                        ColumnLayout {
                            Layout.fillWidth: true
                            Layout.leftMargin: 30
                            Layout.rightMargin: 30
                            spacing: 7
                            Text { text: qsTr("功能与维护"); color: UI.Theme.text; font.pixelSize: 28; font.weight: Font.DemiBold }
                        }
                        UI.Surface {
                            visible: backend.maintenanceMessage.length > 0
                            Layout.fillWidth: true
                            Layout.leftMargin: 30
                            Layout.rightMargin: 30
                            padding: 16
                            color: backend.maintenanceMessageKind === "error" ? UI.Theme.errorSoft : backend.maintenanceMessageKind === "success" ? UI.Theme.successSoft : UI.Theme.accentSoft
                            border.color: backend.maintenanceMessageKind === "error" ? UI.Theme.errorSoft : backend.maintenanceMessageKind === "success" ? UI.Theme.successSoft : UI.Theme.border
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 10
                                UI.Icon { kind: "info"; ink: UI.Theme.textMuted; Layout.preferredWidth: 18; Layout.preferredHeight: 18; Layout.alignment: Qt.AlignTop }
                                Text { objectName: "maintenanceMessage"; text: backend.maintenanceMessage; color: UI.Theme.textSecondary; font.pixelSize: 12; lineHeight: 1.3; wrapMode: Text.Wrap; Layout.fillWidth: true }
                            }
                        }
                        Repeater {
                            model: backend.installedModules.concat(backend.availableModules)
                            delegate: UI.Surface {
                                id: moduleCard
                                required property var modelData
                                Layout.fillWidth: true
                                Layout.leftMargin: 30
                                Layout.rightMargin: 30
                                spacing: 18
                                RowLayout {
                                    Layout.fillWidth: true
                                    spacing: 15
                                    Rectangle { width: 48; height: 48; radius: 14; color: UI.Theme.accentSoft; UI.Icon { anchors.centerIn: parent; width: 28; height: 28; kind: "layers"; ink: UI.Theme.textMuted } }
                                    ColumnLayout {
                                        Layout.fillWidth: true; spacing: 5
                                        Text { text: moduleCard.modelData.name; color: UI.Theme.text; font.pixelSize: 19; font.weight: Font.DemiBold; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                                        Text { visible: !!moduleCard.modelData.version; text: moduleCard.modelData.version || ""; color: UI.Theme.textSecondary; font.pixelSize: 12 }
                                    }
                                    UI.StatusBadge { text: moduleCard.modelData.status === "ready" ? qsTr("已安装") : window.stateText(moduleCard.modelData.status); tone: window.stateTone(moduleCard.modelData.status) }
                                }
                                Rectangle { Layout.fillWidth: true; height: 1; color: UI.Theme.border }
                                Text { visible: !!moduleCard.modelData.message; text: moduleCard.modelData.message || ""; color: moduleCard.modelData.hasError ? UI.Theme.error : UI.Theme.textSecondary; font.pixelSize: 12; wrapMode: Text.Wrap; Layout.fillWidth: true }
                                UI.ActivityBar { active: backend.maintenanceBusy; Layout.fillWidth: true; progress: backend.maintenanceProgress }
                                Flow {
                                    Layout.fillWidth: true
                                    spacing: 10
                                    UI.ActionButton { objectName: "installModule_" + moduleCard.modelData.id; text: backend.maintenanceBusy ? qsTr("正在维护…") : moduleCard.modelData.installed ? (moduleCard.modelData.status === "ready" ? qsTr("模块已就绪") : qsTr("已安装")) : qsTr("安装分析模块"); symbol: moduleCard.modelData.status === "ready" ? "check" : "import"; kind: moduleCard.modelData.installed ? "secondary" : "primary"; enabled: !moduleCard.modelData.installed && window.editable; onClicked: backend.installModule(moduleCard.modelData.id) }
                                    UI.ActionButton { objectName: "repairModule_" + moduleCard.modelData.id; text: qsTr("检查并修复"); symbol: "refresh"; enabled: window.editable && moduleCard.modelData.installed; onClicked: backend.repairModule(moduleCard.modelData.id) }
                                    UI.ActionButton { objectName: "removeModule_" + moduleCard.modelData.id; text: qsTr("删除模块"); kind: "danger"; enabled: window.editable && moduleCard.modelData.installed; onClicked: backend.removeModule(moduleCard.modelData.id) }
                                }
                                Text { visible: backend.busy; text: qsTr("任务运行时暂停组件维护，任务结束后可继续。"); color: UI.Theme.warning; font.pixelSize: 11; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                            }
                        }
                        Item { Layout.preferredHeight: 14 }
                    }
                }
                ScrollView {
                    id: settingsScroll
                    objectName: "settingsScroll"
                    clip: true
                    contentWidth: availableWidth
                    ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                    ColumnLayout {
                        width: settingsScroll.availableWidth
                        spacing: 22
                        Item { Layout.preferredHeight: 8 }
                        Text { text: qsTr("设置"); color: UI.Theme.text; font.pixelSize: 28; font.weight: Font.DemiBold; Layout.leftMargin: 30; Layout.rightMargin: 30; Layout.fillWidth: true }
                        UI.Surface {
                            objectName: "languageAppearanceCard"
                            Layout.fillWidth: true
                            Layout.leftMargin: 30
                            Layout.rightMargin: 30
                            spacing: 18
                            Text { text: qsTr("语言与外观"); color: UI.Theme.text; font.pixelSize: 14; font.weight: Font.DemiBold; Layout.fillWidth: true }
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 20
                                ColumnLayout {
                                    Layout.fillWidth: true
                                    spacing: 7
                                    Text { text: qsTr("界面语言"); color: UI.Theme.text; font.pixelSize: 13; Layout.fillWidth: true }
                                }
                                UI.ChoiceBox {
                                    objectName: "languageChoice"
                                    enabled: backend.languageEditable
                                    Layout.preferredWidth: 170
                                    model: ["English", "中文"]
                                    currentIndex: backend.language === "zh" ? 1 : 0
                                    Accessible.name: qsTr("界面语言")
                                    onActivated: backend.setLanguage(currentIndex === 1 ? "zh" : "en")
                                }
                            }
                            Rectangle { Layout.fillWidth: true; height: 1; color: UI.Theme.border }
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 20
                                ColumnLayout {
                                    Layout.fillWidth: true
                                    spacing: 7
                                    Text { text: qsTr("外观"); color: UI.Theme.text; font.pixelSize: 13; Layout.fillWidth: true }
                                }
                                UI.ChoiceBox {
                                    objectName: "appearanceChoice"
                                    enabled: !backend.maintenanceBusy
                                    Layout.preferredWidth: 170
                                    model: [qsTr("跟随系统"), qsTr("浅色"), qsTr("深色")]
                                    currentIndex: backend.appearanceMode === "light" ? 1 : backend.appearanceMode === "dark" ? 2 : 0
                                    Accessible.name: qsTr("外观")
                                    onActivated: backend.setAppearanceMode(["system", "light", "dark"][currentIndex])
                                }
                            }
                        }
                        UI.Surface {
                            objectName: "storageDataCard"
                            Layout.fillWidth: true
                            Layout.leftMargin: 30
                            Layout.rightMargin: 30
                            spacing: 16
                            Text { text: qsTr("存储与数据"); color: UI.Theme.text; font.pixelSize: 14; font.weight: Font.DemiBold; Layout.fillWidth: true }
                            Text { text: backend.dataLocation; color: UI.Theme.textSecondary; font.pixelSize: 12; Layout.fillWidth: true; wrapMode: Text.WrapAnywhere }
                            Flow {
                                Layout.fillWidth: true
                                spacing: 10
                                UI.ActionButton { objectName: "openDataFolderButton"; text: qsTr("打开软件数据"); symbol: "folder"; onClicked: backend.openDataFolder() }
                                UI.ActionButton { objectName: "chooseDataDirectoryButton"; text: qsTr("更改存储目录"); enabled: window.editable; onClicked: backend.chooseDataDirectory() }
                                UI.ActionButton { objectName: "clearCacheButton"; text: qsTr("清理缓存"); enabled: window.editable; onClicked: cacheDialog.open() }
                            }
                        }
                        RowLayout {
                            objectName: "updatesUninstallRow"
                            Layout.fillWidth: true
                            Layout.leftMargin: 30
                            Layout.rightMargin: 30
                            spacing: 22
                            UI.Surface {
                                objectName: "softwareUpdatesCard"
                                Layout.fillWidth: true
                                Layout.fillHeight: true
                                Layout.preferredWidth: 1
                                spacing: 15
                                Text { text: qsTr("软件更新"); color: UI.Theme.text; font.pixelSize: 14; font.weight: Font.DemiBold; Layout.fillWidth: true }
                                UI.ActionButton { objectName: "checkUpdatesButton"; text: qsTr("检查更新"); symbol: "refresh"; enabled: !backend.maintenanceBusy && !backend.updatesBusy; onClicked: backend.checkUpdates() }
                                UI.ActivityBar { active: backend.updatesBusy; Layout.fillWidth: true }
                                Text { objectName: "updateStatusMessage"; visible: backend.updateStatusMessage.length > 0; text: backend.updateStatusMessage; color: UI.Theme.textSecondary; font.pixelSize: 12; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                            }
                            Rectangle {
                                objectName: "uninstallCard"
                                Layout.fillWidth: true
                                Layout.fillHeight: true
                                Layout.preferredWidth: 1
                                implicitHeight: uninstallButton.implicitHeight + 40
                                radius: 14
                                color: UI.Theme.surface
                                border.color: UI.Theme.border
                                border.width: 1
                                UI.ActionButton {
                                    id: uninstallButton
                                    objectName: "uninstallButton"
                                    anchors.centerIn: parent
                                    text: qsTr("卸载软件")
                                    kind: "danger"
                                    enabled: window.editable
                                    onClicked: uninstallDialog.open()
                                }
                            }
                        }
                        UI.Surface {
                            objectName: "aboutCard"
                            Layout.fillWidth: true
                            Layout.leftMargin: 30
                            Layout.rightMargin: 30
                            spacing: 15
                            Text { text: qsTr("关于"); color: UI.Theme.text; font.pixelSize: 14; font.weight: Font.DemiBold; Layout.fillWidth: true }
                            Text { text: "Project Leap · v" + backend.appVersion; color: UI.Theme.text; font.pixelSize: 16; font.weight: Font.DemiBold; Layout.fillWidth: true }
                            Text { objectName: "aboutDescription"; text: qsTr("一款用于 IHC 图像分析的本地科研平台，提供星形胶质细胞荧光强度测量与细胞计数工具。更多功能即将推出。"); color: UI.Theme.textSecondary; font.pixelSize: 12; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                            Text { objectName: "aboutDeveloper"; text: qsTr("- 开发者：Dingcheng Wang | 神经科学本科生 | 俄亥俄州立大学"); color: UI.Theme.textSecondary; font.pixelSize: 12; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                            Text { objectName: "aboutAffiliation"; text: qsTr("- 研究隶属：Min Zhou 实验室 | 神经科学系 | 医学院"); color: UI.Theme.textSecondary; font.pixelSize: 12; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                            Text { objectName: "aboutLicense"; text: qsTr("本应用的原创代码以 Apache License 2.0 许可证授权。第三方组件遵循各自的许可证。"); color: UI.Theme.textSecondary; font.pixelSize: 12; font.italic: true; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                            Flow {
                                Layout.fillWidth: true
                                spacing: 10
                                UI.ActionButton { objectName: "openLicensesButton"; text: qsTr("许可证"); symbol: "external"; onClicked: backend.openLicenses() }
                                UI.ActionButton { objectName: "openReferencesButton"; text: qsTr("来源与参考资料"); symbol: "external"; onClicked: backend.openReferences() }
                            }
                        }
                        Item { Layout.preferredHeight: 14 }
                    }
                }
            }
        }
    }
    Dialog {
        id: uninstallDialog
        objectName: "uninstallDialog"
        title: qsTr("卸载软件")
        anchors.centerIn: parent
        width: Math.min(window.width - 60, 500)
        modal: true
        popupType: Popup.Window
        standardButtons: Dialog.Cancel
        contentItem: ColumnLayout {
            spacing: 16
            Text { text: qsTr("请选择卸载方式。软件退出后执行卸载。"); color: UI.Theme.textSecondary; font.pixelSize: 13; Layout.fillWidth: true; wrapMode: Text.WordWrap }
            UI.ActionButton { objectName: "uninstallKeepDataButton"; text: qsTr("卸载软件，保留任务与结果"); Layout.fillWidth: true; onClicked: { uninstallDialog.close(); backend.requestUninstall(false) } }
            Text { text: qsTr("移除软件、模块和偏好设置；保留任务记录、最终 ROI、正式结果及必要恢复数据。"); color: UI.Theme.textSecondary; font.pixelSize: 12; Layout.fillWidth: true; wrapMode: Text.WordWrap }
            Rectangle { Layout.fillWidth: true; height: 1; color: UI.Theme.border }
            UI.ActionButton { objectName: "uninstallRemoveDataButton"; text: qsTr("卸载软件并删除软件数据"); kind: "danger"; Layout.fillWidth: true; onClicked: { uninstallDialog.close(); backend.requestUninstall(true) } }
            Text { text: qsTr("同时永久删除软件数据目录及其中的任务、模块、最终 ROI 和正式结果。目录外的原始输入不受影响。"); color: UI.Theme.error; font.pixelSize: 12; Layout.fillWidth: true; wrapMode: Text.WordWrap }
        }
    }
    Dialog {
        id: inputDropDialog
        objectName: "inputDropDialog"
        title: qsTr("一次导入一个文件")
        anchors.centerIn: parent
        width: 390
        modal: true
        popupType: Popup.Window
        standardButtons: Dialog.Ok
        contentItem: Label { text: qsTr("当前每次分析一张多通道 TIFF，请一次拖入一个文件。"); wrapMode: Text.WordWrap; color: UI.Theme.textSecondary; font.pixelSize: 13; padding: 12 }
    }
    Dialog {
        id: deleteJobsDialog
        objectName: "deleteJobsDialog"
        property int jobCount: 1
        title: jobCount === 1 ? qsTr("删除分析") : qsTr("删除所选分析")
        anchors.centerIn: parent
        width: Math.min(window.width - 60, 480)
        modal: true
        popupType: Popup.Window
        contentItem: ColumnLayout {
            spacing: 16
            Text {
                objectName: "deleteJobsConfirmationText"
                text: deleteJobsDialog.jobCount === 1
                    ? qsTr("永久删除该分析在软件内部保存的数据；你的原始图像及已经导出文件不会被删除。")
                    : qsTr("永久删除这些分析在软件内部保存的数据；你的原始图像及已经导出文件不会被删除。")
                color: UI.Theme.textSecondary
                font.pixelSize: 13
                Layout.fillWidth: true
                wrapMode: Text.WordWrap
            }
            CheckBox {
                id: skipDeleteConfirmation
                objectName: "skipDeleteConfirmation"
                text: qsTr("一个月内不再询问")
                Layout.fillWidth: true
            }
        }
        footer: DialogButtonBox {
            UI.ActionButton { objectName: "cancelDeleteJobsButton"; text: qsTr("取消"); onClicked: deleteJobsDialog.reject() }
            UI.ActionButton { objectName: "confirmDeleteJobsButton"; text: qsTr("删除"); kind: "danger"; enabled: window.jobDeletionEnabled; onClicked: deleteJobsDialog.accept() }
        }
        onAccepted: backend.confirmDeleteJobs(skipDeleteConfirmation.checked)
        onRejected: backend.cancelDeleteJobs()
    }
    Dialog {
        id: cacheDialog
        title: qsTr("清理可重建缓存")
        anchors.centerIn: parent
        width: 380
        modal: true
        popupType: Popup.Window
        standardButtons: Dialog.Ok | Dialog.Cancel
        contentItem: Label { text: qsTr("清理可重建的界面缓存和已不使用的临时文件。原始图像、分析结果和审核数据会保留。"); wrapMode: Text.WordWrap; color: UI.Theme.textSecondary; font.pixelSize: 13; padding: 12 }
        onAccepted: backend.clearDisposableCache()
    }
}
