import QtQuick

Rectangle {
    id: badge
    property string text: ""
    property string tone: "neutral"
    readonly property color ink: tone === "success" ? Theme.success : tone === "warning" ? Theme.warning : tone === "error" ? Theme.error : tone === "active" ? Theme.accent : Theme.textSecondary
    implicitWidth: label.implicitWidth + 26
    implicitHeight: 25
    radius: 6
    color: tone === "success" ? Theme.successSoft : tone === "warning" ? Theme.warningSoft : tone === "error" ? Theme.errorSoft : tone === "active" ? Theme.accentSoft : Theme.surfaceAlt
    Row {
        anchors.centerIn: parent
        spacing: 6
        Rectangle { width: 5; height: 5; radius: 2.5; color: badge.ink; anchors.verticalCenter: parent.verticalCenter }
        Text { id: label; text: badge.text; color: badge.ink; font.pixelSize: 11; font.weight: Font.Medium }
    }
}
