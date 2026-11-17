# Project Leap

[中文](README_中文.md) · [Website](https://dingchengwang.github.io/Project-Leap/en/)

Project Leap is a modular Mac platform for analyzing astrocytes in multichannel immunohistochemistry (IHC) fluorescence images. It combines advanced open-source models with original designs to delineate detailed regions of interest (ROIs) against complex image backgrounds. This unique ROI delineation method provides the foundation for a modular design that developers can extend with additional analysis features.

The two current modules measure fluorescence intensity within astrocyte regions and locate and count cells in 3D image stacks. The fluorescence module integrates a customized Fiji (ImageJ) workflow for automatic or semi-automatic measurement. All scientific computations run locally.

## Install and start

[Download Project Leap for Mac](https://github.com/DingchengWang/Project-Leap/releases/download/v1.0.0/Project-Leap-1.0.0-macOS-arm64.dmg). The application requires **Apple Silicon and macOS 27 or later**. Copy **Project Leap.app** from the DMG to Applications, then install the modules you need under **Modules & Maintenance**. Project Leap downloads their required components.

The [user guide](docs/user-guide.en.md) covers first launch, module installation, saved analyses, and settings. Current versions and the [earlier standalone script package](https://dingchengwang.github.io/Project-Leap/en/downloads/#legacy) are listed on the [download page](https://dingchengwang.github.io/Project-Leap/en/downloads/); application changes are recorded in [GitHub Releases](https://github.com/DingchengWang/Project-Leap/releases).

## Choose an analysis module

| Module | Input and result | Documentation |
|---|---|---|
| Astrocyte Fluorescence Intensity Measurement | Requires DAPI, eGFP and/or GFAP, and one target channel: KCNN1, KCNN2, KCNN3, or KCNJ10. Measures raw target-channel intensity, including background, in Whole Cell, Soma, and Processes regions on a 2D maximum intensity projection of the selected Z range. Supports Fiji ROI review and exports measurement tables, overlays, and ROI files. GFAP-only analysis is restricted to mature samples. | [Guide](https://github.com/DingchengWang/Project-Leap-2D-Integrated-IHC-fluorescence-analysis/blob/main/docs/guide.en.md) · [Source](https://github.com/DingchengWang/Project-Leap-2D-Integrated-IHC-fluorescence-analysis) |
| Cell Counting | Requires DAPI and a different, user-defined auxiliary channel. Locates cells across Z planes to distinguish cells that overlap in a 2D view. Supports manual review and editing, and exports confirmed Astrocyte counts as CSV tables and numbered overlays, with an editable session. | [Guide](https://github.com/DingchengWang/Project-Leap-Cell-Counting/blob/main/docs/guide.en.md) · [Source](https://github.com/DingchengWang/Project-Leap-Cell-Counting) |

The application stores job records and results separately from the source TIFF images. Keep the source images to rerun analyses or revisit the original Z planes.

## Source and development

This repository contains the desktop application, shared platform code, build tools, and website. The analysis engines live in the two module repositories linked above. Developers can follow the [build instructions](tools/build/README.md) and consult the [interface contract](docs/implementation/CONTRACT.md) for application–engine requests and results.

## Author and licenses

Developed by **Dingcheng Wang**, a neuroscience undergraduate at **The Ohio State University**. [About Project Leap](docs/about.en.md) describes the research affiliation, AI-assisted development, and upstream methods.

Original Project Leap code is licensed under [Apache License 2.0](LICENSE). Third-party software and models retain their own licenses and notices; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
