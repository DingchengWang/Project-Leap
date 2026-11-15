# Third-party notices / 第三方组件声明

Original Project Leap code is licensed under [Apache License 2.0](LICENSE), with attribution in [NOTICE](NOTICE). Third-party software, models, and datasets retain their own terms. This index identifies the retained material; the linked license texts and notices contain the terms.

Project Leap 原创代码采用 Apache License 2.0。第三方软件、模型和数据集保留各自的许可；本页索引保留的材料，具体条款见链接中的许可证和声明。

## Application and build tools

| Component | Recorded license | Retained material and source |
|---|---|---|
| CPython 3.14.0 | Python Software Foundation license and included historical notices | [License](tools/build/licenses/Python-3.14.0-LICENSE.txt) · [Python](https://www.python.org/) |
| PySide6 / Shiboken6 6.12.0 and listed Qt for Python distributions | Metadata declares `LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only` | [Distribution metadata](tools/build/licenses/ui-wheels/pyside6-6.12.0.dist-info/METADATA) · [Shiboken metadata](tools/build/licenses/ui-wheels/shiboken6-6.12.0.dist-info/METADATA) · [LGPL 3](tools/build/licenses/LGPL-3.0-only.txt) · [GPL 2](tools/build/licenses/GPL-2.0-only.txt) · [GPL 3](tools/build/licenses/GPL-3.0-only.txt) · [Qt for Python](https://doc.qt.io/qtforpython/) |
| Sparkle 2.10.0 | MIT, with additional bundled-component notices | [Retained license collection](app/leap_desktop/docs/licenses.html), section `native/vendor/Sparkle-2.10.0/LICENSE` · [Sparkle](https://github.com/sparkle-project/Sparkle) |
| PyInstaller 6.22.3 | GPLv2-or-later with the exception stated in its license | [License and exception](tools/build/licenses/ui-wheels/pyinstaller-6.22.3.dist-info/licenses/COPYING.txt) · [PyInstaller](https://github.com/pyinstaller/pyinstaller) |
| altgraph 0.17.5 | MIT | [License](tools/build/licenses/ui-wheels/altgraph-0.17.5.dist-info/LICENSE) · [altgraph](https://github.com/ronaldoussoren/altgraph) |
| macholib 1.16.4 | MIT | [License](tools/build/licenses/ui-wheels/macholib-1.16.4.dist-info/LICENSE) · [macholib](https://github.com/ronaldoussoren/macholib) |
| packaging 26.3 | Apache-2.0 OR BSD-2-Clause | [License selection](tools/build/licenses/ui-wheels/packaging-26.3.dist-info/licenses/LICENSE) · [Apache](tools/build/licenses/ui-wheels/packaging-26.3.dist-info/licenses/LICENSE.APACHE) · [BSD](tools/build/licenses/ui-wheels/packaging-26.3.dist-info/licenses/LICENSE.BSD) · [packaging](https://github.com/pypa/packaging) |

The [UI dependency lock](tools/build/licenses/ui-requirements-lock.txt), [retained distribution metadata](tools/build/licenses/ui-wheels/), and [license-source record](tools/build/licenses/sources.json) record the build inputs. The packaged application exposes its retained collection through **Settings → About → Licenses** / **设置 → 关于 → 许可证**.

## Scientific runtime, models, and Fiji

| Component | Recorded license or notice | Retained material and source |
|---|---|---|
| Cellpose 4.2.1.1 code | BSD-3-Clause | [Retained license collection](app/leap_desktop/docs/licenses.html), section `site-packages/cellpose-4.2.1.1.dist-info/licenses/LICENSE` · [Cellpose source](https://github.com/MouseLand/cellpose) |
| Cellpose-SAM `cpsam_v2` weights | The upstream model page labels the file BSD-3-Clause | [Model file and license label](https://huggingface.co/mouseland/cellpose-sam/blob/main/cpsam_v2) · [Recorded model metadata](tools/build/licenses/models/sources.json). The shared model package retains `ThirdPartyNotices/Cellpose/LICENSE`. |
| InstanSeg `single_channel_nuclei` model 0.1.0 | Apache-2.0; model notice also records source-dataset licenses | [Retained Apache license](https://github.com/DingchengWang/Project-Leap-2D-Integrated-IHC-fluorescence-analysis/blob/main/LICENSES/InstanSeg-Apache-2.0.txt) · [Retained model notice](https://github.com/DingchengWang/Project-Leap-2D-Integrated-IHC-fluorescence-analysis/blob/main/LICENSES/InstanSeg-single_channel_nuclei-README.md) · [Upstream release](https://github.com/instanseg/instanseg/releases/tag/instanseg_models_v0.1.2) |
| Fiji distribution and bundled Java | Multiple component-specific licenses and notices | [Retained license collection](app/leap_desktop/docs/licenses.html), sections under `Fiji/licenses/` and `Fiji/java/` · [Fiji distribution source](https://downloads.imagej.net/fiji/archive/latest/20260718-0417/fiji-latest-macos-arm64-jdk.zip) |
| Shared scientific Python runtime and its dependencies | Multiple component-specific licenses and notices | [Retained license collection](app/leap_desktop/docs/licenses.html), sections under `python/` and `site-packages/`; includes NumPy, SciPy, scikit-image, PyTorch, torchvision, imagecodecs, and other installed dependencies. Original license files also remain in the distributed scientific runtime. |

The [Cellpose upstream README](https://github.com/MouseLand/cellpose) separately states that its models were trained on CC-BY-NC data and that the Cellpose annotated dataset is CC-BY-NC. This training-data statement is recorded separately from the code license and the model-file label above. It does not describe the license of a researcher’s input images or analysis outputs.

[Cellpose 上游 README](https://github.com/MouseLand/cellpose)另行声明其模型使用 CC-BY-NC 数据训练，Cellpose 标注数据集也采用 CC-BY-NC。本页将训练数据声明与上表的软件代码许可、模型文件标签分别记录；该声明不描述研究者输入图像或分析输出的许可。

The InstanSeg model notice identifies its training datasets as `cpdmi_2023` (CC BY 4.0), `dsb_2018` (CC0), and `bsst265` (CC0), with their source links. See that notice and the [model-source record](tools/build/licenses/models/sources.json) for the original attribution.
