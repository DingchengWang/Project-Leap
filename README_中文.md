# Project Leap

[English](README.md)

Project Leap 是面向 Apple Silicon Mac 的本地免疫组织化学（IHC）图像分析软件，将图像导入、分析模块、人工审核和结果管理集中在同一个桌面工作台。原始图像与软件管理的分析数据分开保存。

**版本 1.0.0 · macOS 27 或更高版本 · Apple Silicon（arm64）**

## 下载与开始使用

本次提供共享组件包；荧光分析和细胞计数模块包分别由对应仓库提供。公开 macOS 主程序仍**正在准备中**，DMG 下载尚未启用。主程序下载状态见[项目官网](https://dingchengwang.github.io/Project-Leap/zh/)和 [GitHub Releases](https://github.com/DingchengWang/Project-Leap/releases)。

安装包发布后，从 DMG 安装 **Project Leap.app**，再到软件的**功能与维护**中安装所需模块。软件会下载模块依赖。[平台使用指南](docs/user-guide.zh.md)说明安装、首次启动、结果管理和设置操作。

## 分析模块

| 模块 | 用途 | 文档 |
|---|---|---|
| 星形胶质细胞荧光强度分析 · 1.1.0 | 在经过审核的星形胶质细胞感兴趣区域内测量荧光强度，支持 Fiji 人工审核。 | [使用指南](https://github.com/DingchengWang/Project-Leap-2D-Integrated-IHC-fluorescence-analysis/blob/main/docs/guide.zh.md) · [源码](https://github.com/DingchengWang/Project-Leap-2D-Integrated-IHC-fluorescence-analysis) |
| 细胞计数 · 1.0.0 | 根据 DAPI 和辅助通道证据审核、编辑细胞身份，保存计数及叠加图。 | [使用指南](https://github.com/DingchengWang/Project-Leap-Cell-Counting/blob/main/docs/guide.zh.md) · [源码](https://github.com/DingchengWang/Project-Leap-Cell-Counting) |

## 源码与开发

本仓库保存桌面主程序、共享平台代码、构建工具和网站，两个分析引擎分别保存在模块仓库中。将三个仓库检出到同一父目录后，在主仓根目录运行以下命令，将源码装配到独立构建目录：

```sh
python3 tools/assemble_sources.py --fluorescence ../Project-Leap-2D-Integrated-IHC-fluorescence-analysis --counting ../Project-Leap-Cell-Counting --output ../../build/source
```

装配工具会核对 `tools/build/module-sources.json` 固定的模块提交。依赖和打包步骤见[构建说明](tools/build/README.md)，主程序与引擎之间的请求和结果格式见[接口契约](docs/implementation/CONTRACT.md)。

## 作者与许可

开发者为 **Dingcheng Wang**，**俄亥俄州立大学**神经科学本科生。[关于 Project Leap](docs/about.zh.md)说明作者归属、AI 辅助开发和科学参考资料。

Project Leap 原创代码采用 [Apache License 2.0](LICENSE)。第三方软件与模型保留各自的许可证和声明，见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
