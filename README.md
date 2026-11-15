# Project Leap

[中文](README_中文.md)

Project Leap is a local research application for immunohistochemistry (IHC) image analysis on Apple Silicon Macs. It brings image import, analysis modules, manual review, and saved results into one desktop workspace. Original images remain separate from application-managed analysis data.

**Version 1.0.0 · macOS 27 or later · Apple Silicon (arm64)**

## Download and start

The public macOS release is **in preparation**. The application DMG and online module downloads have not yet been published. Availability will be announced on the [project website](https://dingchengwang.github.io/Project-Leap/en/) and [GitHub Releases](https://github.com/DingchengWang/Project-Leap/releases); the website is also being prepared.

Once available, install **Project Leap.app** from the DMG, then use **Modules & Maintenance** to install the modules you need. The application downloads their dependencies. Follow the [platform user guide](docs/user-guide.en.md) for installation, first launch, result management, and settings.

## Analysis modules

| Module | Purpose | Documentation |
|---|---|---|
| Astrocyte Fluorescence Intensity Measurement · 1.1.0 | Measure fluorescence in reviewed astrocyte regions of interest, with Fiji review. | [Guide](https://github.com/DingchengWang/Project-Leap-2D-Integrated-IHC-fluorescence-analysis/blob/main/docs/guide.en.md) · [Source](https://github.com/DingchengWang/Project-Leap-2D-Integrated-IHC-fluorescence-analysis) |
| Cell Counting · 1.0.0 | Review and edit cell identities from DAPI and auxiliary-channel evidence, then save counts and overlays. | [Guide](https://github.com/DingchengWang/Project-Leap-Cell-Counting/blob/main/docs/guide.en.md) · [Source](https://github.com/DingchengWang/Project-Leap-Cell-Counting) |

## Source and development

This repository contains the desktop application, shared platform code, build tools, and website. The analysis engines live in the two module repositories. With all three checked out as sibling folders, run this command from the main repository to assemble a separate build tree:

```sh
python3 tools/assemble_sources.py --fluorescence ../Project-Leap-2D-Integrated-IHC-fluorescence-analysis --counting ../Project-Leap-Cell-Counting --output ../../build/source
```

The assembly tool checks the module commits recorded in `tools/build/module-sources.json`. See the [build instructions](tools/build/README.md) for dependencies and packaging, and the [interface contract](docs/implementation/CONTRACT.md) for application–engine requests and results.

## Author and licenses

Developed by **Dingcheng Wang**, a neuroscience undergraduate at **The Ohio State University**. See [About Project Leap](docs/about.en.md) for attribution, AI-assisted development, and scientific references.

Original Project Leap code is licensed under [Apache License 2.0](LICENSE). Third-party software and models retain their own licenses and notices; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
