Project Leap currently includes two modules. The application manages their installation and maintenance; each module has its own analysis workflow and detailed guide.

<section class="module-detail" aria-labelledby="fluorescence" markdown="1">
<p class="eyebrow">MODULE 1.1.1</p>

## Astrocyte fluorescence intensity measurement {#fluorescence}

Measure target-channel fluorescence within **Whole**, **Soma**, and **Processes** regions after reviewing the proposed regions in Fiji. Whole covers the full cell region; Soma and Processes separate the cell body from its processes.

Import a supported multi-channel TIFF and assign DAPI, eGFP/GFAP, and the target measurement channels in the application. Project Leap prepares the single-channel working images while retaining the source TIFF in its original location. Review the regions in Fiji, confirm your edits, and let the analysis continue to measurement and result saving.

The output includes three overlay images, a text analysis report, a fluorescence workbook, and `IHC_2D_ROI_Archive.zip`. The archive preserves the final regions together with cell identities, calibration, selected Z range, and measurement correspondence.

### What changed from the v1.0.1 script package

Version 1.1.0 introduced the desktop workflow: direct multi-channel TIFF import, channel assignment, module maintenance, task records, result previews, and the final region archive. The application provides Automatic, Mature, and Neonatal sample-age controls; the GFAP-only path remains limited to Mature samples.

Version 1.1.1 fixes compatibility with the shared scientific runtime used by the desktop application. The Z-selection, segmentation, threshold, Fiji review, and fluorescence-measurement rules remain at scientific version 1.0.1.

<div class="actions" markdown="0">
<a class="button secondary" href="https://github.com/DingchengWang/Project-Leap-2D-Integrated-IHC-fluorescence-analysis/blob/main/docs/guide.en.md">Read the fluorescence guide <span aria-hidden="true">↗</span></a>
<a class="text-link" href="https://github.com/DingchengWang/Project-Leap-2D-Integrated-IHC-fluorescence-analysis">View module source <span aria-hidden="true">↗</span></a>
</div>
</section>

<section class="module-detail" aria-labelledby="counting" markdown="1">
<p class="eyebrow">MODULE 1.0.0</p>

## Cell counting {#counting}

Review candidate cells using DAPI and a separate support channel for cell-body and perinuclear evidence. Select the support channel and its marker before starting; both are required. The module retains each cell's identity across its Z observations so that one cell can be reviewed in multiple slices without becoming multiple counts.

Choose the source channels and display colors, then inspect the proposed cells in the counting workbench. Use the maximum-intensity projection for an overview and individual Z slices to check location. You can add, merge, delete, or reclassify objects and undo or redo your edits.

Only objects assigned to **Astrocyte** contribute to the total. **High Likelihood** and **Unrecognized** objects remain available for review and can be added to the count; **Neurons** are shown separately and are excluded. The detailed guide explains these labels and the evidence used to propose them.

Save the numbered overlay, the CSV count table, and the review session. You can reopen a saved session from Jobs &amp; Results. Keep the source image available to return to the original Z slices.

<div class="actions" markdown="0">
<a class="button secondary" href="https://github.com/DingchengWang/Project-Leap-Cell-Counting/blob/main/docs/guide.en.md">Read the counting guide <span aria-hidden="true">↗</span></a>
<a class="text-link" href="https://github.com/DingchengWang/Project-Leap-Cell-Counting">View module source <span aria-hidden="true">↗</span></a>
</div>
</section>

## Planned modules

More analysis modules are planned.

[Check release availability](../downloads/) or [read the application guide](../guide/).
