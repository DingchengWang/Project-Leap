当前Project Leap v1.0.0内有两个初始模块，分别用于测量星形胶质细胞各区域内的目标荧光以及细胞空间定位计数。以下是各模块的简要介绍，具体详情查看对应GitHub仓库。

<section class="module-detail" aria-labelledby="fluorescence" markdown="1">

## 星形胶质细胞荧光强度分析 {#fluorescence}

该模块将分别测量 Whole Cell（完整细胞区域）、Soma（胞体）和 Processes（突起）内的荧光强度。输入需包含 DAPI、至少一种结构标记物（eGFP 和／或 GFAP），以及 KCNN1、KCNN2、KCNN3、KCNJ10 中的一个待测目标通道。若仅使用 GFAP 进行分析，软件只限于使用 Mature（成熟）样本。

软件依据 DAPI 和结构标记物确定图像深度与细胞区域，再测量这些区域内的目标通道荧光。测量基于选定 Z 区间的二维最大强度投影。在测量前，研究者可以选择联动Fiji Image J对照多组图像，编辑完整细胞、胞体和突起的区域。测量值为目标通道的原始强度和背景成分。

输出的测量表将记录每个细胞及各区域的多项荧光指标。同时，还将输出荧光叠加图和ROI坐标文件（可二次导入Fiji），供后续可视化检查。

<div class="actions" markdown="0">
<a class="button secondary" href="https://github.com/DingchengWang/Project-Leap-2D-Integrated-IHC-fluorescence-analysis/blob/main/docs/guide.zh.md">阅读荧光分析指南 <span aria-hidden="true">↗</span></a>
<a class="text-link" href="https://github.com/DingchengWang/Project-Leap-2D-Integrated-IHC-fluorescence-analysis">查看模块源码 <span aria-hidden="true">↗</span></a>
</div>
</section>

<section class="module-detail" aria-labelledby="counting" markdown="1">

## 细胞计数 {#counting}

该模块结合 DAPI 和另一项自定义辅助通道对星形胶质细胞进行计数。其中，软件使用DAPI标定细胞核，进而标定细胞。辅助通道则通过胞体与核周信号模式，为细胞计数的准确性提供更多保障，并为人工筛选提供辅助信息。用户可以在预览视窗内检查不同 Z 层的细胞并进行补加细胞、合并重复、删除或调整已有标记等一系列操作。

输出的结果包含带有细胞编号的荧光叠加图、CSV 计数表等。同时，用户还可以在软件内再次编辑历史分析（若需再次查看原始 Z 层，应保留源 TIFF图像）。

<div class="actions" markdown="0">
<a class="button secondary" href="https://github.com/DingchengWang/Project-Leap-Cell-Counting/blob/main/docs/guide.zh.md">阅读细胞计数指南 <span aria-hidden="true">↗</span></a>
<a class="text-link" href="https://github.com/DingchengWang/Project-Leap-Cell-Counting">查看模块源码 <span aria-hidden="true">↗</span></a>
</div>
</section>
