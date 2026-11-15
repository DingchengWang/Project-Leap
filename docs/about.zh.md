# 关于 Project Leap

[English](about.en.md) · [项目首页](../README_中文.md) · [使用指南](user-guide.zh.md)

## 作者与研究归属

Project Leap 由 **Dingcheng Wang** 开发。作者为**俄亥俄州立大学**神经科学本科生，研究隶属于**医学院神经科学系 Min Zhou 实验室**。

开发过程中，AI 工具辅助代码实现、调试、打包和文档编写。Dingcheng Wang 主导研究需求、分析规则、生物学解释和开发取舍，并负责验收审核。

## 分析中使用的方法与工具

以下上游工具承担不同的分析任务：

- [Cellpose-SAM](https://www.biorxiv.org/content/10.1101/2025.04.28.651001v1.full)：为含 eGFP 的荧光分析分支及计数模块提供分割结果。
- [InstanSeg fluorescence model](https://doi.org/10.1101/2024.09.04.611150)：在成熟样本的 GFAP-only 荧光分析分支中生成 DAPI 细胞核候选。Project Leap 再结合这些候选和 GFAP 结构确定细胞区域。
- [Fiji](https://doi.org/10.1038/nmeth.2019)：用于交互式审核荧光分析区域，并在最终区域内测量原始目标通道投影的强度。

## 源码与许可

源码分别保存在三个仓库中：

- [Project Leap](https://github.com/DingchengWang/Project-Leap)：桌面主程序及共享平台。
- [星形胶质细胞荧光模块](https://github.com/DingchengWang/Project-Leap-2D-Integrated-IHC-fluorescence-analysis)：荧光分析引擎与指南。
- [细胞计数模块](https://github.com/DingchengWang/Project-Leap-Cell-Counting)：计数引擎与指南。

Project Leap 原创代码采用 [Apache License 2.0](../LICENSE)，署名见 [NOTICE](../NOTICE)。第三方软件和模型保留各自的许可证与版权声明。[THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md) 提供保留的许可材料和上游来源链接。
