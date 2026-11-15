"""Import and load fixed models from the exact pruned scientific payload."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from .release_policy import excluded_seed_entry


PROBE = r'''
import importlib, json, sys
from pathlib import Path
names = ['numpy', 'numpy.testing', 'scipy', 'scipy.ndimage', 'scipy.stats',
         'scipy.spatial', 'skimage', 'skimage.measure', 'skimage.morphology',
         'matplotlib', 'tifffile', 'torch', 'torchvision',
         'cellpose.models', 'cellpose.io', 'project_leap_2d.desktop_adapter',
         'leap_cell_counting.cell_pipeline']
versions = {}
for name in names:
    module = importlib.import_module(name)
    versions[name] = getattr(module, '__version__', 'imported')
import numpy as np
from scipy import ndimage
assert ndimage.label(np.eye(3))[1] == 3
import torch
from cellpose import models
model = models.CellposeModel(gpu=False, pretrained_model=sys.argv[1])
assert model.net is not None
from project_leap_2d.nuclei.instanseg_nucleus_detection import get_instanseg_model, validate_instanseg_model_resources
metadata = validate_instanseg_model_resources()
instanseg = get_instanseg_model()
assert instanseg is not None
print(json.dumps({'imports': versions, 'cellpose_model_loaded': True, 'instanseg_model_loaded': True,
                  'instanseg_metadata': metadata, 'numpy_scipy_computation': True}, default=str))
'''


def verify_pruned_seed(seed, engine, excluded):
    seed, engine = Path(seed), Path(engine)
    with tempfile.TemporaryDirectory(prefix='Leap-pruned-seed-', dir='/private/tmp') as directory:
        stage = Path(directory)
        for name in ('python', 'site-packages'):
            subprocess.run(['/bin/cp', '-cR', str(seed / name), str(stage / name)], check=True)
        manifest = json.loads((seed / 'content-manifest.json').read_text())['entries']
        retained = {name for name in manifest if name.split('/', 1)[0] in {'python', 'site-packages'}
                    and name not in excluded}
        # Match the archive payload exactly, including exclusion of any untracked
        # bytecode or locally added files that could otherwise mask a missing import.
        for target in sorted(stage.rglob('*'), key=lambda path: len(path.parts), reverse=True):
            if target.relative_to(stage).as_posix() in retained:
                continue
            if target.is_symlink() or target.is_file():
                target.unlink()
            elif target.is_dir():
                target.rmdir()
        remaining = [str(p.relative_to(stage)) for p in stage.rglob('*') if excluded_seed_entry(str(p.relative_to(stage)))]
        if remaining:
            raise RuntimeError('Excluded scientific test files remain: ' + repr(remaining[:10]))
        environment = {key: value for key, value in os.environ.items()
                       if not key.startswith(('PYTHON', 'DYLD_'))}
        environment.update(PYTHONHOME=str(stage / 'python'), PYTHONPATH=os.pathsep.join((str(stage / 'site-packages'), str(engine))),
                           PYTHONNOUSERSITE='1', PYTHONDONTWRITEBYTECODE='1',
                           MPLCONFIGDIR=str(stage / 'matplotlib-cache'), XDG_CACHE_HOME=str(stage / 'cache'), CELLPOSE_LOCAL_MODELS_PATH=str(seed / 'Models'))
        result = subprocess.run([str(stage / 'python/bin/python3.9'), '-c', PROBE, str(seed / 'Models/cpsam_v2')],
                                cwd=stage, env=environment, text=True, capture_output=True, timeout=300)
        if result.returncode:
            raise RuntimeError('Pruned scientific runtime validation failed:\n' + result.stdout + '\n' + result.stderr)
        details = json.loads(result.stdout.splitlines()[-1])
        return dict(details, performed=True, exit_code=result.returncode, stderr=result.stderr,
                    excluded_entries=len(excluded), original_seed_modified=False)
