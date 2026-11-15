The two modules address different questions: how much target-channel fluorescence lies within an astrocyte region, and which cell records should contribute to a count. Both use multichannel TIFF images and provide visual review tools for the proposed result.

<section class="module-detail" aria-labelledby="fluorescence" markdown="1">

## Astrocyte fluorescence intensity measurement {#fluorescence}

The fluorescence module measures target fluorescence across the analyzed cell region (**Whole Cell**), with separate measurements for the cell body (**Soma**) and processes (**Processes**). Each analysis requires **DAPI**, at least one structural marker (**eGFP and/or GFAP**), and exactly one target: **KCNN1, KCNN2, KCNN3, or KCNJ10**. The GFAP-only route supports **Mature** samples only.

DAPI and the selected structural channels provide the evidence for choosing analysis depth and defining cell regions. The target channel supplies the fluorescence values measured within those regions.

Measurements use a two-dimensional maximum-intensity projection of the selected Z range. In Fiji, you can compare the proposed boundaries with the image and edit the linked cell, soma, and process regions before measurement. The measurements retain the target channel’s original intensity scale and background signal.

The measurement workbook links each value to a cell and region. Labeled overlays and an ROI archive preserve the reviewed boundaries.

<div class="actions" markdown="0">
<a class="button secondary" href="https://github.com/DingchengWang/Project-Leap-2D-Integrated-IHC-fluorescence-analysis/blob/main/docs/guide.en.md">Read the fluorescence guide <span aria-hidden="true">↗</span></a>
<a class="text-link" href="https://github.com/DingchengWang/Project-Leap-2D-Integrated-IHC-fluorescence-analysis">View module source <span aria-hidden="true">↗</span></a>
</div>
</section>

<section class="module-detail" aria-labelledby="counting" markdown="1">

## Cell counting {#counting}

This module uses **DAPI** and a **different support channel** to propose cells for an astrocyte count. The support channel contributes cell-body and perinuclear evidence; both its source channel and marker must be specified.

Each cell record links observations across Z slices, so a candidate can be examined at multiple depths under one identity. The counting workbench shows a maximum-intensity projection for an overview and original Z slices for closer inspection. You can add missing cells, merge duplicate records, delete objects, or change which records are accepted.

The displayed total counts accepted, non-deleted records labeled **Astrocyte**. Other objects remain available for review and enter the total only if accepted.

A confirmed result contains a numbered overlay, a CSV count table, and an editable session. Saved sessions reopen from Jobs &amp; Results; keep the source TIFF available to inspect the original Z slices again.

<div class="actions" markdown="0">
<a class="button secondary" href="https://github.com/DingchengWang/Project-Leap-Cell-Counting/blob/main/docs/guide.en.md">Read the counting guide <span aria-hidden="true">↗</span></a>
<a class="text-link" href="https://github.com/DingchengWang/Project-Leap-Cell-Counting">View module source <span aria-hidden="true">↗</span></a>
</div>
</section>
