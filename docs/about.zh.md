# 关于 Project Leap

[English](about.en.md) · [项目首页](../README_中文.md) · [使用指南](user-guide.zh.md)

Project Leap 是用于免疫组织化学图像分析的本地科研平台。桌面主程序提供**星形胶质细胞荧光强度分析**和**细胞计数**两个模块。平台管理安装、图像导入、任务记录、结果和维护，各模块提供自己的分析流程和审核工具。

## 作者与研究归属

Project Leap 由 **Dingcheng Wang** 开发，作者为**俄亥俄州立大学**神经科学本科生，研究隶属于**医学院神经科学系 Min Zhou 实验室**。软件“关于”页面记录了上述归属，项目 [NOTICE](../NOTICE) 保留作者署名。

## 开发方式与科学责任

AI 工具辅助完成代码实现、调试、打包和文档编写。Dingcheng Wang 主导研究需求、生物学解释、分析规则、取舍及验收审核。

使用 Project Leap 的研究者需要判断分析是否适合自己的实验，审核软件提出的感兴趣区域或细胞身份，并解释保存的测量结果。各模块指南分别说明输入要求、审核步骤和科学适用范围。

## 开源代码与第三方组件

Project Leap 原创代码采用 [Apache License 2.0](../LICENSE)，署名见 [NOTICE](../NOTICE)。第三方软件和模型保留各自的许可证与版权声明。[THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md) 提供保留的许可材料和上游来源链接。

源码分为三个仓库：

- [Project Leap](https://github.com/DingchengWang/Project-Leap)：桌面主程序及共享平台。
- [星形胶质细胞荧光模块](https://github.com/DingchengWang/Project-Leap-2D-Integrated-IHC-fluorescence-analysis)：荧光分析引擎与指南。
- [细胞计数模块](https://github.com/DingchengWang/Project-Leap-Cell-Counting)：计数引擎与指南。

## 科学参考资料

软件的**来源与参考资料**页面列出以下上游方法和工具：

- [Cellpose-SAM](https://www.biorxiv.org/content/10.1101/2025.04.28.651001v1.full)。
- [InstanSeg fluorescence model](https://doi.org/10.1101/2024.09.04.611150)。
- [Fiji](https://doi.org/10.1038/nmeth.2019)。

同一组链接也保留在软件的[参考资料资源文件](../app/leap_desktop/docs/references.html)中。
