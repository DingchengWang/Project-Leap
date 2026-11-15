import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components" as UI

GridLayout {
    id: inputPage
    required property var workspace
    readonly property var markerDefinitions: [{key: "DAPI", label: "DAPI"}, {key: "eGFP", label: "eGFP"}, {key: "GFAP", label: "GFAP"}, {key: "Target", label: qsTr("待测通道")}]
    readonly property var measurementMarkers: ["KCNN1", "KCNN2", "KCNN3", "KCNJ10"]
    readonly property bool gfapOnly: workspace.hasImage && backend.channelRoles.indexOf("DAPI") >= 0 && backend.channelRoles.indexOf("GFAP") >= 0 && backend.channelRoles.indexOf("eGFP") < 0
    readonly property bool invalidAgeProfile: gfapOnly && backend.ageProfile === "neonatal"
    readonly property var sourceChannelChoices: {
        var choices = [qsTr("未包含")]
        var count = workspace.metadataReady ? backend.imageInfo.channel_count : 0
        for (var i = 0; i < count; ++i) {
            choices.push("C" + (i + 1))
        }
        return choices
    }
    columns: workspace.wide ? 2 : 1
    columnSpacing: 20
    rowSpacing: 20
    Layout.fillWidth: true
    ColumnLayout {
        objectName: "imageInputColumn"
        Layout.row: workspace.wide ? 0 : workspace.statusFirst ? 1 : 0
        Layout.column: 0
        Layout.fillWidth: true
        Layout.fillHeight: workspace.wide
        Layout.alignment: Qt.AlignTop
        spacing: 18
        ColumnLayout {
            objectName: "fluorescenceAgeControl"
            Layout.fillWidth: true
            spacing: 7
            RowLayout {
                Layout.fillWidth: true
                spacing: 14
                Text { text: qsTr("图像年龄"); color: UI.Theme.text; font.pixelSize: 13; font.weight: Font.Medium }
                UI.NativeAgeSelector {
                    objectName: "fluorescenceAgeRail"
                    Layout.fillWidth: true
                }
            }
            Text {
                objectName: "fluorescenceAgeNotice"
                visible: inputPage.gfapOnly
                text: qsTr("图像为DAPI+GFAP通道时，当前程序仅能识别成熟星形胶质细胞")
                color: UI.Theme.warning
                font.pixelSize: 11
                Layout.fillWidth: true
                wrapMode: Text.WordWrap
            }
            Text {
                objectName: "fluorescenceInvalidAgeNotice"
                visible: inputPage.invalidAgeProfile
                text: qsTr("请将图像年龄改为“自动”或 Mature 后再开始。")
                color: UI.Theme.warning
                font.pixelSize: 11
                Layout.fillWidth: true
                wrapMode: Text.WordWrap
            }
        }
        UI.Surface {
            Layout.fillWidth: true
            spacing: workspace.hasSourcePreview ? 12 : 16
            RowLayout {
                Layout.fillWidth: true
                Text { text: qsTr("图像导入"); color: UI.Theme.text; font.pixelSize: 14; font.weight: Font.DemiBold; Layout.fillWidth: true }
            }
            Rectangle {
                id: dropZone
                objectName: "sourceDropZone"
                Layout.fillWidth: true
                implicitHeight: workspace.hasSourcePreview ? 200 : workspace.hasImage ? 148 : 190
                radius: 10
                color: fileDrop.containsDrag ? UI.Theme.accentSoft : UI.Theme.surfaceAlt
                border.color: fileDrop.containsDrag ? UI.Theme.accent : UI.Theme.border
                border.width: fileDrop.containsDrag ? 2 : 1
                DropArea {
                    id: fileDrop
                    anchors.fill: parent
                    enabled: workspace.editable && !backend.busy
                    onDropped: function(drop) {
                        if (drop.hasUrls && drop.urls.length > 1) {
                            workspace.warnMultiFileDrop()
                            drop.accepted = false
                            return
                        }
                        if (drop.hasUrls && drop.urls.length === 1) {
                            backend.inspectImage(drop.urls[0].toString())
                            drop.acceptProposedAction()
                        }
                    }
                }
                ColumnLayout {
                    visible: !workspace.hasImage
                    anchors.centerIn: parent
                    width: parent.width - 30
                    spacing: 10
                    Rectangle { Layout.alignment: Qt.AlignHCenter; width: 44; height: 44; radius: 13; color: UI.Theme.accentSoft; UI.Icon { anchors.centerIn: parent; width: 24; height: 24; kind: "import"; ink: UI.Theme.textMuted } }
                    Text { text: fileDrop.containsDrag ? qsTr("松开以导入图像") : qsTr("将图像拖到这里"); color: UI.Theme.textSecondary; font.pixelSize: 13; font.weight: Font.Medium; Layout.alignment: Qt.AlignHCenter }
                    UI.ActionButton { objectName: "fluorescenceChooseImage"; text: qsTr("选择图像…"); compact: true; enabled: workspace.editable && !backend.busy; Layout.alignment: Qt.AlignHCenter; onClicked: backend.chooseImage() }
                    Text { text: qsTr("当前模式仅支持.tif/.tiff格式"); color: UI.Theme.textSecondary; font.pixelSize: 11; Layout.alignment: Qt.AlignHCenter; Layout.fillWidth: true; horizontalAlignment: Text.AlignHCenter; wrapMode: Text.WordWrap }
                }
                RowLayout {
                    visible: workspace.hasImage
                    anchors.fill: parent
                    anchors.margins: 20
                    spacing: 16
                    Rectangle {
                        visible: workspace.hasSourcePreview
                        Layout.preferredWidth: 160
                        Layout.preferredHeight: 160
                        radius: 7
                        color: "#111820"
                        border.color: "#273140"
                        clip: true
                        Image {
                            id: sourcePreviewImage
                            objectName: "sourcePreviewImage"
                            anchors.fill: parent
                            anchors.margins: 1
                            source: backend.imageInfo.thumbnail_url || ""
                            fillMode: Image.PreserveAspectFit
                            asynchronous: true
                            cache: false
                            sourceSize: Qt.size(768, 768)
                            Accessible.name: workspace.sourcePreviewLabel()
                        }
                        Text {
                            anchors.centerIn: parent
                            width: parent.width - 24
                            visible: sourcePreviewImage.status === Image.Loading || sourcePreviewImage.status === Image.Error
                            text: sourcePreviewImage.status === Image.Error ? qsTr("预览未能载入") : qsTr("正在载入预览")
                            color: "#A9B4C4"
                            font.pixelSize: 11
                            horizontalAlignment: Text.AlignHCenter
                            wrapMode: Text.WordWrap
                        }
                    }
                    Rectangle {
                        visible: !workspace.hasSourcePreview
                        Layout.preferredWidth: 69
                        Layout.preferredHeight: 84
                        radius: 9
                        color: UI.Theme.surface
                        border.color: UI.Theme.border
                        ColumnLayout { anchors.centerIn: parent; spacing: 7; UI.Icon { kind: "image"; ink: UI.Theme.textMuted; Layout.alignment: Qt.AlignHCenter; width: 29; height: 29 } Text { text: "TIFF"; color: UI.Theme.textSecondary; font.pixelSize: 10; font.weight: Font.DemiBold; font.letterSpacing: 1; Layout.alignment: Qt.AlignHCenter } }
                    }
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 8
                        Text { text: backend.imageInfo.name || ""; color: UI.Theme.text; font.pixelSize: 14; font.weight: Font.DemiBold; Layout.fillWidth: true; wrapMode: Text.WrapAnywhere; maximumLineCount: 2; elide: Text.ElideMiddle }
                        Text { text: workspace.shapeText() + "  ·  " + (backend.imageInfo.dtype || ""); color: UI.Theme.textSecondary; font.pixelSize: 11; Layout.fillWidth: true }
                        Text { visible: workspace.hasSourcePreview; text: workspace.sourcePreviewLabel(); color: UI.Theme.textSecondary; font.pixelSize: 11; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                        UI.ActionButton { text: qsTr("更换图像"); kind: "subtle"; compact: true; enabled: workspace.editable && !backend.busy; onClicked: backend.chooseImage() }
                    }
                }
            }
            GridLayout {
                visible: backend.imageInfo.series_count > 1 || backend.imageInfo.time_count > 1
                Layout.fillWidth: true
                columns: 2
                columnSpacing: 16
                ColumnLayout {
                    visible: backend.imageInfo.series_count > 1
                    Layout.fillWidth: true
                    spacing: 7
                    Text { text: qsTr("图像组（series）"); color: UI.Theme.textSecondary; font.pixelSize: 11 }
                    UI.ChoiceBox {
                        objectName: "seriesChoice"
                        Accessible.name: qsTr("图像组（series）")
                        Layout.fillWidth: true
                        placeholder: qsTr("请选择图像组")
                        model: (backend.imageInfo.series || []).map(function(item, index) { return qsTr("图像组 %1 · %2").arg(index + 1).arg(item.name) })
                        currentIndex: backend.imageInfo.selected_series >= 0 ? backend.imageInfo.selected_series : -1
                        enabled: workspace.editable && !backend.busy
                        onActivated: backend.selectSeries(currentIndex)
                    }
                }
                ColumnLayout {
                    visible: backend.imageInfo.time_count > 1
                    Layout.fillWidth: true
                    spacing: 7
                    Text { text: qsTr("时间点"); color: UI.Theme.textSecondary; font.pixelSize: 11 }
                    UI.ChoiceBox {
                        id: timeChoice
                        objectName: "timeChoice"
                        Layout.fillWidth: true
                        placeholder: qsTr("请选择时间点")
                        model: backend.imageInfo.time_count || 0
                        currentIndex: backend.imageInfo.selected_time >= 0 ? backend.imageInfo.selected_time : -1
                        displayText: currentIndex < 0 ? placeholder : qsTr("时间点 %1").arg(currentIndex + 1)
                        itemText: function(value, index) { return qsTr("时间点 %1").arg(index + 1) }
                        Accessible.name: qsTr("时间点")
                        enabled: workspace.editable && !backend.busy
                        onActivated: backend.selectTime(currentIndex)
                    }
                }
            }
            Text {
                visible: workspace.needsSeriesSelection || workspace.needsTimeSelection
                text: workspace.needsSeriesSelection ? qsTr("此文件包含多个图像组，请明确选择本次分析的图像组。") : qsTr("此图像组包含多个时间点，请明确选择本次分析的时间点。")
                color: UI.Theme.warning
                font.pixelSize: 11
                Layout.fillWidth: true
                wrapMode: Text.WordWrap
            }
            GridLayout {
                visible: workspace.metadataReady
                Layout.fillWidth: true
                columns: 2
                rowSpacing: workspace.hasSourcePreview ? 10 : 17
                columnSpacing: 20
                UI.InfoCell { label: qsTr("图像尺寸"); value: workspace.shapeText(); Layout.fillWidth: true }
                UI.InfoCell { label: qsTr("通道 / Z 层"); value: qsTr("%1 通道 / %2 层").arg(backend.imageInfo.channel_count || 0).arg(backend.imageInfo.z_count || 0); Layout.fillWidth: true }
                UI.InfoCell { label: qsTr("轴顺序 / 数据类型"); value: (backend.imageInfo.axes || "—") + " / " + (backend.imageInfo.dtype || "—"); Layout.fillWidth: true }
                UI.InfoCell { label: qsTr("体素标定 X × Y × Z"); value: workspace.calibrationText(); Layout.fillWidth: true }
            }
        }

        UI.Surface {
            objectName: "channelMappingCard"
            Layout.fillWidth: true
            Layout.fillHeight: workspace.wide
            Layout.minimumHeight: implicitHeight
            fillContent: workspace.wide
            spacing: workspace.hasSourcePreview ? 10 : 14
            Item {
                id: mappingHeader
                objectName: "channelMappingHeader"
                Layout.fillWidth: true
                implicitHeight: mappingTitle.implicitHeight
                Text {
                    id: mappingTitle
                    objectName: "channelMappingTitle"
                    text: qsTr("通道映射")
                    color: UI.Theme.text
                    font.pixelSize: 14
                    font.weight: Font.DemiBold
                    anchors.left: parent.left
                    anchors.top: parent.top
                }
            }
            RowLayout {
                objectName: "channelMappingDescription"
                Layout.fillWidth: true
                spacing: 16
                Text { text: qsTr("按四类标记物选择实际源通道，缺失项选择“未包含”。导入图像后即可选择通道。"); color: UI.Theme.textSecondary; font.pixelSize: 11; Layout.fillWidth: true; Layout.minimumWidth: 0; wrapMode: Text.WordWrap; Layout.alignment: Qt.AlignTop }
                Text { objectName: "channelMappingSourceState"; visible: workspace.metadataReady || workspace.needsSeriesSelection; text: workspace.metadataReady ? qsTr("%1 个源通道").arg(backend.imageInfo.channel_count) : workspace.needsSeriesSelection ? qsTr("先选择图像组") : ""; color: UI.Theme.textSecondary; font.pixelSize: 11; Layout.minimumWidth: implicitWidth; Layout.alignment: Qt.AlignTop }
            }
            ColumnLayout {
                objectName: "markerMappingSlots"
                uniformCellSizes: true
                Layout.fillWidth: true
                Layout.fillHeight: true
                spacing: 0
                Repeater {
                    model: inputPage.markerDefinitions
                    delegate: Item {
                        required property var modelData
                        required property int index
                        objectName: "markerMappingSlot_" + modelData.key
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        Layout.minimumHeight: implicitHeight
                        implicitHeight: markerRow.implicitHeight + (workspace.hasSourcePreview ? 10 : 14)
                        Rectangle {
                            id: markerRow
                            readonly property var modelData: parent.modelData
                            readonly property int sourceIndex: backend.markerSourceIndices[parent.index]
                            objectName: "markerMappingRow_" + modelData.key
                            width: parent.width
                            height: implicitHeight
                            anchors.verticalCenter: parent.verticalCenter
                            implicitHeight: workspace.hasSourcePreview ? 42 : 47
                            radius: 8
                            color: UI.Theme.surfaceAlt
                            RowLayout {
                                anchors.fill: parent
                                anchors.leftMargin: 12
                                anchors.rightMargin: 8
                                spacing: 11
                                Rectangle { width: 7; height: 7; radius: 3.5; color: workspace.channelColor(markerRow.modelData.key) }
                                Text {
                                    text: markerRow.modelData.label
                                    color: UI.Theme.textSecondary
                                    font.pixelSize: 11
                                    font.weight: Font.DemiBold
                                    Layout.preferredWidth: 80
                                    Layout.minimumWidth: implicitWidth
                                }
                                Loader {
                                    active: markerRow.modelData.key === "Target"
                                    visible: active
                                    Layout.preferredWidth: 150
                                    Layout.preferredHeight: 32
                                    sourceComponent: Component {
                                        UI.ChoiceBox {
                                            objectName: "measurementMarkerChoice"
                                            model: inputPage.measurementMarkers
                                            placeholder: qsTr("请选择标记物")
                                            currentIndex: inputPage.measurementMarkers.indexOf(backend.measurementMarker)
                                            enabled: workspace.editable && workspace.metadataReady
                                            Accessible.name: qsTr("待测通道的标记物")
                                            onActivated: backend.setMeasurementMarker(currentText)
                                        }
                                    }
                                }
                                UI.ChoiceBox {
                                    objectName: "markerSource_" + markerRow.modelData.key
                                    model: inputPage.sourceChannelChoices
                                    currentIndex: markerRow.sourceIndex >= 0 && markerRow.sourceIndex < inputPage.sourceChannelChoices.length - 1 ? markerRow.sourceIndex + 1 : 0
                                    enabled: workspace.editable && workspace.metadataReady && (markerRow.modelData.key !== "Target" || backend.measurementMarker.length > 0)
                                    Layout.fillWidth: true
                                    Layout.minimumWidth: 118
                                    Layout.preferredHeight: 32
                                    Accessible.name: qsTr("%1 的源通道").arg(markerRow.modelData.label)
                                    onActivated: backend.assignMarkerSource(markerRow.modelData.key, currentIndex - 1)
                                }
                            }
                        }
                    }
                }
            }

        }
    }

    ColumnLayout {
        objectName: "analysisSupportColumn"
        Layout.row: workspace.wide ? 0 : workspace.statusFirst ? 0 : 1
        Layout.column: workspace.wide ? 1 : 0
        Layout.fillWidth: !workspace.wide
        Layout.fillHeight: workspace.wide
        Layout.preferredWidth: workspace.wide ? 282 : -1
        Layout.alignment: Qt.AlignTop
        spacing: 18
        UI.Surface {
            objectName: "fluorescenceStatusCard"
            Layout.fillHeight: workspace.wide
            Layout.minimumHeight: implicitHeight
            Layout.fillWidth: true
            fillContent: workspace.wide
            spacing: 17
            RowLayout {
                Layout.fillWidth: true
                Text { text: qsTr("分析状态"); color: UI.Theme.text; font.pixelSize: 14; font.weight: Font.DemiBold; Layout.fillWidth: true }
            }
            Item {
                Layout.fillWidth: true
                Layout.fillHeight: true
                implicitHeight: fluorescenceStatusGroups.implicitHeight
                ColumnLayout {
                    id: fluorescenceStatusGroups
                    objectName: "fluorescenceStatusGroups"
                    anchors.centerIn: parent
                    width: parent.width
                    height: Math.min(parent.height, implicitHeight + 16)
                    spacing: 0
                    ColumnLayout {
                        Layout.fillWidth: true
                        Layout.fillHeight: false
                        spacing: 12
                        UI.StatusBadge { text: workspace.stateText(backend.status); tone: workspace.stateTone(backend.status) }
                        Text { text: workspace.waitingReview ? qsTr("在 Fiji 中审核 ROI") : backend.stage || (workspace.hasImage ? qsTr("准备开始分析") : qsTr("先导入一张图像")); color: UI.Theme.text; font.pixelSize: 17; font.weight: Font.Medium; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                        Text { text: backend.message || (workspace.hasImage ? qsTr("确认源通道与标记物的对应关系后即可开始。") : qsTr("图像元数据与通道将在读取后显示。")); color: UI.Theme.textSecondary; font.pixelSize: 12; lineHeight: 1.35; Layout.fillWidth: true; wrapMode: Text.Wrap }
                        UI.ActivityBar {
                            objectName: "fluorescenceActivityBar"
                            active: backend.activeModuleBusy && !workspace.waitingReview
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
                        UI.ActionButton { objectName: "fluorescenceStart"; text: qsTr("开始分析"); symbol: "play"; kind: "primary"; Layout.fillWidth: true; enabled: workspace.metadataReady && !workspace.needsTimeSelection && !inputPage.invalidAgeProfile && workspace.moduleReady && workspace.editable && !backend.busy; visible: !backend.activeModuleBusy; onClicked: backend.startAnalysis() }
                        UI.ActionButton { text: qsTr("前往 Fiji 审核"); symbol: "external"; kind: "primary"; visible: workspace.waitingReview; Layout.fillWidth: true; onClicked: backend.showFiji() }
                        UI.ActionButton { text: backend.status === "cancelling" ? qsTr("正在取消…") : qsTr("取消当前任务"); symbol: "stop"; kind: "danger"; visible: backend.activeModuleBusy; enabled: backend.status !== "cancelling"; Layout.fillWidth: true; onClicked: backend.cancelAnalysis() }
                        UI.ActionButton { text: qsTr("查看分析结果"); symbol: "folder"; visible: backend.status === "completed"; Layout.fillWidth: true; onClicked: { backend.setPage("jobs"); backend.openResults() } }
                        Text { visible: !workspace.moduleReady && !backend.activeModuleBusy; text: qsTr("开始前，请在“功能与维护”中安装星形胶质细胞荧光强度分析模块。"); color: UI.Theme.warning; font.pixelSize: 11; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                        Text { visible: workspace.waitingReview; text: qsTr("在 Fiji 完成确认后，分析将自动继续。"); color: UI.Theme.warning; font.pixelSize: 11; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                    }
                }
            }
        }
        UI.Surface {
            objectName: "fluorescenceWorkflowCard"
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
                implicitHeight: fluorescenceWorkflowGroups.implicitHeight
                ColumnLayout {
                    id: fluorescenceWorkflowGroups
                    objectName: "fluorescenceWorkflowGroups"
                    anchors.centerIn: parent
                    width: parent.width
                    height: Math.min(parent.height, implicitHeight + 56)
                    spacing: 0
                    Repeater {
                        model: [{number:"01",title:qsTr("导入与通道确认"),detail:qsTr("读取元数据，确认通道标记物")},{number:"02",title:qsTr("自动分析"),detail:qsTr("使用已安装模块的分析流程")},{number:"03",title:qsTr("Fiji 人工审核"),detail:qsTr("检查、编辑并确认最终 ROI")},{number:"04",title:qsTr("测量与结果归档"),detail:qsTr("保存正式结果及最终 ROI")}]
                        delegate: Item {
                            required property var modelData
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            Layout.minimumHeight: implicitHeight
                            implicitHeight: fluorescenceWorkflowRow.implicitHeight + 14
                            RowLayout {
                                id: fluorescenceWorkflowRow
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
            objectName: "fluorescenceInformationCard"
            Layout.fillHeight: workspace.wide
            Layout.minimumHeight: implicitHeight
            Layout.fillWidth: true
            fillContent: workspace.wide
            spacing: 12
            RowLayout {
                Layout.fillWidth: true
                Text { text: qsTr("模块信息"); color: UI.Theme.textSecondary; font.pixelSize: 13; font.weight: Font.DemiBold; Layout.fillWidth: true }
            }
            Item {
                Layout.fillWidth: true
                Layout.fillHeight: true
                implicitHeight: fluorescenceInformationGroups.implicitHeight
                ColumnLayout {
                    id: fluorescenceInformationGroups
                    objectName: "fluorescenceInformationGroups"
                    anchors.centerIn: parent
                    width: parent.width
                    height: Math.min(parent.height, implicitHeight + 16)
                    spacing: 0
                    Text { text: qsTr("在 GitHub 查看模块的使用说明与分析方法。"); color: UI.Theme.textSecondary; font.pixelSize: 12; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                    Item { Layout.fillHeight: true; Layout.minimumHeight: 12; Layout.maximumHeight: 28; implicitHeight: 12 }
                    UI.ActionButton { objectName: "fluorescenceGitHubButton"; text: qsTr("在 GitHub 查看"); symbol: "external"; Layout.fillWidth: true; onClicked: backend.openModuleGitHub(backend.activeModuleId) }
                }
            }
        }
    }
}
