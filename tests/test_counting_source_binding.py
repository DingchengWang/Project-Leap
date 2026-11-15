"""Source relocation preserves scientific records and verifies native identity."""
from contextlib import contextmanager
import copy
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import numpy as np
import tifffile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'engine'))
from leap_cell_counting.errors import CountingError
from leap_cell_counting.imaging import PlaneReader, relocate_source
from leap_cell_counting.source_scan import scan_source
from leap_cell_counting import worker


class SourceBindingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='counting-binding-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        library = ROOT / 'artifacts/prototype-d/native-counting/libLeapCountingStats.dylib'
        environment = patch.dict(os.environ, {'LEAP_COUNTING_STATS_LIBRARY': str(library)})
        environment.start()
        self.addCleanup(environment.stop)
        (self.root / 'sources').mkdir()
        self.source = self.root / 'sources/original.ome.tif'
        self.pixels = np.arange(2 * 3 * 16 * 16, dtype=np.uint16).reshape(2, 3, 16, 16)
        self.write_source(self.source)
        with PlaneReader(self.source, 0, 0) as reader:
            scan = scan_source(reader, self.root / 'statistics', 0, 1, [0, 1])
            info = reader.info
            self.analysis = {'schema_version': 2, 'run_id': 'test', 'source_path': str(self.source),
                'series_index': 0, 'time_index': 0, 'width': 16, 'height': 16, 'z_count': 3,
                'source_dtype': info['dtype'], 'physical_size_um': info['physical_size_um'],
                'dapi_channel': 0, 'support_channel': 1, 'display_channels': [0, 1],
                'channels': [{'index': i, 'name': name} for i, name in enumerate(info['channel_hints'])],
                'source_identity': scan['source_identity'],
                'points': [{'id': 'unchanged-cell', 'x': 4, 'y': 5}], 'labels_path': 'labels.npy'}
        stat = self.source.stat()
        self.analysis['source_signature'] = {'size_bytes': stat.st_size, 'mtime_ns': stat.st_mtime_ns}
        self.analysis_path = self.root / 'analysis.json'
        self.analysis_path.write_text(json.dumps(self.analysis))

    def write_source(self, path, *, pixels=None, spacing=.5):
        tifffile.imwrite(path, self.pixels if pixels is None else pixels, ome=True,
            metadata={'axes': 'CZYX', 'PhysicalSizeX': spacing, 'PhysicalSizeY': .5,
                      'PhysicalSizeZ': 1.5, 'Channel': {'Name': ['DAPI', 'GFAP']}})

    def test_scan_captures_every_used_native_plane_without_copying_source(self):
        identity = self.analysis['source_identity']
        self.assertEqual(identity['channels'], [0, 1])
        self.assertEqual({(p['channel'], p['z']) for p in identity['planes']},
                         {(c, z) for c in (0, 1) for z in range(3)})
        self.assertTrue(all(len(p['sha256']) == 64 for p in identity['planes']))

    def test_modern_relocation_accepts_new_mtime_and_only_changes_binding(self):
        candidate = self.source.with_name('moved.ome.tif')
        shutil.copyfile(self.source, candidate)
        os.utime(candidate, ns=(1_000_000_000, 1_000_000_000))
        self.source.unlink()
        result = relocate_source(self.analysis_path, candidate)
        updated = json.loads(self.analysis_path.read_text())
        expected = copy.deepcopy(self.analysis)
        expected['source_path'] = str(candidate)
        expected['source_signature'] = result['source_signature']
        self.assertEqual(updated, expected)
        self.assertEqual(result['identity_method'], 'native_plane_sha256_v1')

    def test_wrong_pixels_and_calibration_are_rejected_without_mutation(self):
        original = self.analysis_path.read_bytes()
        for kind in ('pixels', 'calibration'):
            candidate = self.source.with_name(f'wrong-{kind}.ome.tif')
            pixels = self.pixels.copy()
            if kind == 'pixels':
                pixels[1, 1, 4, 5] += 1
            self.write_source(candidate, pixels=pixels, spacing=.7 if kind == 'calibration' else .5)
            with self.assertRaises(CountingError) as error:
                relocate_source(self.analysis_path, candidate)
            self.assertEqual(error.exception.code, 'source_changed')
            self.assertEqual(self.analysis_path.read_bytes(), original)

    def test_legacy_requires_unchanged_size_mtime_and_metadata(self):
        del self.analysis['source_identity']
        self.analysis_path.write_text(json.dumps(self.analysis))
        candidate = self.source.with_name('legacy-moved.ome.tif')
        shutil.copy2(self.source, candidate)
        result = relocate_source(self.analysis_path, candidate)
        self.assertEqual(result['identity_method'], 'legacy_size_mtime_metadata')
        original = self.analysis_path.read_bytes()
        os.utime(candidate, ns=(1_000_000_000, 1_000_000_000))
        with self.assertRaises(CountingError) as error:
            relocate_source(self.analysis_path, candidate)
        self.assertEqual(error.exception.code, 'source_identity_unverified')
        self.assertEqual(self.analysis_path.read_bytes(), original)

    def test_legacy_metadata_mismatch_rejected_even_with_matching_stat(self):
        del self.analysis['source_identity']
        self.analysis_path.write_text(json.dumps(self.analysis))
        candidate = self.source.with_name('wrong-calibration.ome.tif')
        self.write_source(candidate, spacing=.7)
        signature = self.analysis['source_signature']
        self.assertEqual(candidate.stat().st_size, signature['size_bytes'])
        os.utime(candidate, ns=(signature['mtime_ns'], signature['mtime_ns']))
        with self.assertRaises(CountingError) as error:
            relocate_source(self.analysis_path, candidate)
        self.assertEqual(error.exception.code, 'source_changed')


