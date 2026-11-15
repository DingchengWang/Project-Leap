import QtQuick

// The AppKit selector submits candidates and displays controller-confirmed state.
Item {
    objectName: "fluorescenceAgeRail"
    implicitWidth: 320
    implicitHeight: 48
    WindowContainer {
        anchors.fill: parent
        window: nativeStyle.ageWindow
    }
}
