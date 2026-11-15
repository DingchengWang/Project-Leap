import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Button {
    id: control
    property string kind: "secondary"
    property string symbol: ""
    property bool compact: false
    readonly property bool primary: kind === "primary"
    readonly property color foreground: !enabled ? Theme.textDisabled : primary ? Theme.white : kind === "danger" ? Theme.error : Theme.text
    implicitHeight: compact ? 32 : 38
    implicitWidth: row.implicitWidth + (compact ? 22 : 28)
    leftPadding: compact ? 11 : 14
    rightPadding: leftPadding
    topPadding: 0
    bottomPadding: 0
    hoverEnabled: true
    focusPolicy: Qt.StrongFocus
    Accessible.name: text
    font.pixelSize: 13
    font.weight: Font.Medium
    background: Rectangle {
        radius: 8
        color: !control.enabled ? Theme.surfaceAlt : control.primary ? (control.down ? Theme.accentPressed : control.hovered ? Theme.accentHover : Theme.accent) : control.kind === "subtle" ? (control.hovered ? Theme.surfaceHover : "transparent") : (control.down ? Theme.surfacePressed : control.hovered ? Theme.surfaceHover : Theme.surface)
        border.width: control.activeFocus ? 2 : 1
        border.color: control.activeFocus ? Theme.accent : control.primary || control.kind === "subtle" ? "transparent" : Theme.border
    }
    contentItem: Item {
        implicitWidth: row.implicitWidth
        Row {
            id: row
            anchors.centerIn: parent
            spacing: 7
            Icon { visible: control.symbol !== ""; kind: control.symbol; ink: control.foreground; width: 16; height: 16; anchors.verticalCenter: parent.verticalCenter }
            Text { text: control.text; color: control.foreground; font: control.font; anchors.verticalCenter: parent.verticalCenter }
        }
    }
}
