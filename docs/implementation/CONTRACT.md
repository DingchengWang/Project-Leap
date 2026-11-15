# Project Leap 当前接口

细胞来源协议见[计数模块 CELL_IDENTITY.md](https://github.com/DingchengWang/Project-Leap-Cell-Counting/blob/main/docs/prototype-d/CELL_IDENTITY.md)。正式应用版本为 1.0.0，首发构建号为 1；公开构建号使用从 1 开始的全局递增正整数，软件版本变化时继续递增。计数包 1.0.0，荧光包 1.1.0（科学规则版本分别为 1.0.0、1.0.1），最低 macOS 27。计数科学流程保留已接受的 IMG005-v4 自动判断。

计数请求保留图像、图像组、时间点、DAPI 及通道映射字段，使用不同于 DAPI 的 `support_channel`、`support_marker` 和 `display_channels`。通道索引从 0 开始，至少选择一个显示通道，默认显示 DAPI 和辅助通道；不含核直径字段。主程序管理任务、显示与审核，不载入 torch。

ALDH1L1 的默认显示颜色为 `#00FF00`；该默认值只影响显示通道配色，用户已保存或显式指定的颜色继续保留。

DAPI 使用局部 Z 事件范围的原生 XY 最大投影作为固定 `cpsam_v2` 的二维模型证据，再从原体积恢复三维核身份。所选弥散性辅助通道独立进行三维信号聚集，生成胞体观察，不调用 Cellpose。标记物名称声明通道身份，不控制隐藏算法分支。定位统计只安排原像素计算；核和胞体分别保留真实掩膜、原图 XYZ 标定与逐层交点。真正单层输入显式按单层处理，未知 Z 间距不填造。

`analysis.json` 使用 schema 2 和 `identity_model: "multi_observation_cell_v1"`。`label_artifacts` 分别登记 `dapi_nuclei`（`labels.npy`，kind=nucleus）与 `support_bodies`（`body-labels.npy`，kind=body），保存文件路径、ZYX 轴、原点和原体积形状。数字 `label_id` 仅在对应 `artifact_id` 中有意义，不能用两个文件中相同数字推断同一对象。

`point.id == point.cell_id` 是全局细胞身份，`source_observations` 保存所属核/胞体观察、各自标签文件与标签编号、判断、指标和真实层面信息；`plane_anchors` 与 `plane_anchor_sources` 保留每层位置及其来源，`z` 仅为代表层。`association` 保存明确一对一、未匹配或竞争归属证据。核与胞体关联依据原生 Z 和核在胞体中的包含关系等证据，不以跨类型掩膜 IoU 判定相同细胞。接受的核来源、独立胞体或已配对细胞均计一次；需要人工判断的候选保留 review，作为待添加对象。

编辑器 `points` 保存全部对象，`visiblePoints` 仅按实际层面及手工删除标记筛选。界面横向显示 Astrocyte / High Likelihood / Unrecognized / Neurons 四项，各自默认显示。Astrocyte 对应 accepted，使用绿色编号圈；High Likelihood 对应带 `review_hint=likely_astrocyte` 的 review，使用琥珀菱形；Unrecognized 对应其余 review，使用黄色轻点；Neurons 对应 excluded，使用紫色小方形。后端仍使用 accepted/review/excluded 三状态。初始和当前总数只计未删除的 Astrocyte。

仅 High Likelihood 普通点击将原对象加入 Astrocyte 计数；Unrecognized 和 Neurons 普通点击只选择，显式“加入计数”可作人工修正。Command 仅选择供批量加入。High Likelihood 和 Unrecognized 未清零也可保存。手动删除仅写 `deleted: true`，保留原 status，隐藏对象且不计数，不把删除变成 excluded 分类；撤销可恢复。四项名称仅呈现现有状态和提示，不新增 `cell_type` 确认字段。

仅 review 且有引擎显式 `review_hint: "likely_astrocyte"` 的对象显示“高Astrocyte可能性”。条件为 DAPI/Support 证据充分、仅人工搜索区域的 XY 边界使其待定、没有其他真实 ROI/Z/model/overlap 身份问题或竞争关联。该提示不自动更改 accepted 总数，界面不自行推断。

当前未删除的 Astrocyte 连续编号为 1…N；MIP、各 Z 层、PNG 和 CSV 使用同一对象到编号的映射，Z 层筛选不重新编号。内部 `cell_id` 保持稳定。画面左侧显示 `Total Astrocyte: N`，右侧按 Astrocyte / High Likelihood / Unrecognized / Neurons 横向排列各自数量。High Likelihood 使用 `likelyUnrecognizedCount`，Unrecognized 使用 `ordinaryUnrecognizedCount`，两项互不重叠；`pendingCount` 仍是后台 review 总数，不显示为分类父项。

确认后，叠加图和 CSV 仅导出未删除的 Astrocyte，保存其 status/count、`source_observations`、`plane_anchor_sources`、`association` 和完整 `point_metadata`。会话保留全部状态和手工删除对象，真实 `pending_count`、`excluded_count`、`deleted_count`，以及 `identity_model`、`label_artifacts` 与相对会话的标签文件路径。稳定身份及来源保留；`label_size` 为审核的逻辑屏幕像素，PNG 独立字号为输出最长边的 1/128（4096 像素图为 32 像素），会话另存 `export_label_size`，PNG 的 `label_size` 元数据记录导出字号。输出保留原图尺寸，不重采样。正式 C schema 1 历史结果和正常人工编辑继续可用。

仅当结果保存成功时，计数工作台清空当前审核会话并恢复为新图像输入状态；已保存任务仍可从“任务与结果”重开。保存失败保留当前会话。切换工作台继续保留尚未完成保存的输入与审核状态。

持久切片服务为 `--slice-server <analysis.json>`，标准输入输出使用逐行 JSON。请求 `{id,z,channels,max_edge,output}`，响应包含 `{id,z,path,plane_anchors,width,height,native_width,native_height,max_edge,quality,sampling}` 或错误。服务使用保存的显示范围和预选通道，不导入 torch。既有 `--slice` 供仍实际使用的调用者和历史测试。

核周信号接口 `measure_support(nucleus_mask, support_volume, spacing_xyz, marker, config=None)` 保留，返回判断和指标。独立胞体发现位于 `support_discovery.py`；原图区域和全局身份由引擎整合。物理邻域及开发阈值显式记录，当前真实图像验收材料为 Biocytin，不将其扩展为其他标记物已验收的结论。

uint8/uint16 DAPI 使用全源精确直方图的 P1/P99 统一归一化；范围及直方图保存为证据。显示和定位摘要不回流为核或胞体测量图像。具体候选上下文与关联判据仍需真实图像验证。

荧光 auto 映射为 null，mature/neonatal 延续原年龄分支。固定 `cellpose-model` 保持唯一权重拥有者；荧光 95 文件基线及两处适配审核继续由构建验证器保护。模块目录使用 `github_url` 提供外部说明入口；仅配置了地址的模块显示入口，本地参考文献与许可证页面随应用冻结和签名。

应用、交付文件夹、磁盘卷名和界面品牌使用 Project Leap；公开主程序镜像文件名为 `Project-Leap-1.0.0-macOS-arm64.dmg`；可选完整离线镜像为 `Project Leap V1.0.0.dmg`。bundle ID 保留 `org.projectleap.prototype.d`，默认数据目录保留 `Project Leap Prototype D`、配置目录保留 `Project Leap Prototype D Settings`，继续使用既有历史，不自动读取或迁移 C。开发版首次切换到正式构建 1 采用手动安装；已有模块通过同版本修复更换正式兼容信息，保留分析历史。导航固定先显示荧光，再显示计数和后续模块。

计数原生统计库的包内路径为 `engine/leap_cell_counting/libLeapCountingStats.dylib`，固定 ABI 1，导出 `leap_counting_stats_abi_version`、`leap_counting_stats_u8`、`leap_counting_stats_u16`。构建期从冻结 C 源码编译、签名，运行时不调用编译器；缺库或版本不匹配明确报错。二进制单独记录为编译产物，不混入荧光科学源码清单。

Z 显示使用各通道的 `slice_display_range`；DAPI 的范围由源图统一 P1/P99 得出，所有 Z 层保持相同范围。MIP 继续使用各通道的 `display_range`。历史文件缺少切片范围时读取其已有显示范围；这些显示变换不回流模型推理。

Z 滑块拖动时自动使用显示代理：当原图最长边大于 1536 且 `viewZoom <= 1` 时，最长边限制为 1536，按原图像素中心最近邻取样。结束拖动后优先复用匹配的原生缓存，否则约 180 ms 请求原分辨率；放大至 `viewZoom > 1` 也切回原生帧。1024 仅为内部配置和测试值，不提供界面设置项。代理只用于显示，标记始终使用原图 XYZ 坐标，不改变分析、labels、计数或导出。画面不显示快速预览徽章或编号引导线。

后台缓冲解码候选帧时，前台继续显示已经提交的完整旧帧；过期或失败候选不能改变已显示层号或标记。临时切片 PNG 使用无损压缩级别 1，正式导出保持既有保存参数。

任务删除、缓存回收、稀疏迁移及外观行为见 README。计数新增 source_identity 保存实际读取通道的原始像素摘要，用于原图异常后的重新定位；该元数据不参与分割、分类或荧光测量。

正式模块描述使用 schema 4，按 science_runtime 精确版本共享运行环境；两个首发模块均要求 host_api 为 1、minimum_host_build 为 1、platform 为 macos-arm64。宿主构建号必须不低于模块最低构建号，API 和平台须匹配。共享模型与 Fiji 依赖独立发布。主程序生产入口只保留数据和配置位置参数，人工 Fiji 审核不可通过请求参数跳过。

`activeRunDetails` 与 `jobRunDetails` 分别提供当前任务和按需载入的历史运行细节，按任务及模块身份隔离。实时流水增量读取 `console.log`、`events.jsonl`，并跟踪计数模块已有的 `model-progress.jsonl`、`native-recovery-progress.jsonl`、`body-progress.jsonl`。三份进度文件按偏移只处理新增完整记录；参数和耗时变化追加到流水，不重写顶部参数头。每份进度文件每轮读取上限为 64 KiB，未完成的单行缓冲上限为 256 KiB；实时日志显示末 120,000 字符。并行阶段和同批窗口的共享耗时明确标注，不相加为总耗时；荧光已用时包含人工审核等待。

历史视图通过 `summarySections` 输出“任务与软件”“样本与通道”“分析步骤与参数”“审核与结果”“耗时与重要事件”五类摘要，保留任务详情内的二级折叠入口；`resources` 为空，`logText` 为空。摘要由已有请求、日志与事件、导入结果、运行来源记录、分析报告、诊断及确认会话按任务身份读取，`job.json` 的 `run_details` 保存分类摘要。历史读取在后台进行，每份文本最多读取 2 MiB 的首尾内容，JSON 读取上限为 16 MiB；超限标记为信息不完整，原始文件不重写，缺失字段不补造。

资源采样在独立线程读取整机指标，只在当前任务导入或计算且运行细节卡片可见时启用；进入人工审核、切换模块或隐藏卡片后停止。CPU 总量和每逻辑核心按约一秒的计数差计算，核心类型按系统 `logical-cpu-id` 与采样索引匹配，能效核心显示绿色。GPU 利用率为整个设备的读数，分组数来自驱动的 `mGPU` 分组，不估算逐 GPU 核心利用率。GPU、温度、物理内存与 SWAP 约每两秒刷新。温度仅使用已识别的 Apple M3、M3 Pro、M3 Max 传感器组，对当次有效、有限且大于零的读数分别求 CPU/GPU 算术均值，不按启用核心数平均；没有有效读数或硬件未识别时为空。资源读数仅供实时卡片使用，历史视图只展示上述分类摘要。

任务结果缩略图单行横排，超宽时可横向滚动，保留图像纵横比并支持点击打开原图。运行流水、历史摘要及资源采样仅观察已有数据，不进入分割、分类、测量或审核结果计算；科学引擎目录和五个组件包保持不变。
