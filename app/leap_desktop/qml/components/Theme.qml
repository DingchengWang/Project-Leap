pragma Singleton
import QtQuick

QtObject {
    readonly property bool dark: appearance.dark
    readonly property bool reducedMotion: appearance.reducedMotion
    readonly property color accent: appearance.accent
    readonly property color accentHover: dark ? Qt.lighter(accent, 1.12) : Qt.darker(accent, 1.08)
    readonly property color accentPressed: Qt.darker(accent, 1.22)
    readonly property color accentSoft: Qt.rgba(accent.r, accent.g, accent.b, dark ? 0.20 : 0.09)
    readonly property color background: dark ? "#191C22" : "#F7F9FC"
    readonly property color surface: dark ? "#252A33" : "#FFFFFF"
    readonly property color surfaceAlt: dark ? "#2B303A" : "#F3F5F9"
    readonly property color surfaceHover: dark ? "#343B47" : "#EDF0F5"
    readonly property color surfacePressed: dark ? "#3A4352" : "#E5E9F0"
    readonly property color text: dark ? "#E6EAF1" : "#313B4D"
    readonly property color textSecondary: dark ? "#B4BECD" : "#5F6D82"
    readonly property color textMuted: dark ? "#99A5B7" : "#788396"
    readonly property color textDisabled: dark ? "#798392" : "#A4A8B0"
    readonly property color border: dark ? "#3D4553" : "#E0E5ED"
    readonly property color borderStrong: dark ? "#626F83" : "#A7B4C9"
    readonly property color success: dark ? "#80D8AA" : "#2C735A"
    readonly property color successSoft: dark ? "#263E35" : "#ECF6F0"
    readonly property color warning: dark ? "#E6C078" : "#806428"
    readonly property color warningSoft: dark ? "#41372A" : "#FCF3E5"
    readonly property color error: dark ? "#F0949D" : "#B33E43"
    readonly property color errorSoft: dark ? "#442E36" : "#FBEFF0"
    readonly property color white: "#FFFFFF"
}
