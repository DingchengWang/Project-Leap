# Project Leap 本机构建

公开主程序 DMG 包含 **Project Leap.app** 和 Applications 链接，用户安装后在应用内下载分析模块。当前公开版本为 **v1.0.0**，镜像文件名为 `Project-Leap-1.0.0-macOS-arm64.dmg`，运行于 Apple Silicon / macOS 27 或更高版本。下文说明如何装配源码、准备依赖、构建主程序并验证产物；完整离线交付见文末。

已发布的 DMG、组件归档和签名文件保持原样。每次新构建使用独立输出目录，发布文件名称及下载地址由 [`release-settings.json`](release-settings.json) 确定。

## 1. 装配源码与准备输入

主仓提供宿主应用和构建工具，两个模块仓提供科学引擎。在主仓根目录运行以下命令，将主仓 `HEAD` 和 [`module-sources.json`](module-sources.json) 锁定的模块提交导出到一个尚不存在的目录：

```sh
python3 tools/assemble_sources.py \
  --fluorescence ../Project-Leap-2D-Integrated-IHC-fluorescence-analysis \
  --counting ../Project-Leap-Cell-Counting \
  --output ../../build/source-candidate
cd ../../build/source-candidate
```

后续命令均在该装配目录执行。运行环境、原生依赖、组件包和应用在这里准备；三个源码仓库及保留的交付文件作为只读输入。构建需要 Apple Command Line Tools 中的 clang、swiftc、codesign、iconutil、hdiutil 和 ditto，并需要足够的暂存空间。

构建器读取以下依赖：

| 输入 | 位置与用途 |
|---|---|
| UI Python 环境 | `runtime/ui-env/bin/python`；依赖固定在 [ui-requirements-lock.txt](licenses/ui-requirements-lock.txt)。 |
| 科学环境 seed | `runtime/seed`；提供科学解释器与依赖，构建时流式压缩。 |
| Sparkle | `native/vendor` 中的固定 Sparkle 2.10.0；用于主程序更新。 |
| 原生源码 | 玻璃桥、AppKit 选择组、交互桥、年龄接口、存储协调桥和计数统计库；与应用及引擎源码一起冻结。 |

源码副本没有这些依赖环境时，可从保留的完整交付恢复科学 seed 和 Sparkle。将下列交付路径替换为实际位置；恢复目标必须尚不存在：

```sh
python3 -m venv runtime/ui-env
uv pip install --python runtime/ui-env/bin/python -r tools/build/licenses/ui-requirements-lock.txt
runtime/ui-env/bin/python tools/build/restore_inputs.py "/path/to/retained/Project Leap"
runtime/ui-env/bin/python native/embed_sparkle.py --verify-vendor
```

恢复工具逐归档核对目录记录、SHA-256、内部清单、源码和模型归属，再逐文件验证提取结果。`runtime/seed.provenance.json` 记录组件重建来源，并注明原始 seed 清单未恢复；重建的描述符和清单使用自身的实际哈希。Sparkle 从通过严格签名校验的应用复制，其文件和链接清单记录在 `native/vendor/Sparkle-2.10.0.provenance.json`，每次嵌入前再次核对。

## 2. 构建公开主程序 DMG

先使发布配置中的版本、归档名和下载 URL 对应本次发行。构建器会核对 `tools/build/release-settings.json` 与客户端的 `app/leap_desktop/release-config.json`，并将主程序更新源及公钥写入应用。

当前主程序更新通道已有公开版本。下面从配置读取版本，从**最新已发布构建的报告**读取构建号并递增，再写入新的输出目录。`application.first_public_build` 只表示首次公开构建号；后续构建不能沿用这个默认值。公开构建号在应用版本变化后继续递增，且必须高于已发布 appcast 中的最大值。

把 `LEAP_PREVIOUS_REPORT` 换成保存的最新发布报告，`LEAP_PACKAGES` 换成已验证的组件包目录，`LEAP_OUTPUT` 换成新的输出目录：

