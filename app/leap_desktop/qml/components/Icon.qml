import QtQuick

Canvas {
    id: icon
    property string kind: "grid"
    property color ink: Theme.textMuted
    readonly property color fillInk: Theme.surfaceAlt
    implicitWidth: 20
    implicitHeight: 20
    onKindChanged: requestPaint()
    onInkChanged: requestPaint()
    onFillInkChanged: requestPaint()
    onPaint: {
        var c = getContext("2d")
        c.clearRect(0, 0, width, height)
        c.save()
        c.scale(width / 24, height / 24)
        c.strokeStyle = ink
        c.fillStyle = ink
        c.lineWidth = 1.65
        c.lineCap = "round"
        c.lineJoin = "round"
        function line(x1,y1,x2,y2) { c.beginPath(); c.moveTo(x1,y1); c.lineTo(x2,y2); c.stroke() }
        function circle(x,y,r) { c.beginPath(); c.arc(x,y,r,0,Math.PI*2); c.stroke() }
        function rect(x,y,w,h) { c.strokeRect(x,y,w,h) }
        if (kind === "workbench") {
            rect(3.5,4,17,16); line(3.5,9,20.5,9); line(9,9,9,20)
        } else if (kind === "jobs") {
            rect(5,3.5,14,17); line(8.5,8,15.5,8); line(8.5,12,15.5,12); line(8.5,16,13,16)
        } else if (kind === "maintenance") {
            rect(3.5,3.5,6,6); rect(14.5,3.5,6,6); rect(3.5,14.5,6,6); circle(17.5,17.5,3)
        } else if (kind === "settings") {
            line(3,6,21,6); line(3,12,21,12); line(3,18,21,18)
            c.fillStyle = fillInk
            c.beginPath(); c.arc(8,6,2.3,0,Math.PI*2); c.fill(); c.stroke()
            c.beginPath(); c.arc(16,12,2.3,0,Math.PI*2); c.fill(); c.stroke()
            c.beginPath(); c.arc(10,18,2.3,0,Math.PI*2); c.fill(); c.stroke()
        } else if (kind === "folder") {
            c.beginPath(); c.moveTo(3,7); c.lineTo(3,19); c.lineTo(21,19); c.lineTo(21,6.5); c.lineTo(12,6.5); c.lineTo(10,4); c.lineTo(3,4); c.closePath(); c.stroke()
        } else if (kind === "image") {
            rect(3.5,3.5,17,17); circle(15.5,8.5,1.7); c.beginPath(); c.moveTo(4,17); c.lineTo(9,11); c.lineTo(13,16); c.lineTo(16,13); c.lineTo(20,18); c.stroke()
        } else if (kind === "import") {
            line(12,3,12,15); line(7.5,10.5,12,15); line(16.5,10.5,12,15); c.beginPath(); c.moveTo(4,15); c.lineTo(4,20); c.lineTo(20,20); c.lineTo(20,15); c.stroke()
        } else if (kind === "play") {
            c.beginPath(); c.moveTo(8,5); c.lineTo(19,12); c.lineTo(8,19); c.closePath(); c.fill()
        } else if (kind === "stop") {
            c.fillRect(6.5,6.5,11,11)
        } else if (kind === "check") {
            line(5,12,10,17); line(10,17,19,7)
        } else if (kind === "arrow") {
            line(5,12,19,12); line(14,7,19,12); line(14,17,19,12)
        } else if (kind === "external") {
            c.beginPath(); c.moveTo(13,4); c.lineTo(20,4); c.lineTo(20,11); c.stroke(); line(11,13,20,4); c.beginPath(); c.moveTo(9,5); c.lineTo(4,5); c.lineTo(4,20); c.lineTo(19,20); c.lineTo(19,15); c.stroke()
        } else if (kind === "refresh") {
            c.beginPath(); c.arc(12,12,8,0.5,5.6); c.stroke(); c.beginPath(); c.moveTo(14.5,4.5); c.lineTo(19,6); c.lineTo(20,1.5); c.stroke()
        } else if (kind === "info") {
            circle(12,12,9); line(12,11,12,16); circle(12,7.5,0.5)
        } else if (kind === "clock") {
            circle(12,12,9); line(12,7,12,12); line(12,12,16,14)
        } else if (kind === "layers") {
            c.beginPath(); c.moveTo(3,8); c.lineTo(12,3); c.lineTo(21,8); c.lineTo(12,13); c.closePath(); c.stroke(); c.beginPath(); c.moveTo(3,12); c.lineTo(12,17); c.lineTo(21,12); c.stroke(); c.beginPath(); c.moveTo(3,16); c.lineTo(12,21); c.lineTo(21,16); c.stroke()
        } else if (kind === "trash") {
            line(4,6,20,6); line(9,3,15,3); c.beginPath(); c.moveTo(6,6); c.lineTo(7,21); c.lineTo(17,21); c.lineTo(18,6); c.stroke(); line(10,10,10,17); line(14,10,14,17)
        } else if (kind === "warning") {
            c.beginPath(); c.moveTo(12,3); c.lineTo(22,21); c.lineTo(2,21); c.closePath(); c.stroke(); line(12,9,12,14); circle(12,17.5,0.5)
        } else if (kind === "logo") {
            c.lineWidth = 2.4; c.beginPath(); c.moveTo(5,18); c.lineTo(10,6); c.lineTo(14,16); c.lineTo(20,6); c.stroke(); circle(5,18,1.5); circle(20,6,1.5)
        }
        c.restore()
    }
}
