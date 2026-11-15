"""Read-only TIFF inspection and plane-streamed, lossless channel import.

This module has no Qt or scientific-analysis dependencies. ``progress`` receives
one JSON-compatible dict per written plane; ``cancelled`` is a zero-argument
predicate. Omitted series/time selections are accepted only for singleton axes.
"""
from __future__ import annotations

import math
import os
from pathlib import Path
import shutil
import tempfile
import time
from fractions import Fraction
import xml.etree.ElementTree as ET

import numpy as np
import tifffile as tf


CHANNEL_ROLES = ("DAPI", "eGFP", "GFAP", "KCNN1", "KCNN2", "KCNN3", "KCNJ10", "Ignore")


class ImportImageError(ValueError):
    """A technical import failure with a stable machine-readable code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class ImportCancelled(ImportImageError):
    def __init__(self):
        super().__init__("cancelled", "图像导入已取消。")


def _unit_factor(unit):
    normalized = str(unit).replace("\\u00B5", "u").replace("µ", "u").replace("μ", "u").strip().lower()
    return {"um": 1.0, "micron": 1.0, "microns": 1.0, "micrometer": 1.0,
            "micrometers": 1.0, "micrometre": 1.0, "micrometres": 1.0,
            "nm": 0.001, "mm": 1000.0, "cm": 10000.0, "m": 1000000.0}.get(normalized)


def _scale(value, unit):
    factor = _unit_factor(unit)
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number * factor if factor is not None and math.isfinite(number) and number > 0 else None


def _ome_pixels(tif, index):
    if not tif.is_ome:
        return None
    try:
        root = ET.fromstring(tif.ome_metadata)
        images = root.findall("{*}Image")
        pixels = images[index].find("{*}Pixels")
    except (ET.ParseError, IndexError, TypeError) as exc:
        raise ImportImageError("invalid_ome_metadata", "无法读取所选 OME 图像的 Pixels 元数据。") from exc
    if pixels is None:
        raise ImportImageError("invalid_ome_metadata", "所选 OME 图像缺少 Pixels 元数据。")
    return pixels


def _calibration(tif, series, index):
    """Interpret explicit OME, SCIFIO, ImageJ and TIFF calibration fields."""
    sizes = {axis: None for axis in "XYZ"}
    sources = {axis: None for axis in "XYZ"}
    pixels = _ome_pixels(tif, index)
    if pixels is not None:
        for axis in "XYZ":
            sizes[axis] = _scale(pixels.get("PhysicalSize" + axis), pixels.get("PhysicalSize" + axis + "Unit", "um"))
            if sizes[axis] is not None:
                sources[axis] = "OME PhysicalSize" + axis
    ij = tif.imagej_metadata or {}
    spacing = _scale(ij.get("spacing"), ij.get("unit"))
    if sizes["Z"] is None and spacing is not None:
        sizes["Z"], sources["Z"] = spacing, "ImageJ spacing/unit"
    axes = [a.strip().upper() for a in str(ij.get("axes", "")).split(",")]
    scales = str(ij.get("scales", "")).split(",")
    units = str(ij.get("units", "")).split(",")
    for axis in "XYZ":
        if sizes[axis] is None and axis in axes and len(axes) == len(scales):
            pos = axes.index(axis)
            unit = units[pos] if len(units) == len(axes) else ij.get("unit")
            sizes[axis] = _scale(scales[pos], unit)
            if sizes[axis] is not None:
                sources[axis] = "SCIFIO axes/scales/units"
    tags = series.pages[0].tags
    resolution_unit = int(tags["ResolutionUnit"].value) if "ResolutionUnit" in tags else 1
    factor = {2: 25400.0, 3: 10000.0}.get(resolution_unit)
    if factor is None and resolution_unit == 1 and tif.is_imagej:
        factor = _unit_factor(ij.get("unit"))
    for axis in "XY":
        tag = tags.get(axis + "Resolution")
        if sizes[axis] is None and tag is not None and factor is not None:
            num, den = tag.value
            if num > 0 and den > 0:
                sizes[axis] = factor * den / num
                sources[axis] = "TIFF resolution" + (" / ImageJ unit" if resolution_unit == 1 else " / ResolutionUnit")
    return sizes, sources


def _series_info(tif, series, index):
    axes, shape = str(series.axes), [int(n) for n in series.shape]
    sizes = dict(zip(axes, shape))
    page = series.pages[0]
    physical, physical_sources = _calibration(tif, series, index)
    count = sizes.get("C", 1)
    hints = ["Channel " + str(i + 1) for i in range(count)]
    pixels = _ome_pixels(tif, index)
    if pixels is not None:
        channels = pixels.findall("{*}Channel")
        if len(channels) == count:
            hints = [channel.get("Name") or hints[i] for i, channel in enumerate(channels)]
    info = {"series_index": index, "name": str(series.name or "Image " + str(index + 1)),
            "axes": axes, "shape": shape, "dtype": str(series.dtype),
            "channel_count": count, "time_count": sizes.get("T", 1), "z_count": sizes.get("Z", 1),
            "physical_size_um": physical, "calibration_sources": physical_sources,
            "channel_hints": hints, "photometric": page.photometric.name,
            "samples_per_pixel": int(page.samplesperpixel), "page_count": len(series.pages)}
    try:
        _validate_layout(series)
        info["supported_layout"] = True
        info["layout_error"] = None
    except ImportImageError as exc:
        info["supported_layout"] = False
        info["layout_error"] = {"code": exc.code, "message": str(exc)}
    return info


def _validate_layout(series):
    axes, shape = str(series.axes), tuple(series.shape)
    if (len(axes) != len(shape) or len(set(axes)) != len(axes) or
            not axes.endswith("YX") or any(a not in "TCZYX" for a in axes) or "Z" not in axes):
        raise ImportImageError("unsupported_axes", f"需要明确的 Z、Y、X 轴及可选 C/T 轴；实际 axes={axes!r}, shape={shape}。")
    if np.dtype(series.dtype) not in (np.dtype("uint8"), np.dtype("uint16"), np.dtype("float32")):
        raise ImportImageError("unsupported_dtype", f"ImageJ 工作 TIFF 不支持无转换写入 {series.dtype}；支持 uint8、uint16、float32。")
    page = series.pages[0]
    if int(page.samplesperpixel) != 1 or int(page.photometric) not in (1, 3):
        raise ImportImageError("unsupported_samples", "需要每像素一个标量样本的 MINISBLACK 或 PALETTE TIFF。")
    if tuple(page.shape) != shape[-2:] or str(page.axes) != "YX":
        raise ImportImageError("unsupported_page_layout", "TIFF 页必须对应完整的单个 YX 平面。")
    expected = math.prod(shape[:-2])
    if len(series.pages) != expected:
        raise ImportImageError("incomplete_pages", f"明确轴需要 {expected} 个平面，文件提供 {len(series.pages)} 个。")


def inspect_image(path) -> dict:
    """Read headers only. Multi-series files expose each series without selection."""
    source = Path(path).expanduser().resolve(strict=True)
    if not source.is_file():
        raise ImportImageError("not_a_file", "请选择一个 TIFF 文件。")
    with tf.TiffFile(str(source)) as tif:
        if not tif.series:
            raise ImportImageError("missing_series", "TIFF 文件中没有可读取的图像组。")
        series = [_series_info(tif, s, i) for i, s in enumerate(tif.series)]
        info = {"path": str(source), "name": source.name, "size_bytes": source.stat().st_size,
                "series_count": len(series), "series": series, "requires_series_selection": len(series) > 1,
                "is_imagej": bool(tif.is_imagej), "is_ome": bool(tif.is_ome)}
        keys = ("axes", "shape", "dtype", "channel_count", "time_count", "z_count",
                "physical_size_um", "calibration_sources", "channel_hints", "supported_layout", "layout_error")
        info.update({key: series[0][key] if len(series) == 1 else None for key in keys})
        return info


def create_preview(source_path, preview_path) -> dict:
    """Render one C1/middle-Z plane for display, at most 768 pixels per side.

    uint8 uses its native 0..255 display range. uint16/float32 uses the finite
    values' min/max range on this plane only; analysis pixels remain untouched.
    Ambiguous series/time selection is reported instead of selecting silently.
    """
    from PIL import Image

    source = Path(source_path).expanduser().resolve(strict=True)
    target = Path(preview_path).expanduser()
    if not target.is_absolute():
        raise ImportImageError("invalid_preview_path", "预览输出必须为绝对路径。")
    target = target.resolve()
    if target.parent == source.parent or source.parent in target.parents:
        raise ImportImageError("source_directory_read_only", "预览必须保存在原图目录之外的应用缓存中。")
    if target.suffix.lower() != ".png":
        raise ImportImageError("invalid_preview_path", "预览输出必须为 PNG 文件。")
    with tf.TiffFile(str(source)) as tif:
        selected = _selection(None, len(tif.series), "series")
        series = tif.series[selected]
        _validate_layout(series)
        dimensions = dict(zip(series.axes, series.shape))
        _selection(None, dimensions.get("T", 1), "time")
        z_index = int(dimensions["Z"] // 2)
        coordinates = {"Z": z_index, "C": 0, "T": 0}
        page_index = int(np.ravel_multi_index(tuple(coordinates[a] for a in series.axes[:-2]), series.shape[:-2]))
        page = series.pages[page_index]
        if page is None:
            raise ImportImageError("missing_plane", f"图像缺少预览平面 {page_index}。")
        plane = page.asarray()
        if tuple(plane.shape) != tuple(series.shape[-2:]) or plane.dtype != series.dtype:
            raise ImportImageError("inconsistent_plane", "预览平面的尺寸或位深与元数据不一致。")
    if plane.dtype == np.dtype("uint8"):
        display = plane
    else:
        finite = np.isfinite(plane)
        display = np.zeros(plane.shape, dtype="uint8")
        if finite.any():
            values = plane[finite].astype("float64")
            low, high = float(values.min()), float(values.max())
            if high > low:
                display[finite] = np.clip((values - low) * (255.0 / (high - low)), 0, 255).astype("uint8")
    image = Image.fromarray(display)
    image.thumbnail((768, 768), Image.Resampling.LANCZOS)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(prefix=".leap-preview-", suffix=".png", dir=str(target.parent), delete=False) as handle:
            temporary = Path(handle.name)
        image.save(temporary, format="PNG")
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return {"preview_path": str(target), "preview_channel_index": 0, "preview_z_index": z_index}


def _selection(value, count, axis):
    if value is None:
        if count != 1:
            raise ImportImageError(axis + "_selection_required", f"文件含 {count} 个 {axis}，请明确选择索引。")
        return 0
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < count:
        raise ImportImageError("invalid_" + axis + "_index", f"{axis} 索引必须在 0 到 {count - 1} 之间。")
    return value


def _resolution_rational(pixel_um):
    """Use explicit 32-bit rationals, avoiding tifffile's float clipping."""
    value = Fraction(10000) / Fraction(str(pixel_um))
    max_int = 2**32 - 1
    denominator_limit = min(max_int, int(Fraction(max_int, 1) / value))
    if denominator_limit < 1:
        raise ImportImageError("unrepresentable_calibration", "XY 标定无法表示为 TIFF 厘米分辨率。")
    value = value.limit_denominator(denominator_limit)
    actual_um = 10000.0 * value.denominator / value.numerator
    if not math.isclose(actual_um, pixel_um, rel_tol=1e-12, abs_tol=0.0):
        raise ImportImageError("unrepresentable_calibration", "TIFF 有理数精度不足以保持此 XY 标定。")
    return value.numerator, value.denominator


