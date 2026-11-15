import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs
import "../components" as UI

ColumnLayout {
    id: page
    required property var workspace
    readonly property var editor: backend.counting
    readonly property bool reviewing: editor.available
    property bool showRunSettings: false
    readonly property var supportMarkers: ["eGFP", "Biocytin", "ALDH1L1", qsTr("自定义")]
    readonly property bool customSupportMarker: backend.countSupportMarker.length > 0 && supportMarkers.slice(0, 3).indexOf(backend.countSupportMarker) < 0
    property bool customMarkerSelected: false
    readonly property var sourceChoices: {
        var list = [qsTr("未包含")]
        for (var i = 0; i < (backend.imageInfo.channel_count || 0); ++i) list.push("C" + (i + 1))
        return list
    }
    function channelName(index) { return index >= 0 ? "C" + (index + 1) : qsTr("未包含") }
    function runCalibrationText() {
        var size = page.editor.runSettings.physical_size_um || {}
        var x = size.X !== undefined ? size.X : size.x
        var y = size.Y !== undefined ? size.Y : size.y
        var z = size.Z !== undefined ? size.Z : size.z
        if (x === undefined || x === null || y === undefined || y === null) return qsTr("未提供")
        var value = Number(x).toFixed(3) + " × " + Number(y).toFixed(3)
        if (z !== undefined && z !== null) value += " × " + Number(z).toFixed(3)
        return value + " µm"
    }
    function colorFor(index) {
        var custom = backend.imageInfo.channel_colors || []
        if (custom[index]) return custom[index]
        var role = backend.channelRoles[index]
        return ({DAPI:"#0000FF", eGFP:"#00FF00", GFAP: backend.channelRoles.indexOf("eGFP") >= 0 ? "#FFFF00" : "#00FF00", Biocytin:"#FFFFFF", ALDH1L1:"#00FF00", SR101:"#FF0000", Target:"#FF0000", KCNN1:"#FF0000", KCNN2:"#FF0000", KCNN3:"#FF0000", KCNJ10:"#FF0000"})[role] || "#00FFFF"
    }
    spacing: 18
    Layout.fillWidth: true

    FileDialog {
        id: sourceRelocationDialog
        objectName: "countSourceRelocationDialog"
        title: qsTr("选择计数使用的原始 TIFF 图像")
        nameFilters: [qsTr("TIFF 图像 (*.tif *.tiff)")]
        fileMode: FileDialog.OpenFile
        onAccepted: backend.relocateCountingSource(selectedFile.toString())
    }

    UI.Surface {
        visible: page.reviewing
        enabled: !backend.maintenanceBusy
        Layout.fillWidth: true
        padding: 16
        spacing: 12
        RowLayout {
            id: countHeader
            objectName: "countHeader"
            Layout.fillWidth: true
            spacing: 16
            Text {
                objectName: "countTotal"
                text: qsTr("Astrocyte 总数：%1").arg(page.editor.total)
                color: UI.Theme.text; font.pixelSize: 20; font.weight: Font.DemiBold
                Layout.fillWidth: true
                Layout.minimumWidth: implicitWidth
                Accessible.name: text
            }
            UI.ActionButton { objectName: "countConfirm"; text: qsTr("确认并保存 Astrocyte 计数"); kind: "primary"; enabled: !page.editor.busy; onClicked: page.editor.confirm() }
        }
        RowLayout {
            objectName: "countSummary"
            spacing: 18
            Row {
                spacing: 4
                Rectangle { width: 8; height: 8; radius: 4; color: "transparent"; border.color: "#56C987"; border.width: 2; anchors.verticalCenter: parent.verticalCenter }
                Text { objectName: "countCategory_Astrocyte"; text: "Astrocyte: " + page.editor.total; color: UI.Theme.textSecondary; font.pixelSize: 12 }
            }
            Row {
                spacing: 4
                Item { width: 8; height: 10; anchors.verticalCenter: parent.verticalCenter; Rectangle { x: 1; y: 2; width: 6; height: 6; rotation: 45; color: "#E4A43A" } }
                Text { objectName: "countCategory_HighLikelihood"; text: "High Likelihood: " + page.editor.likelyUnrecognizedCount; color: UI.Theme.textSecondary; font.pixelSize: 12 }
            }
            Row {
                spacing: 4
                Item { width: 8; height: 10; anchors.verticalCenter: parent.verticalCenter; Rectangle { x: 2; y: 3; width: 4; height: 4; radius: 2; color: "#F3D76A" } }
                Text { objectName: "countCategory_Unrecognized"; text: "Unrecognized: " + page.editor.ordinaryUnrecognizedCount; color: UI.Theme.textSecondary; font.pixelSize: 12 }
            }
            Row {
                spacing: 4
                Rectangle { width: 6; height: 6; color: "#B18AE3"; anchors.verticalCenter: parent.verticalCenter }
                Text { objectName: "countCategory_Neurons"; text: "Neurons: " + page.editor.neuronCount; color: UI.Theme.textSecondary; font.pixelSize: 12 }
            }
        }
        Flow {
            Layout.fillWidth: true
            spacing: 7
            UI.ActionButton { objectName: "countSelectMode"; text: qsTr("选择 (V)"); compact: true; kind: page.editor.mode === "select" ? "primary" : "secondary"; enabled: !page.editor.busy; onClicked: { page.editor.setMode("select"); reviewCanvas.forceActiveFocus() } }
            UI.ActionButton { objectName: "countAddMode"; text: qsTr("添加 (A)"); compact: true; kind: page.editor.mode === "add" ? "primary" : "secondary"; enabled: !page.editor.busy; onClicked: { page.editor.setMode("add"); reviewCanvas.forceActiveFocus() } }
            UI.ActionButton { objectName: "countMerge"; text: qsTr("合并所选 (M)"); compact: true; enabled: page.editor.canMerge && !page.editor.busy; onClicked: { page.editor.mergeSelected(); reviewCanvas.forceActiveFocus() } ToolTip.visible: hovered; ToolTip.delay: 500; ToolTip.text: qsTr("将两个或更多选中点合并为一个点；可撤销。") }
            UI.ActionButton { objectName: "countAcceptSelected"; text: qsTr("加入计数"); compact: true; enabled: page.editor.canAcceptSelected && !page.editor.busy; onClicked: { page.editor.acceptSelected(); reviewCanvas.forceActiveFocus() } }
            UI.ActionButton { objectName: "countDelete"; text: qsTr("删除所选"); compact: true; kind: "danger"; enabled: page.editor.selectedIds.length > 0 && !page.editor.busy; onClicked: { page.editor.deleteSelected(); reviewCanvas.forceActiveFocus() } }
            UI.ActionButton { objectName: "countUndo"; text: qsTr("撤销"); compact: true; enabled: page.editor.canUndo && !page.editor.busy; onClicked: { page.editor.undo(); reviewCanvas.forceActiveFocus() } }
            UI.ActionButton { objectName: "countRedo"; text: qsTr("重做"); compact: true; enabled: page.editor.canRedo && !page.editor.busy; onClicked: { page.editor.redo(); reviewCanvas.forceActiveFocus() } }
            Row {
                spacing: 7
                UI.ActionButton { objectName: "countFit"; text: qsTr("适合窗口"); compact: true; onClicked: reviewCanvas.fitImage() }
                UI.ActionButton { objectName: "countZoomOut"; text: "−"; compact: true; Accessible.name: qsTr("缩小"); onClicked: reviewCanvas.zoomAt(0.8, reviewCanvas.width / 2, reviewCanvas.height / 2) }
                UI.ActionButton { objectName: "countZoomIn"; text: "+"; compact: true; Accessible.name: qsTr("放大"); onClicked: reviewCanvas.zoomAt(1.25, reviewCanvas.width / 2, reviewCanvas.height / 2) }
            }
        }
        UI.CountCanvas {
            id: reviewCanvas
            editor: page.editor
            interactionEnabled: !backend.maintenanceBusy
            Layout.fillWidth: true
            Layout.preferredHeight: Math.max(300, Math.min(520, workspace.height - 410))
        }
        RowLayout {
            Layout.fillWidth: true
            spacing: 10
            UI.ActionButton { objectName: "countMip"; text: "MIP"; compact: true; kind: page.editor.displayedZIndex < 0 ? "primary" : "secondary"; enabled: !page.editor.busy; Accessible.name: qsTr("全 Z 最大强度投影"); onClicked: page.editor.setZ(-1) }
            Text { objectName: "countZReadout"; text: page.editor.displayedZIndex < 0 ? qsTr("全 Z 投影") : qsTr("Z 层 %1 / %2").arg(page.editor.displayedZIndex + 1).arg(page.editor.zCount); color: UI.Theme.textSecondary; font.pixelSize: 12; Layout.minimumWidth: 84 }
            Slider {
                objectName: "countZSlider"
                Layout.fillWidth: true
                Layout.minimumWidth: 60
                from: 0; to: Math.max(0, page.editor.zCount - 1); stepSize: 1
                value: Math.max(0, page.editor.zIndex)
                enabled: page.editor.zCount > 1
                Accessible.name: qsTr("查看 Z 层")
                function finishScrubbing() {
                    var reviewEditor = page.editor
                    // Defer the notification until navigation bindings finish.
                    Qt.callLater(function() { reviewEditor.setScrubbing(false) })
                }
                onPressedChanged: if (pressed) page.editor.setScrubbing(true); else finishScrubbing()
                onVisibleChanged: if (!visible && pressed) finishScrubbing()
                Component.onDestruction: if (pressed) finishScrubbing()
                onMoved: page.editor.setZ(Math.round(value))
            }
        }
        Flow {
            Layout.fillWidth: true
            spacing: 12
            CheckBox { objectName: "countShowLabels"; text: qsTr("显示编号"); checked: page.editor.showLabels; Accessible.name: text; onToggled: page.editor.setShowLabels(checked) }
            RowLayout {
                spacing: 6
                Text { text: qsTr("编号字号"); color: UI.Theme.textSecondary; font.pixelSize: 12 }
                SpinBox { objectName: "countLabelSize"; from: 8; to: 120; stepSize: 2; value: page.editor.labelSize; editable: true; implicitWidth: 112; Accessible.name: qsTr("编号字号"); onValueModified: page.editor.setLabelSize(value) }
            }
            Text { text: qsTr("缩放 %1%").arg(Math.round(reviewCanvas.zoom * 100)); color: UI.Theme.textSecondary; font.pixelSize: 12; height: 40; verticalAlignment: Text.AlignVCenter }
        }
        Flow {
            objectName: "countReviewChannels"
            Layout.fillWidth: true
            spacing: 10
            Repeater {
                model: page.editor.channels
                delegate: CheckBox {
                    required property var modelData
                    objectName: "countVisibleChannel_" + modelData.index
                    text: "C" + (modelData.index + 1)
                    checked: modelData.visible !== false
                    enabled: !page.editor.busy
                    Accessible.name: qsTr("显示通道 %1").arg(text)
                    onToggled: page.editor.setChannelVisible(modelData.index, checked)
                    indicator: Rectangle { x: 0; y: (parent.height - 17) / 2; width: 17; height: 17; radius: 4; color: parent.checked ? parent.modelData.color : "transparent"; border.color: parent.activeFocus ? UI.Theme.accent : UI.Theme.borderStrong; border.width: parent.activeFocus ? 2 : 1; Text { anchors.centerIn: parent; text: parent.parent.checked ? "✓" : ""; color: "#171A21"; font.pixelSize: 12 } }
                }
            }
        }
        Text { objectName: "countEditorMessage"; visible: !!page.editor.message && page.editor.message !== page.editor.sourceRelocationMessage; text: page.editor.message; color: UI.Theme.warning; font.pixelSize: 12; Layout.fillWidth: true; wrapMode: Text.WordWrap }
        UI.ActionButton {
            objectName: "countRelocateSource"
            visible: page.editor.sourceRelocationNeeded
            enabled: !page.editor.sourceRelocationInProgress && !page.editor.busy
            text: qsTr("重新定位原图…")
            symbol: "folder"
            onClicked: sourceRelocationDialog.open()
        }
        Text {
            objectName: "countSourceRelocationMessage"
            visible: page.editor.sourceRelocationNeeded && !!page.editor.sourceRelocationMessage
            text: page.editor.sourceRelocationMessage
            color: UI.Theme.warning
            font.pixelSize: 12
            Layout.fillWidth: true
            wrapMode: Text.WordWrap
        }
        UI.ActionButton { objectName: "countToggleSettings"; text: page.showRunSettings ? qsTr("收起本次设置") : qsTr("查看本次设置"); kind: "subtle"; onClicked: page.showRunSettings = !page.showRunSettings }
        Rectangle {
            objectName: "countRunSettings"
            visible: page.showRunSettings
            Layout.fillWidth: true
            implicitHeight: runSettingsBody.implicitHeight + 24
            radius: 8
            color: UI.Theme.surfaceAlt
            ColumnLayout {
                id: runSettingsBody
                anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                anchors.margins: 12
                spacing: 8
                Text { text: qsTr("本次分析设置（只读）"); color: UI.Theme.text; font.pixelSize: 13; font.weight: Font.DemiBold }
                Text { text: page.editor.runSettings.source_path || "—"; color: UI.Theme.textSecondary; font.pixelSize: 12; Layout.fillWidth: true; wrapMode: Text.WrapAnywhere }
                Text { text: qsTr("DAPI：%1 · 辅助通道：%2 · 标记物：%3").arg(page.channelName(page.editor.runSettings.dapi_channel)).arg(page.channelName(page.editor.runSettings.support_channel)).arg(page.editor.runSettings.support_marker || qsTr("未包含")); color: UI.Theme.textSecondary; font.pixelSize: 12; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                Text { text: qsTr("预选显示通道：%1").arg((page.editor.runSettings.display_channels || []).map(function(i) { return page.channelName(i) }).join("、")); color: UI.Theme.textSecondary; font.pixelSize: 12; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                Text { text: qsTr("%1 × %2 px · %3 层").arg(page.editor.runSettings.width || 0).arg(page.editor.runSettings.height || 0).arg(page.editor.runSettings.z_count || 1); color: UI.Theme.textSecondary; font.pixelSize: 12 }
                Text { text: qsTr("体素标定 X × Y × Z") + " · " + page.runCalibrationText(); color: UI.Theme.textSecondary; font.pixelSize: 12; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                Text { visible: page.editor.schemaVersion < 2; text: qsTr("此结果来自历史二维计数流程。"); color: UI.Theme.warning; font.pixelSize: 12; Layout.fillWidth: true; wrapMode: Text.WordWrap }
            }
        }
    }

    GridLayout {
        objectName: "countInputGrid"
        visible: !page.reviewing
        columns: workspace.wide ? 2 : 1
        columnSpacing: 20
        rowSpacing: 20
        Layout.fillWidth: true
        ColumnLayout {
            objectName: "countInputColumn"
            Layout.row: workspace.wide ? 0 : workspace.statusFirst ? 1 : 0
            Layout.column: 0
            Layout.fillWidth: true
            Layout.fillHeight: workspace.wide
            Layout.alignment: Qt.AlignTop
            spacing: 0
            UI.Surface {
                objectName: "countImageCard"
                Layout.fillWidth: true
                spacing: workspace.hasSourcePreview ? 12 : 16
                RowLayout {
                    Layout.fillWidth: true
                    Text { text: qsTr("图像导入"); color: UI.Theme.text; font.pixelSize: 14; font.weight: Font.DemiBold; Layout.fillWidth: true }
                    UI.ActionButton { objectName: "countRemoveImage"; text: qsTr("移除"); kind: "subtle"; compact: true; visible: workspace.hasImage; enabled: workspace.editable && !page.editor.busy; onClicked: backend.clearImportedImage() }
                    UI.ActionButton { objectName: "countChangeImage"; visible: workspace.hasImage; text: qsTr("更换图像"); compact: true; enabled: workspace.editable && !backend.busy && !page.editor.busy; onClicked: backend.chooseImage() }
                }
                Rectangle {
                    objectName: "countSourceDropZone"
                    Layout.fillWidth: true
                    implicitHeight: workspace.hasSourcePreview ? 200 : workspace.hasImage ? 148 : 190
                    radius: 10
                    color: fileDrop.containsDrag ? UI.Theme.accentSoft : UI.Theme.surfaceAlt
                    border.color: fileDrop.containsDrag ? UI.Theme.accent : UI.Theme.border
                    border.width: fileDrop.containsDrag ? 2 : 1
                    DropArea {
                        id: fileDrop
                        anchors.fill: parent
                        enabled: workspace.editable && !backend.busy && !page.editor.busy
                        onDropped: function(drop) {
                            if (drop.hasUrls && drop.urls.length > 1) { workspace.warnMultiFileDrop(); drop.accepted = false; return }
                            if (drop.hasUrls && drop.urls.length === 1) { backend.inspectImage(drop.urls[0].toString()); drop.acceptProposedAction() }
                        }
                    }
                    ColumnLayout {
                        visible: !workspace.hasImage
                        anchors.centerIn: parent
                        width: parent.width - 30
                        spacing: 10
                        Rectangle { Layout.alignment: Qt.AlignHCenter; width: 44; height: 44; radius: 13; color: UI.Theme.accentSoft; UI.Icon { anchors.centerIn: parent; width: 24; height: 24; kind: "import"; ink: UI.Theme.textMuted } }
                        Text { text: fileDrop.containsDrag ? qsTr("松开以导入图像") : qsTr("将图像拖到这里"); color: UI.Theme.textSecondary; font.pixelSize: 13; font.weight: Font.Medium; Layout.alignment: Qt.AlignHCenter }
                        UI.ActionButton { objectName: "countChooseImage"; text: qsTr("选择图像…"); compact: true; enabled: workspace.editable && !backend.busy && !page.editor.busy; Layout.alignment: Qt.AlignHCenter; onClicked: backend.chooseImage() }
                        Text { text: qsTr("当前模式仅支持.tif/.tiff格式"); color: UI.Theme.textSecondary; font.pixelSize: 11; Layout.alignment: Qt.AlignHCenter; Layout.fillWidth: true; horizontalAlignment: Text.AlignHCenter; wrapMode: Text.WordWrap }
                    }
                    RowLayout {
                        visible: workspace.hasImage
                        anchors.fill: parent
                        anchors.margins: 14
                        spacing: 14
                        Image { visible: workspace.hasSourcePreview; source: backend.imageInfo.thumbnail_url || ""; Layout.preferredWidth: 100; Layout.preferredHeight: 98; fillMode: Image.PreserveAspectFit; asynchronous: true; Accessible.name: workspace.sourcePreviewLabel() }
                        ColumnLayout {
                            Layout.fillWidth: true
                            Text { text: backend.imageInfo.name || qsTr("将图像拖到这里"); color: UI.Theme.text; font.pixelSize: 14; font.weight: Font.Medium; Layout.fillWidth: true; wrapMode: Text.WrapAnywhere; maximumLineCount: 2; elide: Text.ElideMiddle }
                            Text { text: workspace.metadataReady ? workspace.shapeText() + " · " + qsTr("%1 通道 / %2 层").arg(backend.imageInfo.channel_count).arg(backend.imageInfo.z_count) : qsTr("TIFF / OME-TIFF / ImageJ TIFF"); color: UI.Theme.textSecondary; font.pixelSize: 12; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                            Text { visible: workspace.metadataReady; text: qsTr("体素标定 X × Y × Z") + " · " + workspace.calibrationText(); color: UI.Theme.textSecondary; font.pixelSize: 11; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                        }
                    }
                }
                GridLayout {
                    Layout.fillWidth: true
                    columns: width < 620 ? 1 : 2
                    visible: backend.imageInfo.series_count > 1 || backend.imageInfo.time_count > 1
                    UI.ChoiceBox {
                        objectName: "countSeriesChoice"
                        visible: backend.imageInfo.series_count > 1
                        Layout.fillWidth: true
                        placeholder: qsTr("请选择图像组")
                        model: (backend.imageInfo.series || []).map(function(item, index) { return qsTr("图像组 %1 · %2").arg(index + 1).arg(item.name) })
                        currentIndex: backend.imageInfo.selected_series >= 0 ? backend.imageInfo.selected_series : -1
                        enabled: workspace.editable && !backend.busy
                        Accessible.name: qsTr("图像组（series）")
                        onActivated: backend.selectSeries(currentIndex)
                    }
                    UI.ChoiceBox {
                        objectName: "countTimeChoice"
                        visible: backend.imageInfo.time_count > 1
                        Layout.fillWidth: true
                        placeholder: qsTr("请选择时间点")
                        model: backend.imageInfo.time_count || 0
                        currentIndex: backend.imageInfo.selected_time >= 0 ? backend.imageInfo.selected_time : -1
                        displayText: currentIndex < 0 ? placeholder : qsTr("时间点 %1").arg(currentIndex + 1)
                        itemText: function(value, index) { return qsTr("时间点 %1").arg(index + 1) }
                        enabled: workspace.editable && !backend.busy
                        Accessible.name: qsTr("时间点")
                        onActivated: backend.selectTime(currentIndex)
                    }
                }
                Text { visible: workspace.needsSeriesSelection || workspace.needsTimeSelection; text: workspace.needsSeriesSelection ? qsTr("此文件包含多个图像组，请明确选择本次分析的图像组。") : qsTr("此图像组包含多个时间点，请明确选择本次分析的时间点。"); color: UI.Theme.warning; font.pixelSize: 12; Layout.fillWidth: true; wrapMode: Text.WordWrap }
            }
            Item { Layout.fillWidth: true; Layout.minimumHeight: 18; Layout.preferredHeight: 18 }
            UI.Surface {
                objectName: "countMappingCard"
                Layout.fillWidth: true
                spacing: 12
                RowLayout {
                    Layout.fillWidth: true
                    Text { text: qsTr("分析通道"); color: UI.Theme.text; font.pixelSize: 14; font.weight: Font.DemiBold; Layout.fillWidth: true }
                    UI.ChoiceBox { objectName: "countMappingMode"; model: [qsTr("自动识别"), qsTr("手动指定")]; currentIndex: backend.countMappingMode === "manual" ? 1 : 0; enabled: workspace.metadataReady && workspace.editable; Layout.preferredWidth: 180; Accessible.name: qsTr("通道识别方式"); onActivated: backend.setCountMappingMode(currentIndex === 0 ? "auto" : "manual") }
                }
                Text { text: qsTr("DAPI 用于检测细胞核，辅助通道独立检测胞体；匹配为同一细胞的结果只计数一次。"); color: UI.Theme.textSecondary; font.pixelSize: 12; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                Text { text: qsTr("DAPI 计数通道"); color: UI.Theme.text; font.pixelSize: 12; font.weight: Font.Medium }
                UI.ChoiceBox { objectName: "countDapiChoice"; Layout.fillWidth: true; model: page.sourceChoices; currentIndex: backend.countDapiChannel + 1; enabled: workspace.metadataReady && workspace.editable; Accessible.name: qsTr("DAPI 计数通道"); onActivated: backend.setCountDapiChannel(currentIndex - 1) }
                Text { visible: workspace.metadataReady && backend.countDapiChannel < 0; text: qsTr("未识别到可靠的 DAPI 标记，请手动指定源通道。"); color: UI.Theme.warning; font.pixelSize: 12; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                Text { text: qsTr("辅助通道"); color: UI.Theme.text; font.pixelSize: 12; font.weight: Font.Medium }
                UI.ChoiceBox { objectName: "countSupportChoice"; Layout.fillWidth: true; model: page.sourceChoices; currentIndex: backend.countSupportChannel + 1; enabled: workspace.metadataReady && workspace.editable; Accessible.name: qsTr("辅助通道"); onActivated: backend.setCountSupportChannel(currentIndex - 1) }
                Text { text: qsTr("辅助标记物"); color: UI.Theme.text; font.pixelSize: 12; font.weight: Font.Medium }
                UI.ChoiceBox { objectName: "countSupportMarker"; Layout.fillWidth: true; model: page.supportMarkers; placeholder: qsTr("请选择标记物"); currentIndex: page.customMarkerSelected || page.customSupportMarker ? 3 : page.supportMarkers.indexOf(backend.countSupportMarker); enabled: workspace.metadataReady && workspace.editable && backend.countSupportChannel >= 0; Accessible.name: qsTr("辅助标记物"); onActivated: { page.customMarkerSelected = currentIndex === 3; if (currentIndex < 3) backend.setCountSupportMarker(page.supportMarkers[currentIndex]); else backend.setCountSupportMarker("") } }
                TextField { objectName: "countCustomSupportMarker"; visible: page.customMarkerSelected || page.customSupportMarker; Layout.fillWidth: true; text: page.customSupportMarker ? backend.countSupportMarker : ""; placeholderText: qsTr("输入辅助标记物名称"); enabled: workspace.editable; selectByMouse: true; Accessible.name: qsTr("自定义辅助标记物"); onEditingFinished: backend.setCountSupportMarker(text.trim()) }
                Text { visible: backend.countSupportChannel >= 0 && backend.countSupportChannel === backend.countDapiChannel; text: qsTr("DAPI 与辅助通道必须使用不同的源通道。"); color: UI.Theme.warning; font.pixelSize: 12; Layout.fillWidth: true; wrapMode: Text.WordWrap }
            }
            Item { Layout.fillWidth: true; Layout.minimumHeight: 18; Layout.preferredHeight: 18 }
            UI.Surface {
                objectName: "countDisplayCard"
                Layout.fillHeight: workspace.wide
                Layout.minimumHeight: implicitHeight
                Layout.fillWidth: true
                fillContent: workspace.wide
                spacing: 12
                Text { text: qsTr("显示通道与颜色"); color: UI.Theme.text; font.pixelSize: 14; font.weight: Font.DemiBold }
                Text { text: qsTr("选择审核时可见的源通道，并设置显示颜色。"); color: UI.Theme.textSecondary; font.pixelSize: 12; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                Text { visible: !workspace.metadataReady; text: qsTr("导入图像后选择显示通道。"); color: UI.Theme.textSecondary; font.pixelSize: 12; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                ColumnLayout {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    spacing: 0
                    Repeater {
                        model: workspace.metadataReady ? backend.imageInfo.channel_count : 0
                        delegate: Item {
                            id: channelSlot
                            required property int index
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            Layout.minimumHeight: implicitHeight
                            implicitHeight: channelRow.implicitHeight + 12
                            Rectangle {
                                id: channelRow
                                readonly property int index: channelSlot.index
                                objectName: "countSourceChannel_" + index
                                width: parent.width
                                height: implicitHeight
                                anchors.verticalCenter: parent.verticalCenter
                                implicitHeight: channelBody.implicitHeight + 18
                                radius: 7; color: UI.Theme.surfaceAlt
                                RowLayout {
                                    id: channelBody
                                    anchors.left: parent.left; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                                    anchors.margins: 9
                                    spacing: 9
                                    CheckBox { objectName: "countDisplayChannel_" + channelRow.index; text: page.sourceChoices[channelRow.index + 1]; checked: backend.countDisplayChannels.indexOf(channelRow.index) >= 0; enabled: workspace.editable; Layout.fillWidth: true; Layout.minimumWidth: 60; Accessible.name: qsTr("预选显示通道 %1").arg(channelRow.index + 1); onToggled: backend.setCountDisplayChannel(channelRow.index, checked) }
                                    Rectangle { width: 9; height: 9; radius: 4.5; color: page.colorFor(channelRow.index); border.color: UI.Theme.borderStrong }
                                    TextField {
                                        objectName: "countColor_" + channelRow.index
                                        Layout.preferredWidth: 105
                                        text: (backend.imageInfo.channel_colors || [])[channelRow.index] || ""
                                        placeholderText: qsTr("默认颜色")
                                        validator: RegularExpressionValidator { regularExpression: /#[0-9a-fA-F]{6}|^$/ }
                                        selectByMouse: true
                                        enabled: workspace.editable
                                        Accessible.name: qsTr("通道 %1 的显示颜色").arg(channelRow.index + 1)
                                        onEditingFinished: if (acceptableInput) backend.setCountChannelColor(channelRow.index, text)
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
        ColumnLayout {
            objectName: "countSupportColumn"
            Layout.row: workspace.wide ? 0 : workspace.statusFirst ? 0 : 1
            Layout.column: workspace.wide ? 1 : 0
            Layout.fillWidth: !workspace.wide
            Layout.fillHeight: workspace.wide
            Layout.preferredWidth: workspace.wide ? 282 : -1
            Layout.alignment: Qt.AlignTop
            spacing: 18
            UI.Surface {
                objectName: "countStatusCard"
                Layout.fillHeight: workspace.wide
                Layout.minimumHeight: implicitHeight
                Layout.fillWidth: true
                fillContent: workspace.wide
                spacing: 14
                Text { text: qsTr("分析状态"); color: UI.Theme.text; font.pixelSize: 14; font.weight: Font.DemiBold }
                Item {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    implicitHeight: countStatusGroups.implicitHeight
                    ColumnLayout {
                        id: countStatusGroups
                        objectName: "countStatusGroups"
                        anchors.centerIn: parent
                        width: parent.width
                        height: Math.min(parent.height, implicitHeight + 16)
                        spacing: 0
                        ColumnLayout {
                            Layout.fillWidth: true
                            Layout.fillHeight: false
                            spacing: 12
                            UI.StatusBadge { text: workspace.stateText(backend.countInputError ? "blocked" : backend.status); tone: workspace.stateTone(backend.countInputError ? "blocked" : backend.status) }
                            Text { text: backend.countInputError ? qsTr("需要处理") : backend.stage || (workspace.hasImage ? qsTr("准备开始计数") : qsTr("先导入一张图像")); color: UI.Theme.text; font.pixelSize: 17; font.weight: Font.Medium; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                            Text { objectName: "countInputError"; text: backend.countInputError || backend.message || qsTr("确认 DAPI、辅助通道与显示设置后开始计数。"); color: backend.countInputError ? UI.Theme.warning : UI.Theme.textSecondary; font.pixelSize: 12; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                            UI.ActivityBar {
                                objectName: "countActivityBar"
                                active: backend.activeModuleBusy && !page.reviewing
                                        && backend.status !== "completed" && backend.status !== "cancelled"
                                cancelled: backend.status === "cancelling"
                                Layout.fillWidth: true
                                progress: -1
                            }
                        }
                        Item { Layout.fillHeight: true; Layout.minimumHeight: 14; Layout.maximumHeight: 30; implicitHeight: 14 }
                        ColumnLayout {
                            Layout.fillWidth: true
                            Layout.fillHeight: false
                            spacing: 12
                            UI.ActionButton { objectName: "countStart"; text: qsTr("开始自动计数"); kind: "primary"; symbol: "play"; Layout.fillWidth: true; visible: !backend.activeModuleBusy; enabled: workspace.metadataReady && backend.countCalibrationReady && !workspace.needsTimeSelection && backend.countDapiChannel >= 0 && (backend.countSupportChannel >= 0 && backend.countSupportChannel !== backend.countDapiChannel && backend.countSupportMarker.length > 0) && backend.countDisplayChannels.length > 0 && workspace.moduleReady && workspace.editable && !backend.busy && !page.editor.busy; onClicked: backend.startAnalysis() }
                            UI.ActionButton { objectName: "countCancel"; text: backend.status === "cancelling" ? qsTr("正在取消…") : qsTr("取消当前任务"); kind: "danger"; Layout.fillWidth: true; visible: backend.activeModuleBusy; enabled: backend.status !== "cancelling"; onClicked: backend.cancelAnalysis() }
                            Text { visible: !workspace.moduleReady && !backend.activeModuleBusy; text: qsTr("开始前，请在“功能与维护”中安装细胞计数模块。"); color: UI.Theme.warning; font.pixelSize: 12; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                        }
                    }
                }
            }
            UI.Surface {
                objectName: "countWorkflowCard"
                Layout.fillHeight: workspace.wide
                Layout.minimumHeight: implicitHeight
                Layout.fillWidth: true
                color: UI.Theme.surface
                border.color: UI.Theme.border
                fillContent: workspace.wide
                spacing: 14
                Text { text: qsTr("分析流程"); color: UI.Theme.textSecondary; font.pixelSize: 11; font.weight: Font.DemiBold }
                Item {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    implicitHeight: countWorkflowGroups.implicitHeight
                    ColumnLayout {
                        id: countWorkflowGroups
                        objectName: "countWorkflowGroups"
                        anchors.centerIn: parent
                        width: parent.width
                        height: Math.min(parent.height, implicitHeight + 56)
                        spacing: 0
                        Repeater {
                            model: [{number:"01", title:qsTr("导入与通道确认"), detail:qsTr("核对 DAPI、辅助标记与显示通道")}, {number:"02", title:qsTr("自动计数"), detail:qsTr("检测细胞核与胞体，并合并对应对象")}, {number:"03", title:qsTr("逐层审核"), detail:qsTr("点击高可能性对象加入计数，其他对象需选择后加入")}, {number:"04", title:qsTr("确认与保存"), detail:qsTr("保存计数表、叠加图与可继续编辑的会话")}]
                            delegate: Item {
                                required property var modelData
                                Layout.fillWidth: true
                                Layout.fillHeight: true
                                Layout.minimumHeight: implicitHeight
                                implicitHeight: countWorkflowRow.implicitHeight + 14
                                RowLayout {
                                    id: countWorkflowRow
                                    anchors.centerIn: parent
                                    width: parent.width
                                    spacing: 11
                                    Text { text: modelData.number; color: UI.Theme.textSecondary; font.pixelSize: 10; Layout.alignment: Qt.AlignTop; Layout.topMargin: 2 }
                                    ColumnLayout { Layout.fillWidth: true; spacing: 4; Text { text: modelData.title; color: UI.Theme.textSecondary; font.pixelSize: 11; font.weight: Font.DemiBold; Layout.fillWidth: true; wrapMode: Text.WordWrap } Text { text: modelData.detail; color: UI.Theme.textSecondary; font.pixelSize: 11; Layout.fillWidth: true; wrapMode: Text.WordWrap } }
                                }
                            }
                        }
                    }
                }
            }
            UI.Surface {
                objectName: "countInformationCard"
                Layout.fillHeight: workspace.wide
                Layout.minimumHeight: implicitHeight
                Layout.fillWidth: true
                fillContent: workspace.wide
                spacing: 12
                Text { text: qsTr("模块信息"); color: UI.Theme.textSecondary; font.pixelSize: 13; font.weight: Font.DemiBold }
                Item {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    implicitHeight: countInformationGroups.implicitHeight
                    ColumnLayout {
                        id: countInformationGroups
                        objectName: "countInformationGroups"
                        anchors.centerIn: parent
                        width: parent.width
                        height: Math.min(parent.height, implicitHeight + 16)
                        spacing: 0
                        Text { text: qsTr("在 GitHub 查看模块的使用说明与分析方法。"); color: UI.Theme.textSecondary; font.pixelSize: 12; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                        Item { Layout.fillHeight: true; Layout.minimumHeight: 12; Layout.maximumHeight: 28; implicitHeight: 12 }
                        UI.ActionButton { objectName: "countGitHubButton"; text: qsTr("在 GitHub 查看"); symbol: "external"; Layout.fillWidth: true; onClicked: backend.openModuleGitHub(backend.activeModuleId) }
                    }
                }
            }
        }
    }
}
