# About Project Leap

[中文](about.zh.md) · [Project home](../README.md) · [User guide](user-guide.en.md)

## Author and research affiliation

**Dingcheng Wang** developed Project Leap as a neuroscience undergraduate at **The Ohio State University**, with research affiliation to the **Min Zhou Laboratory, Department of Neuroscience, College of Medicine**.

AI tools assisted with code implementation, debugging, packaging, and documentation. Dingcheng Wang directed the research requirements, analysis rules, biological interpretation, and development decisions, and was responsible for acceptance review.

## Methods and tools

Project Leap uses the following upstream tools for distinct parts of the analysis:

- [Cellpose-SAM](https://www.biorxiv.org/content/10.1101/2025.04.28.651001v1.full) provides segmentation for the eGFP fluorescence-analysis route and the counting module.
- [InstanSeg fluorescence model](https://doi.org/10.1101/2024.09.04.611150) supplies DAPI nuclear candidates in the mature GFAP-only fluorescence-analysis route. Project Leap uses these candidates with GFAP structure to define the cell regions.
- [Fiji](https://doi.org/10.1038/nmeth.2019) supports interactive review of fluorescence regions and measures the raw target-channel projections within the final regions.

## Source and licenses

The source is maintained in three repositories:

- [Project Leap](https://github.com/DingchengWang/Project-Leap): desktop application and shared platform.
- [Astrocyte fluorescence module](https://github.com/DingchengWang/Project-Leap-2D-Integrated-IHC-fluorescence-analysis): fluorescence analysis engine and guide.
- [Cell Counting module](https://github.com/DingchengWang/Project-Leap-Cell-Counting): counting engine and guide.

Original Project Leap code is licensed under [Apache License 2.0](../LICENSE), with attribution in [NOTICE](../NOTICE). Third-party software and models retain their respective licenses and copyright notices. [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md) links the retained license material and upstream sources.
