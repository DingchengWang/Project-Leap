两个模块分别用于测量星形胶质细胞区域内的目标荧光，以及审核哪些细胞应纳入计数。它们均使用多通道 TIFF 图像，并提供候选结果的图像审核工具。

<section class="module-detail" aria-labelledby="fluorescence" markdown="1">

## 星形胶质细胞荧光强度分析 {#fluorescence}

模块分别测量 **Whole Cell（完整细胞区域）**、**Soma（胞体）**和 **Processes（突起）**内的荧光强度。输入需包含 **DAPI**、至少一种结构标记物（**eGFP 和／或 GFAP**），以及 **KCNN1、KCNN2、KCNN3、KCNJ10 中恰好一个待测目标通道**。仅使用 GFAP 的分析分支限于 **Mature（成熟）**样本。

软件依据 DAPI 和所选结构通道确定分析深度与细胞区域，再测量这些区域内的目标通道荧光。

测量基于选定 Z 区间的二维最大强度投影。在 Fiji 中，研究者可以对照图像检查边界，并在测量前联动编辑完整细胞、胞体和突起区域。测量值保留目标通道的原始强度尺度和背景成分。

测量工作簿记录每个细胞及各区域的测量值，编号叠加图和 ROI 归档保留审核后的边界。

<div class="actions" markdown="0">
<a class="button secondary" href="https://github.com/DingchengWang/Project-Leap-2D-Integrated-IHC-fluorescence-analysis/blob/main/docs/guide.zh.md">阅读荧光分析指南 <span aria-hidden="true">↗</span></a>
<a class="text-link" href="https://github.com/DingchengWang/Project-Leap-2D-Integrated-IHC-fluorescence-analysis">查看模块源码 <span aria-hidden="true">↗</span></a>
</div>
</section>

<section class="module-detail" aria-labelledby="counting" markdown="1">

## 细胞计数 {#counting}

模块结合 **DAPI** 和**另一个辅助通道**生成待审核的星形胶质细胞计数对象。辅助通道提供胞体与核周信号，需同时指定其源通道和标记物。

同一细胞在不同 Z 层的观察保存在同一条细胞记录中，便于在多个深度核对其身份。计数工作台提供最大强度投影和原始 Z 切片：前者用于查看整体分布，后者用于检查各层图像。研究者可以补加细胞、合并重复记录、删除对象，或调整哪些记录纳入计数。

界面总数包含已接受、未删除且标为 **Astrocyte** 的记录。其他对象保留供审核，接受后才加入总数。

确认后的结果包含编号叠加图、CSV 计数表和可编辑会话。会话可从“任务与结果”重新打开；若需再次查看原始 Z 切片，应保留源 TIFF。

<div class="actions" markdown="0">
<a class="button secondary" href="https://github.com/DingchengWang/Project-Leap-Cell-Counting/blob/main/docs/guide.zh.md">阅读细胞计数指南 <span aria-hidden="true">↗</span></a>
<a class="text-link" href="https://github.com/DingchengWang/Project-Leap-Cell-Counting">查看模块源码 <span aria-hidden="true">↗</span></a>
</div>
</section>
