# Project Leap

[中文](README_中文.md) · [Website](https://dingchengwang.github.io/Project-Leap/en/)

Project Leap is a Mac application for astrocyte fluorescence analysis and cell counting in multichannel microscopy images. Automated analysis proposes cell regions and candidate cells; visual review lets researchers check the regions measured and decide which cells enter the final count.

## Install and start

[Download Project Leap for Mac](https://github.com/DingchengWang/Project-Leap/releases/download/v1.0.0/Project-Leap-1.0.0-macOS-arm64.dmg). The application requires **Apple Silicon and macOS 27 or later**. Copy **Project Leap.app** from the DMG to Applications, then install the modules you need under **Modules & Maintenance**. Project Leap downloads their required components.

The [user guide](docs/user-guide.en.md) covers first launch, image import, task records, results, and settings. Current versions and the [earlier standalone script package](https://dingchengwang.github.io/Project-Leap/en/downloads/#legacy) are listed on the [download page](https://dingchengwang.github.io/Project-Leap/en/downloads/); application changes are recorded in [GitHub Releases](https://github.com/DingchengWang/Project-Leap/releases).

## Choose an analysis module

| Module | Input and result | Documentation |
|---|---|---|
| Astrocyte Fluorescence Intensity Measurement | DAPI, eGFP and/or GFAP, and one target channel: KCNN1, KCNN2, KCNN3, or KCNJ10. Measures whole-cell, soma, and process regions, with Fiji review and a final region archive. GFAP-only analysis supports mature samples only. | [Guide](https://github.com/DingchengWang/Project-Leap-2D-Integrated-IHC-fluorescence-analysis/blob/main/docs/guide.en.md) · [Source](https://github.com/DingchengWang/Project-Leap-2D-Integrated-IHC-fluorescence-analysis) |
| Cell Counting | DAPI and a different support channel. Links cell observations across Z slices for review and exports accepted Astrocyte records as a count table and numbered overlay, with an editable session. | [Guide](https://github.com/DingchengWang/Project-Leap-Cell-Counting/blob/main/docs/guide.en.md) · [Source](https://github.com/DingchengWang/Project-Leap-Cell-Counting) |

Analysis runs locally. The application keeps task records and output files separately from the original TIFF images, which remain in their original location.

## Source and development

This repository contains the desktop application, shared platform code, build tools, and website. The analysis engines live in the two module repositories. With all three checked out as sibling folders, run this command from the main repository to assemble a separate build tree:

```sh
python3 tools/assemble_sources.py --fluorescence ../Project-Leap-2D-Integrated-IHC-fluorescence-analysis --counting ../Project-Leap-Cell-Counting --output ../../build/source
```

The assembly tool checks the module commits recorded in `tools/build/module-sources.json`. See the [build instructions](tools/build/README.md) for dependencies and packaging, and the [interface contract](docs/implementation/CONTRACT.md) for application–engine requests and results.

## Author and licenses

Developed by **Dingcheng Wang**, a neuroscience undergraduate at **The Ohio State University**. [About Project Leap](docs/about.en.md) describes the research affiliation, AI-assisted development, and upstream methods.

Original Project Leap code is licensed under [Apache License 2.0](LICENSE). Third-party software and models retain their own licenses and notices; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
