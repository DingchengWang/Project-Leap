<section class="hero" markdown="1">
<div class="hero-copy" markdown="1">

# 星形胶质细胞荧光图像分析平台

Project Leap是一款面向星形胶质细胞多通道免疫组化荧光图像（IHC）的分析软件。该软件结合了当下先进的开源模型和原创设计，使其拥有独特的星形胶质细胞框选机制，能在复杂的图像背景中描绘出精细的星形胶质细胞ROI（region of interest）。在此基础上，该软件的模块化设计为后续开发者加入各类分析功能提供了很好的灵活度。当前软件内已经包括荧光强度测量和细胞空间定位计数两项功能，其中前者可以连接Fiji Image J进行自动测量，未来还将有更多功能和联动软件接口加入。

<div class="actions" markdown="0">
<a class="button primary" href="downloads/">下载 Mac 版 <span aria-hidden="true">↓</span></a>
<a class="text-link" href="guide/">阅读使用指南 <span aria-hidden="true">→</span></a>
</div>
<p class="release-line">Apple Silicon · macOS 27 或更新版本</p>
</div>

<div class="hero-symbol" markdown="0">
<img src="../assets/icon.svg" alt="Project Leap 应用图标" width="240" height="240">
<p>Project Leap</p>
</div>
</section>

<!-- leap-media:start home-workspace -->
<figure class="media-figure">
<a class="media-link" href="../assets/images/app-workspace-zh.png"><img src="../assets/images/app-workspace-zh.png" alt="分析工作台显示图像导入区，左侧列出可用模块。" width="3808" height="1776" loading="eager" decoding="async"></a>
<figcaption>分析工作台显示图像导入区，左侧列出可用模块。</figcaption>
</figure>
<!-- leap-media:end home-workspace -->

<div class="section-heading" markdown="1">

## 按研究需求选择模块

</div>

<div class="module-list" markdown="1">
<section class="module-row" aria-labelledby="fluorescence-home" markdown="1">
<div class="module-number" aria-hidden="true" markdown="0">01</div>
<div markdown="1">
<h3 id="fluorescence-home">星形胶质细胞荧光强度分析</h3>
<p>该模块基于软件独特的细胞框选机制，整合定制化Fiji Image J流程，使其可以对星形胶质细胞整体、胞体及突起区域的多项目标荧光强度指标进行自动或半自动测量。</p>
<a class="text-link" href="modules/#fluorescence">了解荧光分析 <span aria-hidden="true">→</span></a>
</div>
</section>
<section class="module-row" aria-labelledby="counting-home" markdown="1">
<div class="module-number" aria-hidden="true" markdown="0">02</div>
<div markdown="1">
<h3 id="counting-home">细胞计数</h3>
<p>该模块通过DAPI标志物外加一项自定义的辅助荧光通道识别细胞。同时，该模块在多层图像的3D空间内对细胞进行定位，解决了传统2D图像平面内因细胞重叠不清而造成的计数问题。</p>
<a class="text-link" href="modules/#counting">了解细胞计数 <span aria-hidden="true">→</span></a>
</div>
</section>
</div>

<div class="closing-row" markdown="1">
<div markdown="1">

## 如何使用

当前Project Leap官网提供软件主体的下载和操作说明，内部分析功能模块需要在软件内单独下载安装。软件可实现全自动分析或包含人工检查的半自动分析，具体模块的操作说明详见GitHub仓库。软件内所有科学计算均在本地进行，但分析速度可能受到图像大小和硬件性能的影响。

</div>
<a class="button secondary" href="guide/">查看使用方法 <span aria-hidden="true">→</span></a>
</div>
