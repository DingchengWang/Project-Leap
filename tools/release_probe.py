#!/usr/bin/env python3
"""Exercise real release packages over signed loopback HTTP in a disposable home.

The publisher key and local HTTP allowance exist only in this external tool.
No installed application, default app data, or release archive is modified.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import threading
from unittest.mock import patch
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ORIGIN = "https://release-probe.invalid"
sys.path[:0] = [str(ROOT / "app"), str(ROOT / "tools")]
from leap_desktop import maintenance as maintenance
from leap_desktop.release_catalog import ReleaseConfiguration
from leap_desktop.update_manager import check_component_updates, stage_component_updates, apply_staged_updates
from sign_component_catalog import signed_envelope


RUNTIME_PROBE = r'''
import importlib, json, sys
assert sys.version_info[:2] == (3, 9), sys.version
import numpy as np
from scipy import ndimage
assert ndimage.label(np.eye(3))[1] == 3
import torch, torchvision, skimage, tifffile
from cellpose import models
model = models.CellposeModel(gpu=False, pretrained_model=sys.argv[1])
assert model.net is not None
import project_leap_2d.desktop_adapter
import leap_cell_counting.cell_pipeline
import leap_cell_counting.statistics
from project_leap_2d.nuclei.instanseg_nucleus_detection import get_instanseg_model
assert get_instanseg_model() is not None
print(json.dumps({'python':sys.version, 'executable':sys.executable,
 'module_files':{name:importlib.import_module(name).__file__ for name in
 ['numpy','torch','project_leap_2d.desktop_adapter','leap_cell_counting.cell_pipeline']},
 'versions':{'numpy':np.__version__,'torch':torch.__version__,'torchvision':torchvision.__version__,
 'skimage':skimage.__version__,'tifffile':tifffile.__version__},
 'cellpose_model_loaded':True,'instanseg_model_loaded':True,'scipy_computation':True}))
'''


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def require(result, status):
    if result.get("status") != status:
        raise RuntimeError(json.dumps(result, ensure_ascii=False))
    return result


def isolated_child(state_path):
    """Reload persisted state after process restart, then execute installed Python."""
    state = json.loads(Path(state_path).read_text())
    home = Path(state["home"])
    configuration = ReleaseConfiguration(**state["configuration"])
    if state.get("plan"):
        applied = require(apply_staged_updates(home, state["plan"], configuration), "ready")
    else:
        applied = None
    descriptors = [require(maintenance.component_status(home, module_id=identifier), "ready")["runtime_descriptor"]
                   for identifier in (maintenance.COMPONENT_ID, maintenance.COUNTING_MODULE_ID)]
    if descriptors[0]["python"] != descriptors[1]["python"]:
        raise AssertionError("Installed modules did not share the same Python runtime")
    descriptor = descriptors[0]
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(("PYTHON", "DYLD_", "PROJECT_LEAP_", "QT_", "QML"))}
    environment.update(PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1",
                       PYTHONPATH=os.pathsep.join([item["engine"] for item in descriptors] + [descriptor["site_packages"]]),
                       CELLPOSE_LOCAL_MODELS_PATH=descriptor["models"],
                       MPLCONFIGDIR=str(home / "cache/matplotlib"), XDG_CACHE_HOME=str(home / "cache"),
                       OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", VECLIB_MAXIMUM_THREADS="1")
    result = subprocess.run([descriptor["python"], "-B", "-c", RUNTIME_PROBE,
                             str(Path(descriptor["models"]) / maintenance.MODEL_FILENAME)],
                            env=environment, cwd=home, capture_output=True, text=True, timeout=300)
    log = Path(state["output"]).with_suffix(".runtime.log")
    log.write_text(result.stdout + "\n" + result.stderr)
    if result.returncode:
        raise RuntimeError("Installed scientific runtime failed; see " + str(log))
    runtime = json.loads(result.stdout.splitlines()[-1])
    for module_file in runtime["module_files"].values():
        if not Path(module_file).resolve().is_relative_to(home.resolve()):
            raise AssertionError("Runtime imported a module outside the installed component home: " + module_file)
    write_json(state["output"], {"pid": os.getpid(), "apply": applied, "runtime": runtime,
                                "versions": {identifier: maintenance.component_status(home, module_id=identifier)["component_version"]
                                             for identifier in (maintenance.COMPONENT_ID, maintenance.COUNTING_MODULE_ID)}})


@contextmanager
def serve(directory):
    requests = Counter()
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(directory), **kwargs)
        def do_GET(self):
            requests[self.path] += 1
            super().do_GET()
        def log_message(self, *_args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    def transport(address):
        if not address.startswith(FIXTURE_ORIGIN + "/"):
            raise AssertionError("External verification attempted an unexpected network origin: " + address)
        return urlopen(address.replace(FIXTURE_ORIGIN, "http://127.0.0.1:" + str(server.server_port), 1), timeout=30)
    try:
        with patch("leap_desktop.release_catalog.open_url", side_effect=transport):
            yield FIXTURE_ORIGIN, requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packages", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--native-library", type=Path, default=ROOT / "runtime/build/libLeapGlass.dylib")
    parser.add_argument("--restart-state", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.restart_state:
        isolated_child(args.restart_state)
        return
    if args.packages is None or args.output is None:
        parser.error("--packages and a new --output directory are required")
    packages = args.packages.resolve()
    output = args.output.absolute()
    if output.exists():
        parser.error("--output must not exist; each verification uses a new isolated directory")
    if output == packages or packages in output.parents or output in packages.parents:
        parser.error("--output must be separate from the package source")
    catalog = json.loads((packages / "catalog.json").read_text())
    entries = [dict(value, id=identity, path="packages/" + value["path"])
               for identity, value in catalog["packages"].items()]
    expected = {maintenance.COMPONENT_ID, maintenance.COUNTING_MODULE_ID, maintenance.SCIENCE_ID,
                maintenance.MODEL_ID, maintenance.FIJI_ID}
    if {item["id"] for item in entries} != expected:
        raise AssertionError("The release must contain the two engine modules and three shared components")
    for item in entries:
        archive = packages / catalog["packages"][item["id"]]["path"]
        if archive.stat().st_size != item["bytes"] or maintenance._digest(archive) != item["sha256"]:
            raise AssertionError("Package differs from its build catalog: " + item["id"])
    output.mkdir(parents=True)
    with tempfile.TemporaryDirectory(prefix="leap-release-probe-", dir="/private/tmp") as temporary:
        root = Path(temporary)
        home, server_root = root / "AppData", root / "server"
        server_root.mkdir()
        (server_root / "packages").symlink_to(packages, target_is_directory=True)
        signer, key = root / "signer", root / "private-key"
        subprocess.run(["xcrun", "swiftc", "-module-cache-path", str(root / "swift-cache"),
                        str(ROOT / "tools/component_catalog_signer.swift"), "-o", str(signer)], check=True)
        public = subprocess.check_output([str(signer), "generate", str(key)], text=True).strip()
        latest = {item["id"]: item["version"] for item in entries}
        def publish():
            write_json(server_root / "catalog.json", signed_envelope(
                {"schema_version": 2, "packages": entries, "latest": latest}, key, signer))
        publish()
        with serve(server_root) as (address, requests):
            configuration = ReleaseConfiguration(address + "/catalog.json", public, args.native_library.resolve())
            config_record = dict(catalog_url=configuration.catalog_url, public_key=public,
                                 native_library=str(configuration.native_library))
            install_records = {}
            for identifier in (maintenance.COMPONENT_ID, maintenance.COUNTING_MODULE_ID):
                print("Installing signed release component: " + identifier, flush=True)
                install_records[identifier] = require(maintenance.install_component(home, module_id=identifier,
                                                                                   configuration=configuration), "ready")
            pointers = {identifier: (home / "components" / identifier / "current.json").read_bytes()
                        for identifier in (maintenance.SCIENCE_ID, maintenance.MODEL_ID, maintenance.FIJI_ID, maintenance.COMPONENT_ID)}
            def restart(name, plan=None):
                state_path = root / (name + "-state.json")
                write_json(state_path, {"home": str(home), "configuration": config_record,
                                       "plan": plan, "output": str(output / (name + ".json"))})
                print("Restarting external verification process: " + name, flush=True)
                result = subprocess.run([sys.executable, "-B", str(Path(__file__).resolve()), "--restart-state", str(state_path)],
                                        text=True, capture_output=True, timeout=400)
                (output / (name + ".log")).write_text(result.stdout + "\n" + result.stderr)
                if result.returncode:
                    raise RuntimeError("Restart verification failed; see " + str(output / (name + ".log")))
            restart("installed-runtime")
            identifier = maintenance.COUNTING_MODULE_ID
            original = catalog["packages"][identifier]
            update_seed = root / "updated-module"
            maintenance._extract_seed(packages / original["path"], update_seed)
            descriptor = json.loads((update_seed / "runtime.json").read_text())
            parts = descriptor["component_version"].split(".")
            parts[-1] = str(int(parts[-1]) + 1)
            descriptor["component_version"] = update_version = ".".join(parts)
            write_json(update_seed / "runtime.json", descriptor)
            maintenance.build_manifest(update_seed, identifier)
            archive = server_root / "counting-update.tar.gz"
            with tarfile.open(archive, "w:gz", compresslevel=1) as stream:
                stream.add(update_seed, arcname=".")
            entries.append(dict(original, id=identifier, version=update_version, path=archive.name,
                                bytes=archive.stat().st_size, sha256=maintenance._digest(archive),
                                manifest_sha256=maintenance._digest(update_seed / maintenance.MANIFEST_NAME)))
            latest[identifier] = update_version
            publish()
            plan = require(check_component_updates(home, configuration), "updates_available")["plan"]
            if [target["id"] for target in plan["targets"]] != [identifier]:
                raise AssertionError("The module-only update unexpectedly changed other targets")
            staged = require(stage_component_updates(home, plan, configuration), "staged")["plan"]
            # A fresh process reloads the signed plan and verifies installed imports.
            restart("updated-runtime", staged)
            for identity, pointer in pointers.items():
                if (home / "components" / identity / "current.json").read_bytes() != pointer:
                    raise AssertionError("Module-only update changed another component: " + identity)
            for item in entries[:-1]:
                if requests["/" + item["path"]] != 1:
                    raise AssertionError("A release component was not downloaded exactly once: " + item["id"])
            if requests["/counting-update.tar.gz"] != 1:
                raise AssertionError("The module update was not downloaded exactly once")
            if maintenance.component_status(home, module_id=identifier)["component_version"] != update_version:
                raise AssertionError("Restart did not commit the staged module version")
            write_json(output / "result.json", {"status": "passed", "package_source": str(packages),
                       "download_requests": dict(requests), "initial_versions": {item["id"]: item["version"] for item in entries[:-1]},
                       "updated_module": {"id": identifier, "version": update_version, "fixture": "actual module payload with incremented metadata version"},
                       "unchanged_components": list(pointers), "temporary_home_removed_on_exit": True,
                       "key_scope": "ephemeral local verification only", "native_verifier": str(configuration.native_library)})
            print("Passed signed HTTP install, process restart, installed runtime/model loads and module-only update.", flush=True)


if __name__ == "__main__":
    main()
