# Project Leap 接口与数据约定

本文记录主程序、科学模块和已保存任务之间的数据约定。计数对象的完整来源定义见[计数模块 CELL_IDENTITY.md](https://github.com/DingchengWang/Project-Leap-Cell-Counting/blob/main/docs/prototype-d/CELL_IDENTITY.md)；平台操作见[使用指南](../user-guide.zh.md)。

## 主程序与模块

Project Leap v1.0.0 面向 Apple Silicon，最低系统为 macOS 27。主程序管理任务、显示与审核，不载入 torch。生产入口接受数据和配置位置参数；荧光模块的人工 Fiji 审核不可通过请求参数跳过。

模块描述符使用 schema 4，通过 `science_runtime` 指定科学运行环境的精确版本。`host_api` 和 `platform` 必须与宿主匹配，宿主构建号必须不低于描述符的 `minimum_host_build`。当前宿主 API 为 1，平台为 `macos-arm64`；各模块的最低构建要求以实际发布的描述符和组件目录为准。`cellpose-model` 独立保存固定模型权重，Fiji 也作为独立依赖发布。

应用保留 bundle ID `org.projectleap.prototype.d`、默认数据目录 `Project Leap Prototype D` 和配置目录 `Project Leap Prototype D Settings`，以继续使用这些目录中的已有任务。应用不会自动迁移或合并其他安装的数据。

导航先显示荧光，再显示计数及后续模块。模块说明入口读取模块目录中的 `github_url`，仅在地址已配置时显示，并在浏览器中打开该地址。参考文献与许可证页面以本地 HTML 随应用冻结和签名。

荧光年龄选项 `auto` 映射为 `null`，`mature` 和 `neonatal` 使用各自的年龄分支。科学源码基线及构建验证方法见[构建说明](../../tools/build/README.md)。

## 计数输入与科学结果

计数请求包含图像、图像组、时间点、DAPI 和通道映射字段。`support_channel` 必须与 DAPI 通道不同，`support_marker` 声明辅助通道标记物，`display_channels` 至少选择一个通道，默认包含 DAPI 和辅助通道。通道索引从 0 开始，请求不含核直径字段。ALDH1L1 的默认显示颜色为 `#00FF00`；用户已保存或显式指定的颜色优先。

DAPI 使用局部 Z 事件范围的原生 XY 最大投影，作为固定 `cpsam_v2` 的二维模型输入；引擎随后从原体积恢复三维核身份。所选弥散性辅助通道独立进行三维信号聚集，生成胞体观察，不调用 Cellpose。标记物名称不控制隐藏算法分支。定位统计用于安排原像素计算；核和胞体分别保留真实掩膜、原图 XYZ 标定与逐层交点。真正单层输入按单层处理，未知 Z 间距保持未知。

uint8/uint16 DAPI 使用全源精确直方图的 P1/P99 统一归一化，并保存范围和直方图。显示图像和定位摘要不作为核或胞体测量图像。核周信号接口 `measure_support(nucleus_mask, support_volume, spacing_xyz, marker, config=None)` 返回判断和指标；`support_discovery.py` 负责独立胞体发现，引擎整合原图区域与全局身份。物理邻域及开发阈值显式记录。现有真实图像验收材料为 Biocytin；具体候选上下文与关联判据仍需真实图像验证。

`analysis.json` 使用 schema 2，`identity_model` 为 `multi_observation_cell_v1`。主要对象如下：

| 对象或字段 | 含义与约束 |
|---|---|
| `label_artifacts` | 分别登记 `dapi_nuclei`（`labels.npy`，`kind=nucleus`）和 `support_bodies`（`body-labels.npy`，`kind=body`），保存路径、ZYX 轴、原点和原体积形状。 |
| `artifact_id` / `label_id` | `label_id` 只在所属标签文件内有意义；两个文件中的相同编号不能据此认定为同一对象。 |
| `point.id` / `point.cell_id` | 两者相等，表示全局细胞身份。 |
| `source_observations` | 保存所属核或胞体观察、各自标签文件和编号、判断、指标及真实层面信息。 |
| `plane_anchors` / `plane_anchor_sources` | 保存逐层位置及其来源；`z` 仅表示代表层。 |
| `association` | 保存明确一对一、未匹配或竞争归属的证据。 |
| `source_identity` | 保存实际读取通道的原始像素摘要，用于原图异常后的重新定位；不参与分割、分类或荧光测量。 |

引擎依据原生 Z 和核在胞体中的包含关系等证据关联核与胞体，不用跨类型掩膜 IoU 判断同一细胞。接受的核来源、独立胞体或已配对细胞均计一次；需要人工判断的候选保留为 `review`，供审核时加入计数。

## 审核、计数与保存

编辑器的 `points` 保存全部对象，`visiblePoints` 按实际层面和手工删除标记筛选显示对象。后端使用 `accepted`、`review`、`excluded` 三种状态，界面将其显示为四类：

| 界面类别 | 后端依据 | 标记与普通点击行为 |
|---|---|---|
| Astrocyte | `accepted` | 绿色编号圈，计入总数。 |
| High Likelihood | `review` 且 `review_hint=likely_astrocyte` | 琥珀菱形，普通点击将原对象加入 Astrocyte。 |
| Unrecognized | 其余 `review` | 黄色轻点，普通点击只选择；“加入计数”执行人工修正。 |
| Neurons | `excluded` | 紫色小方形，普通点击只选择；“加入计数”执行人工修正。 |

四类默认均显示。这些名称呈现已有状态与提示，不新增 `cell_type` 确认字段。只有引擎显式给出 `review_hint: "likely_astrocyte"`，界面才显示“高Astrocyte可能性”：该对象的 DAPI/Support 证据充分，仅因人工搜索区域的 XY 边界而待定，且没有其他真实 ROI/Z/model/overlap 身份问题或竞争关联。提示本身不改变计数。

Command 点击仅选择对象，可随后批量“加入计数”。手动删除写入 `deleted: true`，保留原 `status`，将对象隐藏并排除出计数；撤销可恢复。High Likelihood 和 Unrecognized 尚有对象时仍可保存。

初始和当前总数均只计算未删除的 Astrocyte。当前对象连续编号为 1…N，MIP、各 Z 层、PNG 和 CSV 使用同一映射，Z 层筛选不重新编号；内部 `cell_id` 保持稳定。左侧显示 `Total Astrocyte: N`，右侧依次显示四类数量。High Likelihood 使用 `likelyUnrecognizedCount`，Unrecognized 使用 `ordinaryUnrecognizedCount`，两者互不重叠；`pendingCount` 是后台 `review` 总数，不作为界面的父分类显示。

确认后，叠加图和 CSV 只导出未删除的 Astrocyte，并保存其 `status`/`count`、`source_observations`、`plane_anchor_sources`、`association` 和完整 `point_metadata`。会话保存全部状态和手工删除对象、实际的 `pending_count`、`excluded_count`、`deleted_count`，以及 `identity_model`、`label_artifacts` 和相对会话的标签文件路径。既有 schema 1 结果和正常人工编辑仍可读取和使用。

审核字号 `label_size` 使用逻辑屏幕像素。PNG 字号按输出最长边的 1/128 计算，例如 4096 像素图使用 32 像素；会话另存 `export_label_size`，PNG 的 `label_size` 元数据记录导出字号。输出保留原图尺寸，不重采样。

结果保存成功后，计数工作台清空当前审核会话，恢复到新图像输入状态；已保存任务可从“任务与结果”重开。保存失败时保留当前会话。切换工作台也会保留尚未完成保存的输入和审核状态。

## 切片服务与显示

持久切片服务使用 `--slice-server <analysis.json>`，通过标准输入输出交换逐行 JSON：

- 请求字段为 `{id,z,channels,max_edge,output}`。
- 成功响应包含 `{id,z,path,plane_anchors,width,height,native_width,native_height,max_edge,quality,sampling}`；失败响应返回错误。

服务读取已保存的显示范围和预选通道，不导入 torch。单次 `--slice` 接口继续供现有调用者和测试使用。

Z 层使用各通道的 `slice_display_range`，其中 DAPI 使用源图统一 P1/P99，所有 Z 层保持相同范围；MIP 使用各通道的 `display_range`。既有文件缺少切片范围时，读取其已保存的显示范围。这些显示变换不回流模型推理。

拖动 Z 滑块时，若原图最长边大于 1536 且 `viewZoom <= 1`，显示代理将最长边限制为 1536，并按原图像素中心最近邻取样。拖动结束后优先复用匹配的原生缓存，否则约 180 ms 后请求原分辨率；`viewZoom > 1` 时也恢复原生帧。1024 仅用于内部配置和测试，没有对应界面设置项。代理仅影响显示，标记始终使用原图 XYZ 坐标，分析、labels、计数和导出保持原数据。画面不显示快速预览徽章或编号引导线。

后台解码候选帧时，前台保留已提交的完整旧帧；过期或失败候选不能改变已显示层号或标记。临时切片 PNG 使用无损压缩级别 1，正式导出保持原有保存参数。

## 计数原生统计库

计数组件包含 `engine/leap_cell_counting/libLeapCountingStats.dylib`，固定 ABI 1，导出 `leap_counting_stats_abi_version`、`leap_counting_stats_u8` 和 `leap_counting_stats_u16`。构建器从冻结的 C 源码编译并签名该库，运行时直接加载；缺库或 ABI 不匹配时报错。二进制单独记录为编译产物，不进入荧光科学源码清单。

## 运行细节与任务记录

`activeRunDetails` 提供当前任务的运行细节，`jobRunDetails` 按需载入历史任务的细节；两者按任务和模块身份隔离。实时流水增量读取 `console.log`、`events.jsonl`，并跟踪计数的 `model-progress.jsonl`、`native-recovery-progress.jsonl` 和 `body-progress.jsonl`。后三份文件按偏移处理新增完整记录，每份每轮最多读取 64 KiB，未完成单行的缓冲上限为 256 KiB；实时日志显示末 120,000 字符。参数和耗时变化追加到流水，不重写顶部参数头。并行阶段及同批窗口的共享耗时单独标注，不相加为总耗时；荧光已用时包含人工审核等待。

历史视图用 `summarySections` 输出“任务与软件”“样本与通道”“分析步骤与参数”“审核与结果”“耗时与重要事件”五类摘要，在任务详情内保留二级折叠入口，`resources` 和 `logText` 均为空。摘要按任务身份读取已有请求、日志与事件、导入结果、运行来源记录、分析报告、诊断和确认会话，`job.json` 的 `run_details` 保存分类摘要。读取在后台进行：每份文本最多读取 2 MiB 的首尾内容，JSON 上限为 16 MiB。超过上限时标记信息不完整，原始文件不重写，缺失字段不补造。

资源采样线程只在当前任务导入或计算且运行细节卡片可见时运行；进入人工审核、切换模块或隐藏卡片后停止。各项读数的定义如下：

| 读数 | 来源与刷新方式 |
|---|---|
| CPU 总量和每逻辑核心 | 约每秒计算系统计数差；按 `logical-cpu-id` 匹配核心类型和采样索引，能效核心显示绿色。 |
| GPU | 约每两秒读取整个设备的利用率，分组数来自驱动的 `mGPU` 分组；不估算逐 GPU 核心利用率。 |
| 温度 | 约每两秒读取已识别的 Apple M3、M3 Pro、M3 Max 传感器组，对当次有限且大于零的有效读数分别求 CPU/GPU 算术均值，不按启用核心数平均。无有效读数或硬件未识别时为空。 |
| 物理内存与 SWAP | 约每两秒读取整机指标。 |

资源读数仅用于实时卡片，历史视图只展示分类摘要。结果缩略图单行横排，超宽时可横向滚动，保持图像纵横比并支持点击打开原图。运行流水、历史摘要和资源采样只观察已有数据，不参与分割、分类、测量或审核结果计算。