class WorkerLeaseTests(unittest.TestCase):
    def test_component_lease_covers_terminal_writes_and_cleanup(self):
        with tempfile.TemporaryDirectory(prefix='counting-lease-') as folder:
            run = Path(folder) / 'run'
            run.mkdir()
            sources = Path(folder) / 'sources'
            sources.mkdir()
            source = sources / 'source.tif'
            source.write_bytes(b'source')
            request = {'schema_version': 2, 'run_id': 'lease', 'source_path': str(source),
                       'run_dir': str(run), 'events_path': str(run / 'events.jsonl'),
                       'outcome_path': str(run / 'outcome.json'), 'cancel_path': str(run / 'cancel')}
            path = run / 'request.json'
            path.write_text(json.dumps(request))
            held = []
            writes = []
            original_atomic = worker.atomic_json
            @contextmanager
            def lease(**kwargs):
                held.append(True)
                try:
                    yield
                finally:
                    held.pop()
            def write(target, payload):
                self.assertTrue(held)
                writes.append(Path(target).name)
                return original_atomic(target, payload)
            def event(*args, **kwargs):
                self.assertTrue(held)
            with patch.object(worker, 'component_usage_lock', lease), \
                    patch.object(worker, '_runtime_cache'), patch.object(worker, 'atomic_json', write), \
                    patch.object(worker.Events, 'emit', event), \
                    patch.object(worker, 'analyse', return_value={'status': 'review_ready', 'message': 'ready'}):
                self.assertEqual(worker.run_request(path), 0)
            self.assertEqual(writes, ['worker.json', 'outcome.json'])
            self.assertFalse(held)


