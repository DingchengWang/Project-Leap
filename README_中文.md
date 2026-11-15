# Project Leap

[English](README.md) · [项目网站](https://dingchengwang.github.io/Project-Leap/zh/)

Project Leap 是一款在 Mac 上分析多通道显微荧光图像的软件，用于星形胶质细胞荧光测量和细胞计数。程序自动生成候选细胞区域与计数对象，研究者可对照图像审核和调整，决定最终测量的区域及纳入计数的细胞。

## 安装与开始使用

[下载 Project Leap Mac 版](https://github.com/DingchengWang/Project-Leap/releases/download/v1.0.0/Project-Leap-1.0.0-macOS-arm64.dmg)。软件需要 **Apple Silicon Mac 和 macOS 27 或更新版本**。将 DMG 中的 **Project Leap.app** 复制到“应用程序”，再到软件的**功能与维护**中安装所需模块；程序会一并下载依赖组件。

[使用指南](docs/user-guide.zh.md)说明首次启动、图像导入、任务记录、结果管理和设置。当前版本与[旧版独立脚本包](https://dingchengwang.github.io/Project-Leap/zh/downloads/#legacy)统一列在[下载页面](https://dingchengwang.github.io/Project-Leap/zh/downloads/)，主程序的版本变化见 [GitHub Releases](https://github.com/DingchengWang/Project-Leap/releases)。

## 选择分析模块

| 模块 | 输入与结果 | 文档 |
|---|---|---|
| 星形胶质细胞荧光强度分析 | 输入包含 DAPI、eGFP 和／或 GFAP，以及 KCNN1、KCNN2、KCNN3、KCNJ10 中的一个目标通道。分别测量完整细胞、胞体和突起区域，支持 Fiji 审核并保留最终区域归档。GFAP-only 分支仅适用于成熟样本。 | [使用指南](https://github.com/DingchengWang/Project-Leap-2D-Integrated-IHC-fluorescence-analysis/blob/main/docs/guide.zh.md) · [源码](https://github.com/DingchengWang/Project-Leap-2D-Integrated-IHC-fluorescence-analysis) |
| 细胞计数 | 输入包含 DAPI 和另一个辅助通道。同一细胞在不同 Z 层的观察关联到同一记录，供逐层审核；已接受的 Astrocyte 记录导出为计数表与编号叠加图，并保留可编辑会话。 | [使用指南](https://github.com/DingchengWang/Project-Leap-Cell-Counting/blob/main/docs/guide.zh.md) · [源码](https://github.com/DingchengWang/Project-Leap-Cell-Counting) |

图像在本机完成分析。软件另行保存任务记录和输出文件，原始 TIFF 保留在原位置。

## 源码与开发

本仓库保存桌面主程序、共享平台代码、构建工具和网站，两个分析引擎分别保存在模块仓库中。将三个仓库检出到同一父目录后，在主仓根目录运行以下命令，将源码装配到独立构建目录：

```sh
python3 tools/assemble_sources.py --fluorescence ../Project-Leap-2D-Integrated-IHC-fluorescence-analysis --counting ../Project-Leap-Cell-Counting --output ../../build/source
```

装配工具会核对 `tools/build/module-sources.json` 固定的模块提交。依赖和打包步骤见[构建说明](tools/build/README.md)，主程序与引擎之间的请求和结果格式见[接口契约](docs/implementation/CONTRACT.md)。

## 作者与许可

开发者为 **Dingcheng Wang**，**俄亥俄州立大学**神经科学本科生。[关于 Project Leap](docs/about.zh.md)说明研究归属、AI 辅助开发，以及分析中使用的上游方法。

Project Leap 原创代码采用 [Apache License 2.0](LICENSE)。第三方软件与模型保留各自的许可证和声明，见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