def split_image(source_path, destination_dir, channel_roles, *, profile=None,
                series_index=None, time_index=None, progress=None, cancelled=None) -> dict:
    """Create fresh single-channel ZYX TIFFs; never overwrite existing files.

    Source files are opened read-only. Temporary files are contained in one owned
    directory, and cancellation removes only files created by this invocation.
    """
    started = time.monotonic()
    source = Path(source_path).expanduser().resolve(strict=True)
    destination = Path(destination_dir).expanduser().resolve()
    if destination == source.parent or source.parent in destination.parents:
        raise ImportImageError("source_directory_read_only", "导入输出必须位于原图目录之外。")
    if profile not in (None, "mature", "neonatal"):
        raise ImportImageError("invalid_profile", "profile 必须为 null、mature 或 neonatal。")
    if not isinstance(channel_roles, (list, tuple)):
        raise ImportImageError("invalid_channel_roles", "channel_roles 必须按源通道顺序提供列表。")
    def check_cancel():
        if cancelled is not None and cancelled():
            raise ImportCancelled()
    check_cancel()
    created = []
    temporary = None
    try:
        with tf.TiffFile(str(source)) as tif:
            selected = _selection(series_index, len(tif.series), "series")
            series = tif.series[selected]
            _validate_layout(series)
            info = _series_info(tif, series, selected)
            selected_time = _selection(time_index, info["time_count"], "time")
            if len(channel_roles) != info["channel_count"] or any(role not in CHANNEL_ROLES for role in channel_roles):
                raise ImportImageError("invalid_channel_roles", "通道角色数量必须等于源通道数，且每个角色必须来自支持列表。")
            kept = [(i, role) for i, role in enumerate(channel_roles) if role != "Ignore"]
            if not kept or len({role for _, role in kept}) != len(kept):
                raise ImportImageError("invalid_channel_roles", "至少保留一个通道；每个非 Ignore 角色只允许出现一次。")
            physical = info["physical_size_um"]
            missing = [axis for axis in "XYZ" if physical[axis] is None]
            if missing:
                raise ImportImageError("missing_calibration", "缺少可读取的物理标定：" + ", ".join(missing))
            resolution = tuple(_resolution_rational(physical[a]) for a in "XY")
            suffix = "-" + profile if profile else ""
            outputs = [(channel, role, destination / f"C{channel + 1}-{role}{suffix}.tif") for channel, role in kept]
            if any(path.exists() or path.is_symlink() for _, _, path in outputs):
                raise ImportImageError("destination_exists", "目标通道文件已存在；请选择新的任务目录。")
            destination.mkdir(parents=True, exist_ok=True)
            temporary = Path(tempfile.mkdtemp(prefix=".leap-import-", dir=str(destination)))
            shape = tuple(info["shape"])
            axes = info["axes"]
            leading_shape = shape[:-2]
            z_count = info["z_count"]
            total = z_count * len(kept)
            written = 0
            for channel, role, final in outputs:
                check_cancel()
                def planes():
                    nonlocal written
                    for z in range(z_count):
                        check_cancel()
                        coords = {"Z": z, "C": channel, "T": selected_time}
                        page_index = int(np.ravel_multi_index(tuple(coords[a] for a in axes[:-2]), leading_shape))
                        page = series.pages[page_index]
                        if page is None:
                            raise ImportImageError("missing_plane", f"图像缺少平面 {page_index}。")
                        array = page.asarray()
                        if tuple(array.shape) != shape[-2:] or array.dtype != series.dtype:
                            raise ImportImageError("inconsistent_plane", f"平面 {page_index} 的尺寸或位深与图像元数据不一致。")
                        yield array
                        written += 1
                        if progress is not None:
                            progress({"stage": "import", "current": written, "total": total,
                                      "channel_index": channel, "role": role, "z_index": z,
                                      "message": f"正在拆分 {role}：{z + 1}/{z_count}"})
                # Write a scalar ImageJ stack with explicit XYZ calibration.
                with tf.TiffWriter(str(temporary / final.name), imagej=True) as writer:
                    writer.write(planes(), shape=(z_count, *shape[-2:]), dtype=series.dtype,
                                 photometric="minisblack", resolution=resolution,
                                 resolutionunit="CENTIMETER", metadata={"axes": "ZYX", "unit": "um",
                                 "spacing": physical["Z"]}, contiguous=True)
            check_cancel()
            # Hard links provide no-overwrite publication on the same filesystem.
            # Rollback tracks only successful links from this invocation.
            for _, _, final in outputs:
                check_cancel()
                os.link(temporary / final.name, final)
                created.append(final)
            check_cancel()
            return {"files": {role: str(path) for _, role, path in outputs}, "source": str(source),
                    "metadata": {**info, "series_index": selected, "time_index": selected_time,
                                 "channel_roles": list(channel_roles), "profile": profile,
                                 "output_axes": "ZYX", "output_shape": [z_count, *shape[-2:]],
                                 "output_resolution_cm": [list(pair) for pair in resolution]},
                    "elapsed_seconds": time.monotonic() - started}
    except BaseException:
        for path in created:
            path.unlink(missing_ok=True)
        raise
    finally:
        if temporary is not None:
            shutil.rmtree(temporary)
