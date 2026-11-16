# About

[中文](about.zh.md) · [Project home](../README.md) · [User guide](user-guide.en.md)

## Author and research

Project Leap was developed by Dingcheng Wang, an undergraduate neuroscience student at The Ohio State University. The research projects it primarily supports are based in the Min Zhou Laboratory, Department of Neuroscience, College of Medicine, at The Ohio State University.

The software’s research requirements, biological interpretations, internal analysis methods, and cell parameters are all original. GPT-5.6 Sol / GPT-6 Astra assisted with code implementation, debugging, packaging, and documentation during development.

## Open-source tools used in the software

The following are upstream analysis tools that perform different tasks:

- [Cellpose-SAM](https://www.biorxiv.org/content/10.1101/2025.04.28.651001v1.full): provides the base segmentation for fluorescence analysis with eGFP and for the cell counting module.
- [InstanSeg fluorescence model](https://doi.org/10.1101/2024.09.04.611150): generates candidate DAPI nuclear regions for GFAP-only fluorescence analysis of mature samples. Project Leap then combines these candidates with GFAP structural patterns to define cell regions.
- [Fiji](https://doi.org/10.1038/nmeth.2019): used to interactively review fluorescence analysis regions and measure the intensity of the raw target-channel projection within the final regions.

## Source code and licensing

The source code is maintained in three repositories:

- [Project Leap](https://github.com/DingchengWang/Project-Leap): the desktop application and shared platform.
- [Astrocyte fluorescence module](https://github.com/DingchengWang/Project-Leap-2D-Integrated-IHC-fluorescence-analysis): the fluorescence analysis engine and guide.
- [Cell counting module](https://github.com/DingchengWang/Project-Leap-Cell-Counting): the counting engine and guide.

Original Project Leap code is licensed under the [Apache License 2.0](../LICENSE), with attribution in [NOTICE](../NOTICE). Third-party software and models retain their respective licenses and copyright notices. [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md) contains the retained licensing materials and links to upstream sources.