```sh
LEAP_SETTINGS=tools/build/release-settings.json
LEAP_PREVIOUS_REPORT=../published-previous/build-report.json
LEAP_PACKAGES=../release-1.0.0/packages
LEAP_OUTPUT=../release-candidate
LEAP_VERSION="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["application"]["version"])' "$LEAP_SETTINGS")"
LEAP_BUILD_NUMBER="$(python3 -c 'import json,sys; print(int(json.load(open(sys.argv[1]))["build_number"])+1)' "$LEAP_PREVIOUS_REPORT")"

runtime/ui-env/bin/python tools/build_app.py \
  --version "$LEAP_VERSION" \
  --build-number "$LEAP_BUILD_NUMBER" \
  --packages "$LEAP_PACKAGES" \
  --output "$LEAP_OUTPUT" \
  --main-only-dmg

runtime/ui-env/bin/python tools/verify_app_bundle.py \
  --build-report "$LEAP_OUTPUT/build-report.json" \
  --expected-build "$LEAP_BUILD_NUMBER" \
  --output "$LEAP_OUTPUT/bundle-verification.json"
```

组件包的源码、构建器、签名身份、目录约定、科学 seed 排除清单及归档哈希全部匹配时，构建器直接复用归档，并在主应用报告中记录原始包构建记录和生产策略的哈希。仅 UI 允许列表变化不会重建科学组件。

模块源码或兼容信息变化时，先将 `LEAP_PACKAGES` 设为新的组件输出目录；构建器按目标描述符和逐文件清单计算内容哈希，核对未变化组件的归档 SHA-256 后复用，只重新压缩实际变化的组件。仅需准备组件包时运行：

```sh
runtime/ui-env/bin/python tools/build_app.py --packages-only --packages "$LEAP_PACKAGES"
```

构建器拒绝覆盖已有 `build-report.json` 或容器，也拒绝替换受保护的 `runtime/packages`、`runtime/prototype-c`、`artifacts/prototype-c` 及其父子目录。使用新输出目录，不覆盖已发布文件。

若为一个尚无任何公开版本的通道准备首次发行，构建命令保持不变，仅将构建号取自配置：

```sh
LEAP_BUILD_NUMBER="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["application"]["first_public_build"])' "$LEAP_SETTINGS")"
```

## 3. 验证应用与容器

`build-report.json` 记录实际应用路径 `app`、交付目录 `delivery_directory`、版本 `version` 和构建号 `build_number`；`main_only_dmg` 记录公开主程序镜像的路径、长度和 SHA-256。签名应用保存在 `/private/tmp/ProjectLeap-build-*`，以避免 FileProvider 为松散应用附加资源后破坏签名，不能根据报告所在目录推测应用位置。

构建默认采用 ad hoc 签名，未进行 Apple 公证。验证器检查严格代码签名、arm64、macOS 27、bundle ID、产品名称、源码和许可证字节、原生符号、系统 Swift 实际加载及五份组件归档，同时核对模型归属、归档内容、内部链接、模块源码和荧光基线。构建过程中源码或原生、构建输入发生变化时，构建器拒绝交付。

对 DMG 运行 `hdiutil verify` 后只读挂载，并再次验证挂载中的应用。将 `LEAP_MOUNTED_APP` 设为该挂载点内的实际应用路径；以下构建号从待验证报告读取：

```sh
LEAP_REPORT="$LEAP_OUTPUT/build-report.json"
LEAP_EXPECTED_BUILD="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["build_number"])' "$LEAP_REPORT")"
LEAP_DMG="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["main_only_dmg"]["path"])' "$LEAP_REPORT")"
hdiutil verify "$LEAP_DMG"
hdiutil attach -readonly -nobrowse "$LEAP_DMG"
```

从挂载结果确认路径后执行：

```sh
LEAP_MOUNTED_APP="/Volumes/Project Leap/Project Leap.app"
runtime/ui-env/bin/python tools/verify_app_bundle.py \
  --app "$LEAP_MOUNTED_APP" \
  --packages "$LEAP_PACKAGES" \
  --build-report "$LEAP_REPORT" \
  --expected-build "$LEAP_EXPECTED_BUILD" \
  --output "$LEAP_OUTPUT/mounted-bundle-verification.json"
```

公开主程序 DMG 不包含组件，所以挂载验证必须通过 `--packages` 提供本次已验证的组件目录。实际启动和安装验收使用独立的 `--data-dir` 与 `--config-dir`；签名及归档检查不能替代运行验收或科学准确率验证。正式入口不提供 probe、截图或自动退出测试开关，开发侧工具负责驱动测试。

