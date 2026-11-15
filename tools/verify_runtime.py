#!/usr/bin/env python3
"""Import and relocation smoke checks without launching an analysis or Fiji GUI."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
PROBE = r'''
import json, sys, sysconfig, importlib.metadata as md
from pathlib import Path
import numpy, scipy, skimage, torch, torchvision, cv2, tifffile, cellpose, matplotlib
print(json.dumps({
"python":sys.version, "executable":sys.executable, "prefix":sys.prefix,
"sysconfig_paths":sysconfig.get_paths(),
"sysconfig_install_variables": {key:sysconfig.get_config_var(key) for key in ["BINDIR", "LIBDIR", "INCLUDEPY", "LIBPL", "prefix", "exec_prefix"]},
"paths":sys.path, "mps_built":torch.backends.mps.is_built(),
"mps_available":torch.backends.mps.is_available(),
"imports": {n:md.version(n) for n in ["numpy","scipy","scikit-image","torch","torchvision","opencv-python-headless","tifffile","cellpose","matplotlib"]},
"origins": {m.__name__:m.__file__ for m in [numpy, scipy, skimage, torch, torchvision, cv2, tifffile, cellpose, matplotlib]},
"matplotlib_cache":matplotlib.get_cachedir()
}, indent=2))
'''


def verify(seed: Path, output: Path) -> dict:
    seed = seed.resolve()
    descriptor = json.loads((seed / "runtime.json").read_text())
    cache = ROOT / "runtime/cache/verification"
    for name in ["home", "xdg", "torch", "hf", "matplotlib", "tmp", "java-prefs"]:
        (cache / name).mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update({"PYTHONPATH":str(seed / descriptor["site_packages"]), "PYTHONDONTWRITEBYTECODE":"1",
                "PYTHONNOUSERSITE":"1", "HOME":str(cache / "home"), "XDG_CACHE_HOME":str(cache / "xdg"),
                "TORCH_HOME":str(cache / "torch"), "HF_HOME":str(cache / "hf"),
                "MPLCONFIGDIR":str(cache / "matplotlib"), "TMPDIR":str(cache / "tmp"),
                "CELLPOSE_LOCAL_MODELS_PATH":str(seed / descriptor["models"]),
                "JAVA_TOOL_OPTIONS":f'-Djava.io.tmpdir="{cache / "tmp"}" -Djava.util.prefs.userRoot="{cache / "java-prefs"}"'})
    env.pop("PYTHONHOME", None)
    process = subprocess.run([str(seed / descriptor["python"]), "-c", PROBE], env=env, text=True, capture_output=True)
    if process.returncode:
        raise RuntimeError(process.stdout + process.stderr)
    probe = json.loads(process.stdout)
    for path in [probe["executable"], probe["prefix"], *probe["origins"].values()]:
        if not Path(path).resolve().is_relative_to(seed):
            raise ValueError(f"Import escaped runtime: {path}")
    if "Project Leap 2D Support" in json.dumps(probe):
        raise ValueError("Production Support remains in runtime paths")
    java = next((seed / "Fiji").glob("java/**/bin/java"))
    java_result = subprocess.run([str(java), "-version"], env=env, text=True, capture_output=True)
    if java_result.returncode:
        raise RuntimeError(java_result.stderr)
    report = {"checked_at_utc":datetime.now(timezone.utc).isoformat(), "seed":str(seed),
              "import_probe":probe, "import_stderr":process.stderr, "java":str(java),
              "java_version":java_result.stderr, "fiji_launcher":str(seed / descriptor["fiji_launcher"]),
              "fiji_launcher_executable":os.access(seed / descriptor["fiji_launcher"], os.X_OK),
              "analysis_executed":False, "fiji_gui_executed":False}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=Path, default=ROOT / "runtime/seed")
    parser.add_argument("--output", type=Path, default=ROOT / "runtime/logs/runtime-verification.json")
    args = parser.parse_args()
    verify(args.seed, args.output)