class SourceBindingProcessTests(unittest.TestCase):
    setUp = SourceBindingTests.setUp
    write_source = SourceBindingTests.write_source

    def test_background_verification_rebinds_and_restores_original_z_pixels(self):
        try:
            from PySide6.QtCore import QUrl
            from PySide6.QtGui import QImage
            from PySide6.QtWidgets import QApplication
        except ImportError:
            self.skipTest('Qt is exercised by runtime/test-env; seed runs engine identity tests')
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        application = QApplication.instance() or QApplication([])
        sys.path.insert(0, str(ROOT / 'app'))
        from leap_desktop.counting import CountingEditor
        candidate = self.source.with_name('relocated.ome.tif')
        shutil.move(self.source, candidate)
        analysis = copy.deepcopy(self.analysis)
        analysis['schema_version'] = 1
        analysis['points'] = [{'id': 'manual-cell', 'source': 'manual', 'x': 4, 'y': 5, 'z': None}]
        for channel in analysis['channels']:
            channel.update(path=f"channel-{channel['index']}.png", color='#FFFFFF', display_range=[0, 65535])
            image = QImage(16, 16, QImage.Format.Format_Grayscale8)
            image.fill(20)
            self.assertTrue(image.save(str(self.root / channel['path'])))
        self.analysis_path.write_text(json.dumps(analysis))
        editor = CountingEditor()
        runtime = {'python': str(ROOT / 'runtime/seed/python/bin/python3.9'),
                   'site_packages': str(ROOT / 'runtime/seed/site-packages'), 'engine': str(ROOT / 'engine')}
        def wait_for(predicate):
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                application.processEvents()
                if editor._frame_pending:
                    editor.imageReady(editor.imageUrl)
                if predicate():
                    return
                time.sleep(.005)
            self.fail('Background source verification/slice worker timed out: ' + editor.message)
        try:
            editor.open_analysis(self.analysis_path, runtime=runtime)
            points = editor.points
            self.assertTrue(editor.sourceRelocationNeeded)
            # A confirmation queued first also owns the current source binding.
            with patch.object(editor, '_start_pending'):
                editor.confirm()
                editor.relocateSource(QUrl.fromLocalFile(str(candidate)).toString())
                self.assertTrue(editor.confirmationInProgress)
                self.assertFalse(editor.sourceRelocationInProgress)
            editor.close()
            editor.open_analysis(self.analysis_path, runtime=runtime)
            editor.relocateSource(QUrl.fromLocalFile(str(candidate)).toString())
            self.assertTrue(editor.sourceRelocationInProgress)
            self.assertTrue(editor.busy)
            verification = editor._relocation_process
            editor.confirm()
            self.assertFalse(editor.confirmationInProgress)
            self.assertIs(editor._relocation_process, verification)
            with self.assertRaisesRegex(RuntimeError, "正在验证原图"):
                editor.open_analysis(self.analysis_path, runtime=runtime)
            self.assertIs(editor._relocation_process, verification)
            self.assertEqual(editor.points, points)
            wait_for(lambda: not editor.sourceRelocationInProgress)
            self.assertFalse(editor.sourceRelocationNeeded, editor.sourceRelocationMessage)
            self.assertEqual(editor.points, points)
            self.assertEqual(json.loads(self.analysis_path.read_text())['source_path'], str(candidate))
            editor.setZ(0)
            wait_for(lambda: editor.displayedZIndex == 0 and not editor.busy)
            self.assertEqual((editor.appliedFrameWidth, editor.appliedFrameHeight), (16, 16))
            self.assertEqual(editor.points, points)
            rendered = QImage(QUrl(editor.appliedImageUrl).toLocalFile())
            expected = np.clip(sum((plane.astype(np.float64) * (255. / 65535)).astype(np.uint8)
                                   .astype(np.uint16) for plane in self.pixels[:, 0]), 0, 255)
            actual = np.array([[rendered.pixelColor(x, y).red() for x in range(16)] for y in range(16)])
            np.testing.assert_array_equal(actual, expected)
        finally:
            editor.close()
            wait_for(lambda: editor.drained)
        self.assertFalse((self.root / '.editor').exists())


if __name__ == '__main__':
    unittest.main()
