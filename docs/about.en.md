# About Project Leap

[中文](about.zh.md) · [Project home](../README.md) · [User guide](user-guide.en.md)

Project Leap is a local research platform for immunohistochemistry image analysis. Its desktop application hosts two modules: **Astrocyte Fluorescence Intensity Measurement** and **Cell Counting**. The platform handles installation, image import, job records, results, and maintenance; each module supplies its analysis workflow and review tools.

## Author and affiliation

**Dingcheng Wang** developed Project Leap as a neuroscience undergraduate at **The Ohio State University**, with research affiliation to the **Min Zhou Laboratory, Department of Neuroscience, College of Medicine**. The application’s About panel records this affiliation, and the project [NOTICE](../NOTICE) records the author attribution.

## Development and scientific responsibility

AI tools assisted with code implementation, debugging, packaging, and documentation. Dingcheng Wang directed the research requirements, biological interpretation, analysis rules, trade-offs, and acceptance review.

Researchers using Project Leap remain responsible for deciding whether the analysis fits their experiment, reviewing the proposed regions or cell identities, and interpreting the saved measurements. Each module guide describes its inputs, review procedure, and scientific scope.

## Open-source code and third-party components

Original Project Leap code is licensed under [Apache License 2.0](../LICENSE), with attribution in [NOTICE](../NOTICE). Third-party software and models retain their respective licenses and copyright notices. [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md) links the retained license material and upstream sources.

The source is maintained in three repositories:

- [Project Leap](https://github.com/DingchengWang/Project-Leap): desktop application and shared platform.
- [Astrocyte fluorescence module](https://github.com/DingchengWang/Project-Leap-2D-Integrated-IHC-fluorescence-analysis): fluorescence analysis engine and guide.
- [Cell Counting module](https://github.com/DingchengWang/Project-Leap-Cell-Counting): counting engine and guide.

## Scientific references

The application’s **Sources & references** page lists these upstream methods and tools:

- [Cellpose-SAM](https://www.biorxiv.org/content/10.1101/2025.04.28.651001v1.full).
- [InstanSeg fluorescence model](https://doi.org/10.1101/2024.09.04.611150).
- [Fiji](https://doi.org/10.1038/nmeth.2019).

The same reference links are retained in the application’s [reference resource](../app/leap_desktop/docs/references.html).
