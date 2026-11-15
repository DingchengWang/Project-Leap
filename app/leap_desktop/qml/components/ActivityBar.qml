import QtQuick

Item {
    id: bar
    property bool active: false
    property real progress: -1
    property bool cancelled: false
    readonly property bool indeterminate: progress < 0
    readonly property bool animating: active && !cancelled && visible && indeterminate && !Theme.reducedMotion
    readonly property int segmentCount: Math.max(2, Math.floor(width / 12) + 1)
    readonly property real segmentWidth: Math.min(5, height, width / 3)
    readonly property real staggerDuration: 55 * Math.max(1, Math.floor(width / 22)) / (segmentCount - 1)
    readonly property int enterDuration: 480
    readonly property int holdDuration: 600
    readonly property int exitDuration: 420
    readonly property int resetDuration: 140
    implicitWidth: 240
    implicitHeight: 5
    visible: active && !cancelled && (!indeterminate || !Theme.reducedMotion)
    clip: true

    Rectangle {
        anchors.fill: parent
        radius: height / 2
        color: Theme.surfacePressed
    }
    Rectangle {
        objectName: "activityDeterminateFill"
        visible: !bar.indeterminate
        width: bar.width * Math.max(0, Math.min(1, bar.progress))
        height: bar.height
        radius: height / 2
        color: Theme.accent
    }
    Repeater {
        id: segments
        model: bar.segmentCount
        delegate: Rectangle {
            id: segment
            required property int index
            readonly property real restX: (bar.segmentCount - 1 - index) * (bar.width - width) / (bar.segmentCount - 1)
            objectName: "activitySegment_" + index
            width: bar.segmentWidth
            height: width
            y: (bar.height - height) / 2
            radius: height / 2
            color: Theme.accent
            visible: bar.indeterminate
            x: -width
            // The first segment parks at the right edge; later arrivals fill
            // the remaining positions. All segments then share one hold.
            SequentialAnimation {
                id: cycle
                running: bar.animating
                loops: Animation.Infinite
                PauseAnimation { duration: segment.index * bar.staggerDuration }
                NumberAnimation { target: segment; property: "x"; from: -segment.width; to: segment.restX; duration: bar.enterDuration; easing.type: Easing.OutCubic }
                PauseAnimation { duration: (bar.segmentCount - 1) * bar.staggerDuration + bar.holdDuration }
                NumberAnimation { target: segment; property: "x"; from: segment.restX; to: bar.width; duration: bar.exitDuration; easing.type: Easing.InCubic }
                PropertyAction { target: segment; property: "x"; value: -segment.width }
                PauseAnimation { duration: (bar.segmentCount - 1 - segment.index) * bar.staggerDuration + bar.resetDuration }
            }
            Connections {
                target: bar
                function onWidthChanged() { if (bar.animating) cycle.restart() }
                function onAnimatingChanged() { if (!bar.animating) segment.x = -segment.width }
            }
        }
    }
}
