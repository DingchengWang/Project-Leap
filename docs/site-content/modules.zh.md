Project Leap 当前包含两个模块。主程序负责安装和维护，各模块拥有各自的分析流程和详细指南。

<section class="module-detail" aria-labelledby="fluorescence" markdown="1">
<p class="eyebrow">模块版本 1.1.0</p>

## 星形胶质细胞荧光强度分析 {#fluorescence}

在 Fiji 中审核候选区域后，测量 **Whole（完整细胞）**、**Soma（胞体）** 和 **Processes（突起）** 区域内目标通道的荧光强度。Whole 覆盖完整细胞区域，Soma 与 Processes 则区分胞体和突起。

导入受支持的多通道 TIFF，在界面中指定 DAPI、eGFP/GFAP 和待测通道。Project Leap 自动准备单通道工作图像，原始 TIFF 保留在原位置。随后在 Fiji 中审核区域并确认编辑，程序会继续测量和保存结果。

输出包括三幅叠加图、一份文本分析报告、一份荧光测量工作簿，以及 `IHC_2D_ROI_Archive.zip`。归档保存最终区域及其细胞身份、物理标定、选定 Z 区间和测量对应关系。

### 相比 v1.0.1 脚本包的变化

1.1.0 将分析接入桌面软件，增加多通道 TIFF 直接导入、界面通道指定、模块维护、任务记录、结果预览和最终区域归档。界面提供“自动”、Mature、Neonatal 图像年龄选项；仅使用 GFAP 的分支仍限于 Mature 样本。

既有的 Z 区间选择、分割、阈值、Fiji 审核和荧光测量规则沿用科学版本 1.0.1。本次更新改进分析的准备、执行和保存方式。

<div class="actions" markdown="0">
<a class="button secondary" href="https://github.com/DingchengWang/Project-Leap-2D-Integrated-IHC-fluorescence-analysis/blob/main/docs/guide.zh.md">阅读荧光分析指南 <span aria-hidden="true">↗</span></a>
<a class="text-link" href="https://github.com/DingchengWang/Project-Leap-2D-Integrated-IHC-fluorescence-analysis">查看模块源码 <span aria-hidden="true">↗</span></a>
</div>
</section>

<section class="module-detail" aria-labelledby="counting" markdown="1">
<p class="eyebrow">模块版本 1.0.0</p>

## 细胞计数 {#counting}

根据 DAPI 和另一辅助通道提供的胞体及核周信号审核候选细胞。开始前需指定辅助通道及其标记物，两项均为必选。模块将同一细胞在不同 Z 层的观察关联到同一个细胞身份，便于逐层核对而不重复计数。

指定源通道和显示颜色后，在计数工作台检查候选细胞。最大强度投影用于查看整体分布，各 Z 切片用于核对位置。你可以添加、合并、删除或重新分类对象，并撤销或重做编辑。

总数只包含标记为 **Astrocyte** 的对象。**High Likelihood** 和 **Unrecognized** 对象保留供审核，可由用户加入计数；**Neurons** 单独显示，不计入总数。详细指南说明这些标签及候选对象的证据来源。

保存编号叠加图、CSV 计数表和审核会话后，可在“任务与结果”中重新打开会话。若要继续查看原始 Z 切片，请保留源图像。

<div class="actions" markdown="0">
<a class="button secondary" href="https://github.com/DingchengWang/Project-Leap-Cell-Counting/blob/main/docs/guide.zh.md">阅读细胞计数指南 <span aria-hidden="true">↗</span></a>
<a class="text-link" href="https://github.com/DingchengWang/Project-Leap-Cell-Counting">查看模块源码 <span aria-hidden="true">↗</span></a>
</div>
</section>

## 后续模块

项目计划增加更多分析模块。

[查看发布状态](../downloads/)，或[阅读主程序使用指南](../guide/)。
