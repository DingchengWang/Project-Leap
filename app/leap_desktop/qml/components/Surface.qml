import QtQuick
import QtQuick.Layouts

Rectangle {
    id: surface
    property real padding: 20
    property real spacing: 16
    property bool fillContent: false
    default property alias content: body.data
    implicitHeight: body.implicitHeight + padding * 2
    radius: 14
    color: Theme.surface
    border.color: Theme.border
    border.width: 1
    ColumnLayout {
        id: body
        x: surface.padding
        y: surface.padding
        width: surface.width - surface.padding * 2
        height: surface.fillContent ? Math.max(implicitHeight, surface.height - surface.padding * 2) : implicitHeight
        spacing: surface.spacing
    }
}