交付暂存使用同卷 APFS clone。构建结束时，脚本只清理自己的 PyInstaller 工作目录和 DMG 暂存树；签名应用暂存保留至安装验收后再清理。原始图像、历史任务和外部安装不在清理范围内。

验证完成后，按[发布指南](../../docs/releasing.md)生成并发布 `components.json`、`appcast.xml` 和 `appcast.xml.sig`。

## 4. 组件内容与构建约束

主应用封装界面、维护、导入接口及本地参考文献和许可证页面；模块说明在浏览器中打开。科学解释器、引擎、Fiji 和权重存放在外部组件。模块使用 schema 4 描述符，按精确版本依赖 `science-runtime`；模块包只包含自身引擎和许可证，荧光模块另依赖 Fiji。共享模型和 Fiji 描述符使用 schema 2，本地组件目录和内容清单使用 schema 1。固定 `cpsam_v2` 权重只由 `cellpose-model` 保存。

生产允许列表位于 `release_policy.py`。主应用逐文件收集源码；Qt QML 包含 Basic 样式及 Dialogs、Layouts、Models 等实际依赖，不收集调试插件、WebEngine、Graphs、Quick3D、QtTest、PDF 或虚拟键盘。筛选在 PyInstaller 收集和签名前完成。科学环境去除独立 tests/test/fixture 目录及 Torch 内部测试框架，保留正常导入所需的 testing helpers，并在独立裁剪副本中验证依赖导入、NumPy/SciPy 运算和两个固定模型加载。原始 seed 保持不变。

源码与构建输入的审核记录保存在 `artifacts/project-leap-final-refinement/baseline`、`artifacts/prototype-d/baseline` 和 `artifacts/release-1.0.0/engineering-pruning.json`。荧光科学完整性由 `artifacts/prototype-c/baseline/source-sha256.json` 的原始 95 文件基线及两处适配清单约束，验证器核对审核后的精确哈希。

应用保留 bundle ID `org.projectleap.prototype.d`、默认数据目录 `Project Leap Prototype D` 和设置目录 `Project Leap Prototype D Settings`，以继续读取这些目录中的任务。应用不会自动迁移或合并其他安装的数据。应用、交付文件夹和磁盘卷名统一使用 Project Leap。原生界面保留 SwiftUI 容器布局，玻璃选择组使用 AppKit；运行时只依赖系统 Swift。

### 计数原生统计库

`native/counting_stats.c` 提供精确整数统计，仅依赖系统 libdispatch/libSystem。`tools/build_counting_stats.py` 在构建时以 arm64、macOS 27、O3 和 warnings-as-errors 编译并签名，随后将库放入计数组件的 `engine/leap_cell_counting/libLeapCountingStats.dylib`。源码目录、荧光组件和主 UI 应用不包含该二进制。

`runtime.json.native_statistics` 保存相对路径、库 SHA-256、C 源码 SHA-256 和 ABI 1。包构建记录及应用报告记录编译命令、构建器哈希、系统依赖、导出与签名检查；复用要求 C 源码、构建器和签名身份一致。验证器从实际计数归档读取库，在临时目录检查 arm64、minOS 27、三个固定导出、严格签名，并在清除 DYLD 覆盖后加载和验证 ABI。

科学 worker 从自身模块目录加载预编译库；缺失或 ABI 不匹配时报错，不在运行时编译或切换统计实现。源码开发可以用绝对路径 `LEAP_COUNTING_STATS_LIBRARY` 指定开发库，单独构建命令为：

```sh
runtime/ui-env/bin/python tools/build_counting_stats.py --output ../native-counting/libLeapCountingStats.dylib
```

## 5. 可选：完整离线交付

需要离线安装时，在新的输出目录构建，并将主程序构建命令的 `--main-only-dmg` 改为 `--dmg`。该选项生成 `Project Leap V1.0.0.dmg`，内容为：

```text
Project Leap/
  Project Leap.app
  Install — 安装说明.txt
  Project Leap Packages/
    catalog.json
    package-build.json
    <component-id>/<version>/package.tar.gz
```

`Project Leap Packages` 包含荧光、计数、科学运行环境、模型和 Fiji 五份组件归档，版本和路径以 `catalog.json` 为准。完整离线镜像验证时使用应用旁的组件目录。需要 ZIP 时可加 `--zip`，解包后同样验证其中的应用和组件。
