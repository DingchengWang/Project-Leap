"""Technical import regression tests; no model loading or image-quality gates."""
from __future__ import annotations

import importlib.util
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import tifffile as tf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from leap_desktop.importer import ImportCancelled, ImportImageError, create_preview, inspect_image, split_image


def core_read_meta(path):
    """Run the unmodified kernel's metadata function without assembling its ML runtime."""
    filename = ROOT / "engine/project_leap_2d/images/image_loading.py"
    spec = importlib.util.spec_from_file_location("isolated_image_loading", filename)
    module = importlib.util.module_from_spec(spec)
    module.np, module.tf, module.Path = np, tf, Path
    spec.loader.exec_module(module)
    return module.read_meta(path)


class ImporterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.inputs = self.root / "source"
        self.inputs.mkdir()
        self.destination = self.root / "工作目录 with spaces"
        self.source = self.inputs / "输入 image.tif"
        self.data = np.arange(4 * 3 * 11 * 13, dtype=np.uint16).reshape(4, 3, 11, 13)

    def tearDown(self):
        self.temp.cleanup()

    def write_imagej(self, data=None, **kwargs):
        tf.imwrite(self.source, self.data if data is None else data, imagej=True,
                   metadata={"axes": "ZCYX", "unit": "um", "spacing": 0.2508},
                   resolution=((10871545, 1000000), (100, 9)), resolutionunit="NONE", **kwargs)

    def test_inspection_headers_and_imagej_streamed_roundtrip(self):
        self.write_imagej()
        with patch.object(tf.TiffPage, "asarray", side_effect=AssertionError("inspection decoded pixels")):
            info = inspect_image(self.source)
        self.assertEqual(info["axes"], "ZCYX")
        self.assertEqual(info["channel_count"], 3)
        self.assertEqual(info["physical_size_um"]["Z"], 0.2508)
        before = self.source.stat()
        events = []
        result = split_image(self.source, self.destination, ["DAPI", "eGFP", "KCNN2"], progress=events.append)
        self.assertEqual(len(events), 12)
        self.assertEqual(events[-1]["current"], 12)
        for channel, role in enumerate(["DAPI", "eGFP", "KCNN2"]):
            output = Path(result["files"][role])
            np.testing.assert_array_equal(tf.imread(output), self.data[:, channel])
            meta = core_read_meta(output)
            self.assertEqual(meta["axes"], "ZYX")
            self.assertEqual(meta["dtype"], "uint16")
            self.assertAlmostEqual(meta["pixel_width_um"], 1000000 / 10871545, places=14)
            self.assertAlmostEqual(meta["pixel_height_um"], .09, places=14)
            self.assertEqual(meta["pixel_depth_um"], .2508)
            self.assertNotIn("mature", output.name.lower())
        self.assertEqual(self.source.stat().st_mtime_ns, before.st_mtime_ns)
        self.assertEqual(self.source.stat().st_size, before.st_size)

    def test_palette_indices_are_preserved_as_scalar_grayscale(self):
        data = (self.data % 256).astype("uint8")
        palette = np.vstack([np.arange(256, dtype="uint16") * 257] * 3)
        self.write_imagej(data, photometric="palette", colormap=palette)
        self.assertEqual(inspect_image(self.source)["series"][0]["photometric"], "PALETTE")
        result = split_image(self.source, self.destination, ["DAPI", "Ignore", "KCNN2"])
        self.assertEqual(set(result["files"]), {"DAPI", "KCNN2"})
        np.testing.assert_array_equal(tf.imread(result["files"]["KCNN2"]), data[:, 2])

    def test_ome_explicit_time_and_permuted_channel_z_axes(self):
        data = self.data.reshape(2, 3, 2, 11, 13)
        tf.imwrite(self.source, data, ome=True, photometric="minisblack", metadata={
            "axes": "TCZYX", "PhysicalSizeX": 91, "PhysicalSizeXUnit": "nm",
            "PhysicalSizeY": .12, "PhysicalSizeYUnit": "um", "PhysicalSizeZ": .4,
            "PhysicalSizeZUnit": "um", "Channel": {"Name": ["nuclei", "structural", "target"]}})
        info = inspect_image(self.source)
        self.assertEqual(info["time_count"], 2)
        self.assertEqual(info["channel_hints"], ["nuclei", "structural", "target"])
        with self.assertRaisesRegex(ImportImageError, "time"):
            split_image(self.source, self.destination, ["DAPI", "eGFP", "KCNN2"])
        result = split_image(self.source, self.destination, ["DAPI", "eGFP", "KCNN2"], time_index=1)
        np.testing.assert_array_equal(tf.imread(result["files"]["eGFP"]), data[1, 1])
        self.assertAlmostEqual(core_read_meta(Path(result["files"]["DAPI"]))["pixel_width_um"], .091)

    def test_multiple_series_requires_explicit_selection(self):
        with tf.TiffWriter(self.source, ome=True) as writer:
            for scale in [.1, .2]:
                writer.write(self.data, photometric="minisblack", metadata={"axes": "ZCYX",
                             "PhysicalSizeX": scale, "PhysicalSizeY": scale, "PhysicalSizeZ": .3})
        info = inspect_image(self.source)
        self.assertEqual(info["series_count"], 2)
        self.assertIsNone(info["axes"])
        with self.assertRaises(ImportImageError) as error:
            split_image(self.source, self.destination, ["DAPI", "eGFP", "KCNN2"])
        self.assertEqual(error.exception.code, "series_selection_required")
        result = split_image(self.source, self.destination, ["DAPI", "eGFP", "KCNN2"], series_index=1)
        self.assertAlmostEqual(result["metadata"]["physical_size_um"]["X"], .2)

    def test_scifio_scales(self):
        description = "SCIFIO=0.49.0\naxes=X,Y,Z\nlengths=13,11,4\nscales=100,120,250\nunits=nm,nm,nm\nimages=4\nchannels=1\nslices=4\nframes=1\nunit=nm\n"
        tf.imwrite(self.source, self.data[:, 0], photometric="minisblack", description=description, metadata=None)
        info = inspect_image(self.source)
        self.assertEqual(info["physical_size_um"], {"X": .1, "Y": .12, "Z": .25})
        result = split_image(self.source, self.destination, ["DAPI"])
        self.assertAlmostEqual(core_read_meta(Path(result["files"]["DAPI"]))["pixel_depth_um"], .25)

    def test_float32_pixel_bits_and_explicit_profile(self):
        data = self.data.astype("float32") / 7
        data[0, 0, 0, 0] = np.nan
        data[0, 0, 0, 1] = np.inf
        data[0, 0, 0, 2] = -0.0
        self.write_imagej(data)
        result = split_image(self.source, self.destination, ["DAPI", "eGFP", "KCNN2"], profile="mature")
        output = Path(result["files"]["DAPI"])
        self.assertIn("mature", output.name)
        np.testing.assert_array_equal(tf.imread(output).view("uint32"), data[:, 0].copy().view("uint32"))

    def test_rgb_samples_and_out_of_range_selections_are_rejected(self):
        tf.imwrite(self.source, np.zeros((4, 11, 13, 3), dtype="uint8"), photometric="rgb", metadata={"axes": "ZYXS"})
        with self.assertRaises(ImportImageError) as error:
            split_image(self.source, self.destination, ["DAPI"])
        self.assertEqual(error.exception.code, "unsupported_axes")
        self.write_imagej()
        for kwargs in ({"series_index": -1}, {"series_index": 1}, {"time_index": 1}, {"time_index": True}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ImportImageError):
                split_image(self.source, self.destination, ["DAPI", "eGFP", "KCNN2"], **kwargs)

    def test_publication_failure_rolls_back_own_outputs(self):
        self.write_imagej()
        self.destination.mkdir()
        marker = self.destination / "keep.txt"
        marker.write_text("keep")
        real_link = os.link
        calls = []
        def fail_second_link(source, target):
            calls.append(str(target))
            if len(calls) == 2:
                raise OSError("simulated publication failure")
            return real_link(source, target)
        with patch("leap_desktop.importer.os.link", side_effect=fail_second_link):
            with self.assertRaises(OSError):
                split_image(self.source, self.destination, ["DAPI", "eGFP", "KCNN2"])
        self.assertEqual(list(self.destination.iterdir()), [marker])

    def test_cancellation_during_publication_rolls_back_published_channel(self):
        self.write_imagej()
        before = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.destination.mkdir()
        marker = self.destination / "keep.txt"
        marker.write_text("keep")
        real_link = os.link
        published = []
        def link_then_cancel(source, target):
            real_link(source, target)
            published.append(str(target))
        with patch("leap_desktop.importer.os.link", side_effect=link_then_cancel):
            with self.assertRaises(ImportCancelled):
                split_image(self.source, self.destination, ["DAPI", "eGFP", "KCNN2"],
                            cancelled=lambda: bool(published))
        self.assertEqual(len(published), 1)
        self.assertEqual(list(self.destination.iterdir()), [marker])
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), before)

    def test_actual_malformed_and_unsupported_tiffs_have_clear_cli_errors(self):
        pythonpath = os.pathsep.join([str(ROOT / "app"), os.environ.get("PYTHONPATH", "")])
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=pythonpath)
        env.pop("PROJECT_LEAP_COMPONENT_LOCK", None)
        self.source.write_bytes(b"II*\x00\x08\x00\x00\x00\x0e\x00")
        before = self.source.read_bytes()
        command = [sys.executable, "-B", "-m", "leap_desktop.import_worker", "--inspect", str(self.source)]
        malformed = subprocess.run(command, env=env, capture_output=True, text=True)
        self.assertEqual(malformed.returncode, 1)
        result = json.loads(malformed.stdout)
        self.assertEqual(result["status"], "failed")
        self.assertTrue(result["message"].strip())
        self.assertEqual(self.source.read_bytes(), before)
        tf.imwrite(self.source, self.data.astype("uint32"), photometric="minisblack", metadata={"axes": "ZCYX"})
        unsupported = subprocess.run(command, env=env, capture_output=True, text=True)
        self.assertEqual(unsupported.returncode, 0)
        result = json.loads(unsupported.stdout)
        self.assertFalse(result["supported_layout"])
        self.assertEqual(result["layout_error"]["code"], "unsupported_dtype")
        self.assertIn("uint32", result["layout_error"]["message"])
        request = {"source_path": str(self.source), "destination_dir": str(self.destination),
                   "channel_roles": ["DAPI", "eGFP", "KCNN2"], "result_path": str(self.root / "result.json")}
        request_path = self.root / "request.json"
        request_path.write_text(json.dumps(request))
        before = hashlib.sha256(self.source.read_bytes()).hexdigest()
        unsupported = subprocess.run([sys.executable, "-B", "-m", "leap_desktop.import_worker", "--request", str(request_path)],
                                     env=env, capture_output=True, text=True)
        self.assertEqual(unsupported.returncode, 1)
        self.assertEqual(json.loads(unsupported.stdout)["error_code"], "unsupported_dtype")
        self.assertFalse(self.destination.exists())
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), before)

    def test_corrupt_late_plane_leaves_no_partial_output(self):
        self.write_imagej()
        # Keep all IFDs and axis metadata intact, but point a late C2 plane
        # beyond EOF. C1 has already been staged when this real read fails.
        with tf.TiffFile(self.source) as tif:
            value_offset = tif.series[0].pages[10].tags["StripOffsets"].valueoffset
            byteorder = tif.byteorder
        with self.source.open("r+b") as handle:
            handle.seek(value_offset)
            handle.write(struct.pack(byteorder + "I", self.source.stat().st_size + 4096))
        before = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.destination.mkdir()
        marker = self.destination / "keep.txt"
        marker.write_text("keep")
        events = []
        with self.assertRaises((OSError, ValueError)) as error:
            split_image(self.source, self.destination, ["DAPI", "eGFP", "KCNN2"], progress=events.append)
        self.assertGreaterEqual(len(events), 4)
        self.assertTrue(str(error.exception).strip())
        self.assertEqual(list(self.destination.iterdir()), [marker])
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), before)

    def test_worker_lock_blocks_maintenance_after_parent_releases_its_lock(self):
        lock_path = self.root / "component.lock"
        lock_path.touch()
        pythonpath = os.pathsep.join([str(ROOT / "app"), os.environ.get("PYTHONPATH", "")])
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=pythonpath,
                   PROJECT_LEAP_COMPONENT_LOCK=str(lock_path))
        script = """import sys
from leap_desktop import import_worker, importer
def inspect(path):
    print('worker owns lock', flush=True)
    sys.stdin.readline()
    return {'path': path}
importer.inspect_image = inspect
raise SystemExit(import_worker.main(['--inspect', sys.argv[1]]))
"""
        with lock_path.open("rb") as parent_lock, lock_path.open("rb") as maintenance_lock:
            fcntl.flock(parent_lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
            child = subprocess.Popen([sys.executable, "-B", "-c", script, str(self.source)], env=env,
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                self.assertEqual(child.stdout.readline().strip(), "worker owns lock")
                fcntl.flock(parent_lock, fcntl.LOCK_UN)
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(maintenance_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                output, errors = child.communicate("finish\n", timeout=10)
                self.assertEqual(child.returncode, 0, errors)
                self.assertEqual(json.loads(output)["path"], str(self.source))
                fcntl.flock(maintenance_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                if child.poll() is None:
                    child.kill()
                    child.communicate(timeout=10)

    def test_worker_rejects_busy_component_before_importing_scientific_packages(self):
        lock_path = self.root / "component.lock"
        lock_path.touch()
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=str(ROOT / "app"),
                   PROJECT_LEAP_COMPONENT_LOCK=str(lock_path))
        with lock_path.open("rb") as maintenance_lock:
            fcntl.flock(maintenance_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            run = subprocess.run([sys.executable, "-B", "-m", "leap_desktop.import_worker", "--inspect", str(self.source)],
                                 env=env, capture_output=True, text=True, timeout=10)
        self.assertEqual(run.returncode, 1, run.stderr)
        result = json.loads(run.stdout)
        self.assertEqual(result["error_code"], "component_busy")
        self.assertIn("维护", result["message"])

    def test_request_worker_registers_before_loading_numpy(self):
        self.write_imagej()
        request = {"source_path": str(self.source), "destination_dir": str(self.destination),
                   "channel_roles": ["DAPI", "eGFP", "KCNN2"], "result_path": str(self.root / "result.json")}
        request_path = self.root / "request.json"
        request_path.write_text(json.dumps(request))
        pythonpath = os.pathsep.join([str(ROOT / "app"), os.environ.get("PYTHONPATH", "")])
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=pythonpath)
        env.pop("PROJECT_LEAP_COMPONENT_LOCK", None)
        script = """import sys
from leap_desktop import import_worker
class PauseNumpy:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'numpy':
            sys.meta_path.remove(self)
            print('before numpy import', flush=True)
            sys.stdin.readline()
        return None
sys.meta_path.insert(0, PauseNumpy())
raise SystemExit(import_worker.main(['--request', sys.argv[1]]))
"""
        child = subprocess.Popen([sys.executable, "-B", "-c", script, str(request_path)], env=env,
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(child.stdout.readline().strip(), "before numpy import")
            registry = json.loads((self.root / "worker.json").read_text())
            self.assertEqual(registry, {"pid": child.pid, "kind": "import", "request_path": str(request_path.resolve())})
            output, errors = child.communicate("continue\n", timeout=10)
            self.assertEqual(child.returncode, 0, errors)
            self.assertEqual(json.loads(output.splitlines()[-1])["status"], "completed")
        finally:
            if child.poll() is None:
                child.kill()
                child.communicate(timeout=10)

    def test_missing_calibration_and_unknown_axes_fail_explicitly(self):
        tf.imwrite(self.source, self.data, photometric="minisblack", metadata={"axes": "ZCYX"})
        with self.assertRaises(ImportImageError) as error:
            split_image(self.source, self.destination, ["DAPI", "eGFP", "KCNN2"])
        self.assertEqual(error.exception.code, "missing_calibration")
        tf.imwrite(self.source, self.data, photometric="minisblack", metadata={"axes": "QQYX"})
        self.assertFalse(inspect_image(self.source)["supported_layout"])
        with self.assertRaises(ImportImageError) as error:
            split_image(self.source, self.destination, ["DAPI", "eGFP", "KCNN2"])
        self.assertEqual(error.exception.code, "unsupported_axes")

    def test_invalid_mapping_source_write_and_existing_output_are_rejected(self):
        self.write_imagej()
        for roles in (["DAPI"], ["DAPI", "DAPI", "KCNN2"], ["Ignore"] * 3, ["DAPI", "eGFP", "other"]):
            with self.subTest(roles=roles), self.assertRaises(ImportImageError):
                split_image(self.source, self.destination, roles)
        with self.assertRaises(ImportImageError):
            split_image(self.source, self.inputs, ["DAPI", "eGFP", "KCNN2"])
        self.destination.mkdir()
        existing = self.destination / "C1-DAPI.tif"
        existing.write_bytes(b"existing")
        with self.assertRaises(ImportImageError):
            split_image(self.source, self.destination, ["DAPI", "eGFP", "KCNN2"])
        self.assertEqual(existing.read_bytes(), b"existing")

    def test_cancellation_and_read_failure_clean_only_owned_files(self):
        self.write_imagej()
        self.destination.mkdir()
        marker = self.destination / "keep.txt"
        marker.write_text("keep")
        events = []
        with self.assertRaises(ImportCancelled):
            split_image(self.source, self.destination, ["DAPI", "eGFP", "KCNN2"],
                        progress=events.append, cancelled=lambda: len(events) >= 2)
        self.assertEqual(list(self.destination.iterdir()), [marker])
        with patch.object(tf.TiffPage, "asarray", side_effect=OSError("read failed")):
            with self.assertRaises(OSError):
                split_image(self.source, self.destination, ["DAPI", "eGFP", "KCNN2"])
        self.assertEqual(list(self.destination.iterdir()), [marker])

    def test_cli_result_progress_and_cancel_protocol(self):
        self.write_imagej()
        pythonpath = os.pathsep.join([str(ROOT / "app"), os.environ.get("PYTHONPATH", "")])
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=pythonpath)
        inspected = subprocess.run([sys.executable, "-B", "-m", "leap_desktop.import_worker", "--inspect", str(self.source)], env=env, capture_output=True, text=True)
        self.assertEqual(inspected.returncode, 0, inspected.stderr)
        self.assertEqual(json.loads(inspected.stdout)["channel_count"], 3)
        request_path, result_path = self.root / "request.json", self.root / "result.json"
        request = {"source_path": str(self.source), "destination_dir": str(self.destination),
                   "channel_roles": ["DAPI", "eGFP", "KCNN2"], "result_path": str(result_path)}
        request_path.write_text(json.dumps(request))
        run = subprocess.run([sys.executable, "-B", "-m", "leap_desktop.import_worker", "--request", str(request_path)], env=env, capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        lines = [json.loads(line) for line in run.stdout.splitlines()]
        self.assertEqual(lines[0]["type"], "progress")
        self.assertEqual(lines[-1]["status"], "completed")
        self.assertEqual(json.loads(result_path.read_text()), lines[-1])
        (self.root / "cancel.request").touch()
        request["destination_dir"] = str(self.root / "cancelled-output")
        request_path.write_text(json.dumps(request))
        run = subprocess.run([sys.executable, "-B", "-m", "leap_desktop.import_worker", "--request", str(request_path)], env=env, capture_output=True, text=True)
        self.assertEqual(run.returncode, 130)
        self.assertEqual(json.loads(result_path.read_text())["status"], "cancelled")
        self.assertFalse(Path(request["destination_dir"]).exists())

    def test_preview_decodes_one_plane_and_caps_dimensions(self):
        from PIL import Image
        data = np.arange(3 * 2 * 512 * 1024, dtype="uint8").reshape(3, 2, 512, 1024)
        self.write_imagej(data)
        output = self.root / "cache" / "thumbnail.png"
        original = tf.TiffPage.asarray
        decoded = []
        def read_plane(page, *args, **kwargs):
            decoded.append(page.index)
            return original(page, *args, **kwargs)
        with patch.object(tf.TiffPage, "asarray", read_plane):
            preview = create_preview(self.source, output)
        self.assertEqual(decoded, [2])
        self.assertEqual(preview["preview_channel_index"], 0)
        self.assertEqual(preview["preview_z_index"], 1)
        with Image.open(output) as image:
            self.assertEqual(image.mode, "L")
            self.assertEqual(image.size, (768, 384))
            expected = Image.fromarray(data[1, 0])
            expected.thumbnail((768, 768), Image.Resampling.LANCZOS)
            np.testing.assert_array_equal(np.asarray(image), np.asarray(expected))

    def test_preview_does_not_select_multiple_series_or_write_source(self):
        with tf.TiffWriter(self.source, ome=True) as writer:
            for _ in range(2):
                writer.write(self.data, photometric="minisblack", metadata={"axes": "ZCYX"})
        output = self.root / "cache" / "thumbnail.png"
        with self.assertRaises(ImportImageError) as error:
            create_preview(self.source, output)
        self.assertEqual(error.exception.code, "series_selection_required")
        self.assertFalse(output.exists())
        self.write_imagej()
        with self.assertRaises(ImportImageError) as error:
            create_preview(self.source, self.inputs / "thumbnail.png")
        self.assertEqual(error.exception.code, "source_directory_read_only")

    def test_cli_preview_failure_preserves_metadata_but_not_inspection_errors(self):
        self.write_imagej()
        pythonpath = os.pathsep.join([str(ROOT / "app"), os.environ.get("PYTHONPATH", "")])
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=pythonpath)
        command = [sys.executable, "-B", "-m", "leap_desktop.import_worker", "--inspect", str(self.source),
                   "--preview-path", str(self.inputs / "preview.png")]
        run = subprocess.run(command, env=env, capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        result = json.loads(run.stdout)
        self.assertEqual(result["axes"], "ZCYX")
        self.assertEqual(result["channel_count"], 3)
        self.assertEqual(result["preview_error"]["code"], "source_directory_read_only")
        self.assertNotIn("preview_path", result)
        command[-1] = str(self.root / "cache" / "preview.png")
        run = subprocess.run(command, env=env, capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        result = json.loads(run.stdout)
        self.assertTrue(Path(result["preview_path"]).is_file())
        self.assertEqual(result["preview_channel_index"], 0)
        self.assertEqual(result["preview_z_index"], 2)
        self.assertNotIn("preview_error", result)
        self.source.write_bytes(b"not a TIFF")
        run = subprocess.run(command, env=env, capture_output=True, text=True)
        self.assertEqual(run.returncode, 1)
        self.assertEqual(json.loads(run.stdout)["status"], "failed")


if __name__ == "__main__":
    unittest.main()
