"""Read the installed application's version; source runs use the package version."""
import plistlib
from pathlib import Path
import sys

from . import __version__


def application_version():
    if getattr(sys, "frozen", False):
        executable = Path(sys.executable).resolve()
        for parent in executable.parents:
            if parent.suffix == ".app":
                with (parent / "Contents/Info.plist").open("rb") as stream:
                    info = plistlib.load(stream)
                return str(info["CFBundleShortVersionString"])
    return __version__


def application_build():
    if getattr(sys, "frozen", False):
        for parent in Path(sys.executable).resolve().parents:
            if parent.suffix == ".app":
                with (parent / "Contents/Info.plist").open("rb") as stream:
                    return str(plistlib.load(stream)["CFBundleVersion"])
    return "1"
