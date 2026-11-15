import QtQuick
import QtQuick.Layouts

ColumnLayout {
    id: cell
    property string label: ""
    property string value: "—"
    spacing: 5
    Text { text: cell.label; color: Theme.textSecondary; font.pixelSize: 11; Layout.fillWidth: true }
    Text { text: cell.value; color: Theme.text; font.pixelSize: 13; font.weight: Font.Medium; elide: Text.ElideRight; Layout.fillWidth: true }
}
