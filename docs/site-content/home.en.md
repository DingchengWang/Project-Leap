<section class="hero" markdown="1">
<div class="hero-copy" markdown="1">

# A platform for astrocyte fluorescence image analysis

Project Leap analyzes astrocytes in multichannel immunohistochemistry (IHC) fluorescence images. It combines advanced open-source models with original designs to delineate detailed astrocyte regions of interest (ROIs) against complex image backgrounds. This unique ROI delineation method provides a foundation for a modular design that gives developers the flexibility to add a wide range of analysis features. The software currently has two modules, one for fluorescence intensity measurement and one for spatial cell localization and counting. The fluorescence module integrates with Fiji (ImageJ) for automated measurements. More features and interfaces for integration with other software will be added in the future.

<div class="actions" markdown="0">
<a class="button primary" href="downloads/">Download for Mac <span aria-hidden="true">↓</span></a>
<a class="text-link" href="guide/">Read the user guide <span aria-hidden="true">→</span></a>
</div>
<p class="release-line">Apple Silicon · macOS 27 or later</p>
</div>

<div class="hero-symbol" markdown="0">
<img src="../assets/icon.svg" alt="Project Leap app icon" width="240" height="240">
<p>Project Leap</p>
</div>
</section>

<div class="section-heading" markdown="1">

## Choose modules for your research

</div>

<div class="module-list" markdown="1">
<section class="module-row" aria-labelledby="fluorescence-home" markdown="1">
<div class="module-number" aria-hidden="true" markdown="0">01</div>
<div markdown="1">
<h3 id="fluorescence-home">Astrocyte Fluorescence Intensity Measurement</h3>
<p>Building on Project Leap's unique approach to ROI delineation, this module integrates a customized Fiji (ImageJ) workflow to automatically or semi-automatically measure multiple fluorescence intensity metrics from the target channel within whole-cell, soma, and process regions.</p>
<a class="text-link" href="modules/#fluorescence">Explore fluorescence analysis <span aria-hidden="true">→</span></a>
</div>
</section>
<section class="module-row" aria-labelledby="counting-home" markdown="1">
<div class="module-number" aria-hidden="true" markdown="0">02</div>
<div markdown="1">
<h3 id="counting-home">Cell Counting</h3>
<p>This module identifies cells using DAPI and a user-defined auxiliary fluorescence channel. It also locates cells in the 3D volume of an image stack, resolving counting problems caused by cells that overlap and are difficult to distinguish in conventional 2D images.</p>
<a class="text-link" href="modules/#counting">Explore cell counting <span aria-hidden="true">→</span></a>
</div>
</section>
</div>

<div class="closing-row" markdown="1">
<div markdown="1">

## How to use Project Leap

The Project Leap website currently provides downloads and operating instructions for the main application. Analysis modules need to be downloaded and installed separately within the application. Analyses can run fully automatically or semi-automatically with manual review; detailed instructions for each module are available in its GitHub repository. All scientific computations run locally, though analysis speed may vary with image size and hardware performance.

</div>
<a class="button secondary" href="guide/">See how to use Project Leap <span aria-hidden="true">→</span></a>
</div>
