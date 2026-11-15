import QtQuick
import QtQuick.Controls
import QtQml.Models

ComboBox {
    id: control
    property string placeholder: qsTr("请选择")
    property var itemText: function(value, index) { return String(value) }
    implicitHeight: 36
    implicitWidth: 160
    focusPolicy: Qt.StrongFocus
    font.pixelSize: 13
    Accessible.name: displayText
    displayText: currentIndex < 0 ? placeholder : itemText(currentText, currentIndex)
    background: Rectangle {
        color: control.enabled ? Theme.surface : Theme.surfaceAlt
        radius: 7
        border.color: control.activeFocus ? Theme.accent : Theme.border
        border.width: control.activeFocus ? 2 : 1
    }
    contentItem: Text {
        text: control.displayText
        leftPadding: 11
        rightPadding: 28
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
        color: !control.enabled ? Theme.textDisabled : control.currentIndex < 0 ? Theme.textSecondary : Theme.text
        font: control.font
    }
    indicator: Text {
        x: control.width - width - 11
        y: (control.height - height) / 2 - 2
        text: "⌄"
        color: Theme.textMuted
        font.pixelSize: 16
    }
    delegate: MenuItem {
        required property var modelData
        required property int index
        objectName: control.objectName + "_option_" + index
        text: control.itemText(modelData, index)
        checkable: true
        autoExclusive: true
        checked: control.currentIndex === index
        enabled: control.enabled
        Accessible.name: text
    }
    popup: Menu {
        id: menu
        objectName: control.objectName + "_popup"
        y: control.height
        popupType: Popup.Native
        font: control.font
        palette: control.palette
        // Reuse ComboBox's delegates so Qt preserves index bindings and activation.
        Instantiator {
            model: control.delegateModel
            onObjectAdded: function(index, object) { menu.insertItem(index, object) }
            onObjectRemoved: function(index, object) { menu.removeItem(object) }
        }
    }
}
