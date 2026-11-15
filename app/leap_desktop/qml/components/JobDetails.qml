import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Surface {
    id: jobDetails
    required property var job
    required property var controller
    readonly property var previews: (job.artifacts || []).filter(function(item) { return String(item.kind).toLowerCase() === "png" })
    readonly property var otherArtifacts: (job.artifacts || []).filter(function(item) { return String(item.kind).toLowerCase() !== "png" })
    readonly property bool countingJob: job.moduleId === "cell-counting" || job.module_id === "cell-counting"
    property bool runDetailsExpanded: false
    objectName: "jobDetails_" + (job.jobId || "")
    spacing: 16
    padding: 20

    RowLayout {
        Layout.fillWidth: true
        Text { text: qsTr("任务详情"); color: Theme.textSecondary; font.pixelSize: 13; font.weight: Font.DemiBold; Layout.fillWidth: true }
        ActionButton { visible: jobDetails.job.status === "completed"; text: qsTr("打开结果文件夹"); symbol: "folder"; compact: true; onClicked: jobDetails.controller.openResultsForJob(jobDetails.job.jobId) }
    }
    Flow {
        visible: jobDetails.countingJob
        Layout.fillWidth: true
        spacing: 12
        Text { visible: jobDetails.job.count !== undefined && jobDetails.job.count !== null && jobDetails.job.status === "completed"; text: qsTr("确认计数：%1").arg(jobDetails.job.count || 0); color: Theme.text; font.pixelSize: 14; font.weight: Font.DemiBold; height: 32; verticalAlignment: Text.AlignVCenter }
        Text { visible: jobDetails.job.auto_count !== undefined; text: qsTr("自动计数：%1").arg(jobDetails.job.auto_count || 0); color: Theme.textSecondary; font.pixelSize: 12; height: 32; verticalAlignment: Text.AlignVCenter }
        ActionButton { objectName: "reopenCountingJob"; visible: !!jobDetails.job.analysis_path; text: jobDetails.job.status === "completed" ? qsTr("重新打开计数") : qsTr("继续审核计数"); compact: true; enabled: !jobDetails.controller.maintenanceBusy; onClicked: jobDetails.controller.openCountingJob(jobDetails.job.jobId) }
    }
    Text { visible: !!jobDetails.job.message; text: jobDetails.job.message || ""; color: Theme.textSecondary; font.pixelSize: 12; lineHeight: 1.35; wrapMode: Text.WordWrap; Layout.fillWidth: true }
    Text { visible: !jobDetails.job.artifacts || jobDetails.job.artifacts.length === 0; text: qsTr("暂无可显示的结果文件。"); color: Theme.textSecondary; font.pixelSize: 12; Layout.fillWidth: true; wrapMode: Text.WordWrap }
    Text { visible: jobDetails.previews.length > 0; text: qsTr("结果预览"); color: Theme.textSecondary; font.pixelSize: 13; font.weight: Font.DemiBold; Layout.fillWidth: true }
    Flickable {
        id: previewStrip
        objectName: "jobPreviewStrip_" + jobDetails.job.jobId
        visible: jobDetails.previews.length > 0
        Layout.fillWidth: true
        Layout.preferredHeight: previewRow.implicitHeight + 14
        contentWidth: previewRow.implicitWidth
        contentHeight: previewRow.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        flickableDirection: Flickable.HorizontalFlick
        ScrollBar.horizontal: ScrollBar { policy: previewStrip.contentWidth > previewStrip.width ? ScrollBar.AlwaysOn : ScrollBar.AlwaysOff }
        Row {
            id: previewRow
            spacing: 14
            Repeater {
                model: jobDetails.previews
                delegate: ColumnLayout {
                    id: previewEntry
                    required property var modelData
                    required property int index
                    width: 180
                    spacing: 8
                    Button {
                        id: previewButton
                        objectName: "jobPreviewButton_" + jobDetails.job.jobId + "_" + previewEntry.index
                        Layout.fillWidth: true
                        Layout.preferredHeight: 120
                        padding: 8
                        text: qsTr("打开 %1").arg(previewEntry.modelData.name)
                        Accessible.name: text
                        focusPolicy: Qt.StrongFocus
                        hoverEnabled: true
                        onClicked: jobDetails.controller.openArtifactForJob(jobDetails.job.jobId, previewEntry.modelData.path)
                        background: Rectangle { radius: 8; color: Theme.surface; border.width: previewButton.activeFocus ? 2 : 1; border.color: previewButton.activeFocus ? Theme.accent : previewButton.hovered ? Theme.borderStrong : Theme.border }
                        contentItem: Item {
                            Image {
                                id: previewImage
                                objectName: "jobPreview_" + jobDetails.job.jobId + "_" + previewEntry.index
                                anchors.fill: parent
                                source: previewEntry.modelData.url || ""
                                fillMode: Image.PreserveAspectFit
                                asynchronous: true
                                cache: false
                                sourceSize: Qt.size(480, 480)
                                Accessible.name: previewButton.text
                            }
                            Text {
                                anchors.centerIn: parent
                                width: parent.width - 20
                                visible: previewImage.status === Image.Loading || previewImage.status === Image.Error
                                text: previewImage.status === Image.Error ? qsTr("预览未能载入\n可点击打开原文件") : qsTr("正在载入预览")
                                color: Theme.textSecondary
                                font.pixelSize: 12
                                horizontalAlignment: Text.AlignHCenter
                                wrapMode: Text.WordWrap
                            }
                        }
                        ToolTip.visible: hovered
                        ToolTip.delay: 600
                        ToolTip.text: qsTr("打开原图")
                    }
                    Text {
                        text: previewEntry.modelData.name
                        color: Theme.textSecondary; font.pixelSize: 11; Layout.fillWidth: true
                        Layout.preferredHeight: 30
                        wrapMode: Text.WrapAnywhere; maximumLineCount: 2; elide: Text.ElideRight
                        HoverHandler { id: previewNameHover }
                        ToolTip.visible: previewNameHover.hovered
                        ToolTip.text: text
                    }
                }
            }
        }
    }
    Repeater {
        model: jobDetails.otherArtifacts
        delegate: Button {
            id: artifactButton
            required property var modelData
            Layout.fillWidth: true
            implicitHeight: artifactContent.implicitHeight + 24
            text: modelData.name
            enabled: !!modelData.path
            Accessible.name: text
            hoverEnabled: true
            focusPolicy: Qt.StrongFocus
            leftPadding: 12; rightPadding: 12; topPadding: 12; bottomPadding: 12
            onClicked: jobDetails.controller.openArtifactForJob(jobDetails.job.jobId, modelData.path)
            background: Rectangle { radius: 7; color: artifactButton.hovered ? Theme.surfaceHover : Theme.surfaceAlt; border.width: artifactButton.activeFocus ? 2 : 1; border.color: artifactButton.activeFocus ? Theme.accent : Theme.border }
            contentItem: RowLayout {
                id: artifactContent
                spacing: 10
                Icon { kind: "jobs"; ink: Theme.textMuted; Layout.preferredWidth: 17; Layout.preferredHeight: 17 }
                Text { text: artifactButton.text; color: Theme.textSecondary; font.pixelSize: 12; wrapMode: Text.WrapAnywhere; Layout.fillWidth: true }
                Icon { kind: "external"; ink: Theme.textMuted; Layout.preferredWidth: 14; Layout.preferredHeight: 14 }
            }
            ToolTip.visible: hovered
            ToolTip.delay: 600
            ToolTip.text: modelData.path
        }
    }
    Text { visible: !!jobDetails.job.results_dir; text: jobDetails.job.results_dir || ""; color: Theme.textSecondary; font.pixelSize: 11; wrapMode: Text.WrapAnywhere; Layout.fillWidth: true }
    Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: Theme.border }
    RowLayout {
        Layout.fillWidth: true
        Text { text: qsTr("运行细节"); color: Theme.text; font.pixelSize: 13; font.weight: Font.DemiBold; Layout.fillWidth: true }
        ActionButton {
            objectName: "jobRunDetailsToggle_" + jobDetails.job.jobId
            text: jobDetails.runDetailsExpanded ? qsTr("收起") : qsTr("展开")
            kind: "subtle"
            compact: true
            onClicked: {
                jobDetails.runDetailsExpanded = !jobDetails.runDetailsExpanded
                if (jobDetails.runDetailsExpanded) jobDetails.controller.loadJobRunDetails(jobDetails.job.jobId)
            }
        }
    }
    Loader {
        objectName: "jobRunDetailsLoader_" + jobDetails.job.jobId
        active: jobDetails.runDetailsExpanded
        visible: active
        Layout.fillWidth: true
        Layout.preferredHeight: item ? item.implicitHeight : 0
        sourceComponent: Component {
            RunDetails {
                objectName: "jobRunDetails_" + jobDetails.job.jobId
                controller: jobDetails.controller
                details: controller.jobRunDetails.jobId === jobDetails.job.jobId ? controller.jobRunDetails : ({loading: true})
                history: true
                showTitle: false
                displayActive: visible
                padding: 0
                color: "transparent"
                border.width: 0
            }
        }
    }
}
