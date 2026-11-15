"""Isolated visible native-control harness. Uses no scientific data or controller."""
import json
import os
from pathlib import Path
import sys
import time
os.environ['QT_QPA_PLATFORM'] = 'cocoa'
ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'app'), str(ROOT / 'tests')]
from PySide6.QtCore import QObject, Slot, QTimer, Qt
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtWidgets import QApplication
from leap_desktop.native_style import NativeStyle
from leap_desktop.appearance import Appearance, ApplicationIcon
from native_appkit_probe import AppKitProbe
from test_native_style import Backend, native_bridge_override

app = QApplication([])
app.setApplicationName('Project Leap Native Verification')
app.styleHints().setColorScheme(Qt.ColorScheme.Light)
with native_bridge_override():
    appearance = Appearance(app, 'light')
application_icon = ApplicationIcon(app, appearance, ROOT)
probe = AppKitProbe()
backend = Backend()
backend.activeModuleId = 'project-leap-2d'
backend.installedModules = [{'id':'project-leap-2d','name':'Astrocyte Fluorescence Intensity Analysis','hasError':False},
                            {'id':'cell-counting','name':'3D DAPI Cell Counting','hasError':False}]
output = Path(os.environ.get('LEAP_NATIVE_HARNESS_OUTPUT', ROOT / 'artifacts/native-verification'))
output.mkdir(parents=True, exist_ok=True)
class Harness(QObject):
    def __init__(self):
        super().__init__(); self.reject = False; self.requests=[]
    def request(self, kind, value):
        self.requests.append({'kind':kind,'value':value,'accepted':not self.reject,'time':time.time()})
        if not self.reject:
            setattr(backend, {'age':'ageProfile','page':'page','module':'activeModuleId'}[kind],value)
        backend.changed.emit()
    @Slot(bool)
    def setRejected(self, value): self.reject=value
    @Slot(bool)
    def setLocked(self, value): backend.parametersLocked=value; backend.changed.emit()
    @Slot(bool)
    def setGFAP(self, value):
        backend.imageInfo={'path':'synthetic.tif'} if value else {}
        backend.channelRoles=['DAPI','GFAP'] if value else []; backend.changed.emit()
    @Slot()
    def scheduleResize(self): QTimer.singleShot(2000, lambda: window.setWidth(window.width()+80))
    @Slot()
    def scheduleFocusLoss(self):
        QTimer.singleShot(2000, window.showMinimized)
        QTimer.singleShot(3500, window.showNormal)
    @Slot()
    def stop(self): app.quit()
harness=Harness()
backend.setPage.side_effect=lambda value:harness.request('page',value)
backend.selectModule.side_effect=lambda value:harness.request('module',value)
backend.setAgeProfile.side_effect=lambda value:harness.request('age',value)
with native_bridge_override():
    style = NativeStyle(backend)
engine=QQmlApplicationEngine()
engine.rootContext().setContextProperty('nativeStyle',style)
engine.rootContext().setContextProperty('harness',harness)
engine.loadData(b'''import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
ApplicationWindow {
 visible: true; title: "Project Leap Native Verification"; width: 940; height: 680; x: 100; y: 100
 RowLayout { anchors.fill: parent; spacing: 22
  WindowContainer { Layout.preferredWidth: 236; Layout.fillHeight: true; window: nativeStyle.navigationWindow }
  ColumnLayout { Layout.fillWidth: true; Layout.fillHeight: true; Layout.rightMargin: 30; spacing: 16
   Text { text: "Native controls verification"; font.pixelSize: 24 }
   Text { text: "Independent test window. No scientific data or installed app is modified."; wrapMode: Text.WordWrap; Layout.fillWidth: true }
   WindowContainer { id: ageHost; Layout.fillWidth: true; Layout.preferredHeight: 48; window: nativeStyle.ageWindow }
   CheckBox { text: "Show age selector"; checked: true; onToggled: ageHost.visible=checked }
   CheckBox { text: "Reject controller requests"; onToggled: harness.setRejected(checked) }
   CheckBox { text: "Lock age parameters"; onToggled: harness.setLocked(checked) }
   CheckBox { text: "GFAP-only (disable Neonatal)"; onToggled: harness.setGFAP(checked) }
   Button { text: "Resize in 2 seconds"; onClicked: harness.scheduleResize() }
   Button { text: "Lose focus in 2 seconds"; onClicked: harness.scheduleFocusLoss() }
   Button { text: "Close verification window"; onClicked: harness.stop() }
   Item { Layout.fillHeight: true }
  }
 }
}''')
window=engine.rootObjects()[0]
samples = open(output/'interaction-samples.jsonl', 'w')
last_sample = None
def record():
    global last_sample
    state={'sidebar':probe.snapshot(style.navigationWindow), 'age':probe.snapshot(style.ageWindow)}
    compact={'time':time.time(),'requests':len(harness.requests),**state}
    signature=json.dumps({k:v for k,v in compact.items() if k != 'time'},sort_keys=True)
    if signature != last_sample:
        samples.write(json.dumps(compact)+'\n'); samples.flush(); last_sample=signature
    (output/'live-state.json').write_text(json.dumps({'time':time.time(),'requests':harness.requests,'native':state},indent=2))
timer=QTimer();timer.timeout.connect(record);timer.start(40)
QTimer.singleShot(15*60*1000, app.quit)
try: app.exec()
finally:
    record(); timer.stop(); samples.close()
    import shiboken6
    shiboken6.delete(engine); style.close()
    application_icon.close(); appearance.close(); probe.close()
