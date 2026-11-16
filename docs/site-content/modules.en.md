Project Leap v1.0.0 currently offers two initial modules: one for measuring target-channel fluorescence within different astrocyte regions, and one for spatial cell localization and counting. The brief descriptions below introduce each module; full details are available in the corresponding GitHub repositories.

<section class="module-detail" aria-labelledby="fluorescence" markdown="1">

## Astrocyte Fluorescence Intensity Measurement {#fluorescence}

This module measures fluorescence intensity separately in the **Whole Cell** region, the **Soma** (cell body), and the **Processes**. Input images must include **DAPI**, at least one structural marker (**eGFP and/or GFAP**), and one target channel to be measured: **KCNN1, KCNN2, KCNN3, or KCNJ10**. When GFAP is the only structural marker used, analysis is restricted to **Mature** samples.

The software uses DAPI and structural markers to select the analysis depth and define cell regions, then measures target channel fluorescence within those regions. Measurements use a two-dimensional maximum intensity projection of the selected Z range. Before measurement, researchers can connect to Fiji (ImageJ) to compare multiple sets of images and edit the whole-cell, soma, and process regions. The reported measurements retain the target channel’s raw intensity, including its background signal.

The output measurement table records multiple fluorescence metrics for each cell and its regions. Fluorescence overlays and ROI coordinate files, which can be imported back into Fiji, are also exported for subsequent visual inspection.

<div class="actions" markdown="0">
<a class="button secondary" href="https://github.com/DingchengWang/Project-Leap-2D-Integrated-IHC-fluorescence-analysis/blob/main/docs/guide.en.md">Read the fluorescence analysis guide <span aria-hidden="true">↗</span></a>
<a class="text-link" href="https://github.com/DingchengWang/Project-Leap-2D-Integrated-IHC-fluorescence-analysis">View module source code <span aria-hidden="true">↗</span></a>
</div>
</section>

<section class="module-detail" aria-labelledby="counting" markdown="1">

## Cell Counting {#counting}

This module counts astrocytes using **DAPI** and an additional user-defined auxiliary channel. The software uses **DAPI** to locate cell nuclei and, in turn, identify cells. Soma and perinuclear signal patterns in the auxiliary channel further support counting accuracy and help users screen cells manually. In the preview window, users can inspect cells across Z planes, add cells, merge duplicates, and delete or adjust existing markers.

Outputs include fluorescence overlays labeled with cell IDs and CSV count tables. Users can also reopen and edit previous analyses within the application (retain the source TIFF images if you need to revisit the original Z planes).

<div class="actions" markdown="0">
<a class="button secondary" href="https://github.com/DingchengWang/Project-Leap-Cell-Counting/blob/main/docs/guide.en.md">Read the cell counting guide <span aria-hidden="true">↗</span></a>
<a class="text-link" href="https://github.com/DingchengWang/Project-Leap-Cell-Counting">View module source code <span aria-hidden="true">↗</span></a>
</div>
</section>
