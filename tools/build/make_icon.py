#!/usr/bin/env python3
"""Rasterize the approved SVG at native macOS icon sizes and create ICNS."""
from __future__ import annotations
import argparse
import os
from pathlib import Path
import subprocess

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--svg", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    application = QGuiApplication([])
    renderer = QSvgRenderer(str(args.svg))
    if not renderer.isValid():
        raise ValueError("Application SVG icon is invalid")
    iconset = args.output.with_suffix(".iconset")
    iconset.mkdir(parents=True, exist_ok=True)
    for size in (16, 32, 128, 256, 512):
        for scale in (1, 2):
            pixels = size * scale
            image = QImage(pixels, pixels, QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(Qt.GlobalColor.transparent)
            painter = QPainter(image)
            renderer.render(painter)
            painter.end()
            suffix = "@2x" if scale == 2 else ""
            if not image.save(str(iconset / f"icon_{size}x{size}{suffix}.png")):
                raise RuntimeError("Unable to save icon raster")
    subprocess.run(["/usr/bin/iconutil", "-c", "icns", str(iconset), "-o", str(args.output)], check=True)
    print(args.output)


if __name__ == "__main__":
    main()
