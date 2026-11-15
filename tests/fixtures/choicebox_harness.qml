import QtQuick
import QtQuick.Controls
import "../../app/leap_desktop/qml/components" as UI

ApplicationWindow {
    id: host
    width: 480
    height: 240
    visible: true
    title: "Project Leap ChoiceBox Contract Test"
    property int selectedIndex: 0
    property int activations: 0
    property int lastIndex: -1
    property var options: ["DAPI", "eGFP", "Ignore"]
    property bool selectable: true
    property string language: "zh"
    property string format: "marker"
    UI.ChoiceBox {
        id: choice
        objectName: "choice"
        anchors.centerIn: parent
        width: 250
        model: host.options
        currentIndex: host.selectedIndex
        enabled: host.selectable
        itemText: function(value, index) {
            if (host.format === "time") return (host.language === "zh" ? "时间点 " : "Time point ") + (index + 1)
            return value === "Ignore" && host.language === "zh" ? "忽略" : String(value)
        }
        onActivated: function(index) {
            host.activations += 1
            host.lastIndex = index
            host.selectedIndex = index
        }
    }
    function snapshot() {
        var items = []
        for (var i = 0; i < choice.popup.count; ++i) {
            var item = choice.popup.itemAt(i)
            items.push({text: item.text, checked: item.checked, enabled: item.enabled})
        }
        return JSON.stringify({items: items, index: choice.currentIndex, text: choice.currentText,
            display: choice.displayText, activations: activations, lastIndex: lastIndex,
            pressed: choice.pressed, popupVisible: choice.popup.visible,
            nativeRequested: choice.popup.popupType === Popup.Native})
    }
    function triggerItem(index) { choice.popup.itemAt(index).click() }
}
