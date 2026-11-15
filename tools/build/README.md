# Project Leap 本机构建

应用、交付文件夹和磁盘卷名使用 **Project Leap**；公开主程序 DMG 文件名为 **Project-Leap-1.0.0-macOS-arm64.dmg**，可选完整离线 DMG 为 **Project Leap V1.0.0.dmg**。正式版本为 `1.0.0`，首发构建号为 `1`；公开构建号按 `1 → 2 → 3 → …` 全局递增，软件版本变化时继续递增，产品名称不包含构建号。Apple Silicon / macOS `27.0` 起。两个科学模块分别为星形胶质细胞荧光强度分析分发版本 `1.1.0` 和细胞计数分发版本 `1.0.0`，科学版本分别为 `1.0.1` 和 `1.0.0`。保留 bundle ID `org.projectleap.prototype.d`、数据目录 `Project Leap Prototype D` 和设置目录 `Project Leap Prototype D Settings`，继续使用既有历史，不迁移或合并其他安装的数据。

## 离线交付

```text
Project Leap/
  Project Leap.app
  Install — 安装说明.txt
  Project Leap Packages/
    catalog.json
    package-build.json
    project-leap-2d/1.1.0/package.tar.gz
    cell-counting/1.0.0/package.tar.gz
    science-runtime/1.0.0/package.tar.gz
    cellpose-model/4.2.1.1/package.tar.gz
    fiji/20260718.0417/package.tar.gz
```

应用只封装界面、维护、导入接口和本地参考文献、许可证说明。模块使用说明从界面打开在线页面。科学解释器、依赖、引擎、Fiji 和权重位于外置组件；两个模块通过 schema 4 描述符依赖共享科学环境 `science-runtime 1.0.0` 和共享模型，模块包本身仅包含各自引擎及其许可证，只有荧光依赖 Fiji。共享模型和 Fiji 描述符为 schema 2，组件目录和内容清单为 schema 1。权重仅在 `cellpose-model` 中存在一份，固定 `cpsam_v2` SHA-256。

## 构建输入与隔离

主仓保存宿主应用与构建工具；两个科学模块保存在各自仓库。先在主仓根目录运行装配工具，将主仓 `HEAD` 与 `tools/build/module-sources.json` 锁定的两个模块提交导出到新的构建目录：

```sh
python3 tools/assemble_sources.py --fluorescence ../Project-Leap-2D-Integrated-IHC-fluorescence-analysis --counting ../Project-Leap-Cell-Counting --output ../../build/source
cd ../../build/source
```

后续命令均在该装配目录执行。运行环境、原生依赖、组件包和应用在装配后准备；三个源码仓库及保留的旧交付继续作为只读输入。本次发行的输出目录为装配目录旁的 `../release-1.0.0`，对应发布工作区的 `build/release-1.0.0`。

重新构建需要先准备独立的 Python 界面环境、科学 seed 和 Sparkle 2.10.0 vendor；以下路径是构建工具要求的输入位置。UI 解释器为 `runtime/ui-env/bin/python`，固定依赖记录见 [ui-requirements-lock.txt](licenses/ui-requirements-lock.txt)。科学解释器及依赖从准备好的 `runtime/seed` 读取并流式压缩；不修改 seed，不复制输入图像。本次正式发行的五份组件包输出到独立的 `../release-1.0.0/packages`，构建命令显式指定该位置；已有归档且源码、构建器、签名身份、组件目录契约、科学 seed 排除清单及归档哈希均匹配时直接复用。仅 UI 允许列表变化不重建科学组件；保留原始包构建记录，在主应用报告中记录其哈希和原生产策略哈希。重打组件时，按目标描述符和逐文件清单精确计算内容清单哈希；未变化组件再核验归档 SHA-256 后复用，仅压缩实际改变的组件。模块描述符与目录记录均含宿主 API 1、最低构建 1 和 macos-arm64 平台约束。脚本拒绝替换 `runtime/packages`、`runtime/prototype-c`、`artifacts/prototype-c` 及其子目录或父目录。

源码副本不包含依赖环境时，可从保留的完整交付恢复科学 seed 与 Sparkle，无需重新下载模型或 Fiji：

```sh
python3 -m venv runtime/ui-env
uv pip install --python runtime/ui-env/bin/python -r tools/build/licenses/ui-requirements-lock.txt
runtime/ui-env/bin/python tools/build/restore_inputs.py "/path/to/retained/Project Leap"
runtime/ui-env/bin/python native/embed_sparkle.py --verify-vendor
```

恢复工具逐归档核对目录记录、SHA-256、内部清单、源码和模型归属，再逐文件验证提取结果。`runtime/seed.provenance.json` 明确记录 seed 由已验证组件重建，原始 seed 清单未被恢复；重建的描述符和清单使用自身真实哈希。Sparkle 从严格签名校验通过的交付应用复制，来源及全部文件/链接清单记入 `native/vendor/Sparkle-2.10.0.provenance.json`，每次嵌入前再次比对。工具只接受尚不存在的恢复目标。

应用和引擎源码在同一时点冻结。本次修缮基线位于 `artifacts/project-leap-final-refinement/baseline`，此前开发基线保留于 `artifacts/prototype-d/baseline`。两份基线清单均纳入构建输入；荧光科学完整性仍固定到 `artifacts/prototype-c/baseline/source-sha256.json` 和两处已审核适配清单；本次生产清理由 `artifacts/release-1.0.0/engineering-pruning.json` 逐文件记录精确修改/删除，独立验证器固定其审核后 SHA-256。原始 95 文件基线保持原值。

原生输入包括 Sparkle 固定 vendor、玻璃桥、AppKit 选择组、交互桥、年龄接口和存储协调桥，均随源码冻结。SwiftUI 保留容器布局，实际玻璃选择组使用 AppKit。运行时仅依赖系统 Swift，不携带开发环境路径。构建需要 Command Line Tools 的 clang、swiftc、codesign、iconutil、hdiutil、ditto。

构建前检查可用空间。交付暂存使用同卷 APFS clone；构建结束只移除脚本自己的 PyInstaller 工作目录和 DMG 暂存树。原始图像、历史任务和外部安装不属于构建脚本的清理范围。

## 计数原生统计库

`native/counting_stats.c` 是项目自有的精确整数统计内核，只使用系统 libdispatch/libSystem。构建时由 `tools/build_counting_stats.py` 以 arm64、macOS27、O3 和 warnings-as-errors 编译并签名，随后注入计数组件的 `engine/leap_cell_counting/libLeapCountingStats.dylib`。不向源码目录写入二进制，也不进入荧光组件或主 UI 应用。

`runtime.json.native_statistics` 保存相对路径、库 SHA-256、C 源码 SHA-256 和 ABI 1。包构建记录与最终应用报告另存编译命令、构建器哈希、系统依赖、导出及签名检查。复用组件包同时要求 C 源码、构建器和签名身份一致。独立 verifier 从实际计数归档读取库字节，在临时目录核对 arm64、minOS27、三个固定导出、严格签名，以及清除 DYLD 覆盖后的 ABI 实际加载。

科学 worker 默认从自身模块目录加载预编译库。仅源码开发允许显式绝对路径 `LEAP_COUNTING_STATS_LIBRARY`；缺失或 ABI 不匹配会报错，不在运行时编译，也不切换统计实现。单独构建开发库：

```sh
runtime/ui-env/bin/python tools/build_counting_stats.py --output ../native-counting/libLeapCountingStats.dylib
```

## 执行与验证

源码冻结后执行一次完整构建。应用界面变化不改变科学组件；五份现有组件的源码、元数据和归档哈希匹配时，直接只读复用，不建立临时候选或重复运行 prepare：

```sh
runtime/ui-env/bin/python tools/build_app.py --version 1.0.0 --build-number 1 --packages ../release-1.0.0/packages --output ../release-1.0.0 --main-only-dmg
runtime/ui-env/bin/python tools/verify_app_bundle.py --build-report ../release-1.0.0/build-report.json --expected-build 1 --output ../release-1.0.0/bundle-verification.json
```

仅需预生成组件包时使用 `tools/build_app.py --packages-only --packages ../release-1.0.0/packages`。构建号必须是大于等于 1 的正整数。首发验证命令显式要求 `--expected-build 1`；后续构建按其实际编号验证。同一输出已有 build-report 或容器时拒绝覆盖，重建使用新的 `--output ../<新目录>`。模块源码变化时，仅重建带自身 `package-build.json` 的组件包目录。可加 `--zip` 生成 ZIP；本机空间有限时不同时保留不必要的重复容器。

签名应用暂存在 `/private/tmp/ProjectLeap-build-*`，避免 FileProvider 为松散应用附加资源而破坏签名。上述命令的真实应用和交付文件夹路径记录于 `../release-1.0.0/build-report.json`；`Project-Leap-1.0.0-macOS-arm64.dmg` 及验证记录保存在同目录。原组件目录保持不变，签名应用暂存保留至安装验收后清理。构建默认 ad hoc 签名，未设置公证或公开更新源。

独立验证核对严格代码签名、arm64、macOS27、保留的 bundle ID、统一产品名称、源码/许可证字节、原生符号、系统 Swift 实际加载和五份归档。模型归属、流式归档内容、内部链接、模块源码与荧光原始基线均独立核验。源码或原生/构建输入在构建期间有变化时拒绝交付。

DMG 使用 `hdiutil verify`，随后只读挂载；对挂载应用再运行相同 verifier，并传入 `--app`、`--expected-build 1` 和独立 `--output`。公开主程序 DMG 不含组件，验证时须用 `--packages` 指向本次已验证的五份组件包目录；完整离线 DMG 则核对应用旁的组件。ZIP 若生成，须解包后验证。实际启动/安装验收使用独立 `--data-dir` 与 `--config-dir`。正式入口不携带 probe、截图或自动退出测试开关；测试由开发侧工具驱动。签名和归档检查不等同运行或科学准确率验证。

生产允许列表位于 `release_policy.py`。主应用源码逐文件允许；Qt QML 只收集 Basic 样式及 Dialogs、Layouts、Models 等 15 个实际依赖模块，不收集 QML 调试插件、WebEngine、Graphs、Quick3D、QtTest、PDF 和虚拟键盘。所有筛选均在 PyInstaller 收集与签名前完成。科学环境剔除独立 tests/test/fixture 目录及 Torch 内部测试框架，保留 NumPy/SciPy/Torch 正常导入依赖的 testing helpers；打包前在独立裁剪副本实测实际依赖导入、NumPy/SciPy 运算及两个固定模型加载。原始 seed 不修改。

主应用允许列表保持 42 项。运行流水、分类摘要和资源读数使用现有 Python、Qt 及 macOS 系统接口，不新增生产文件或运行依赖；科学引擎源码保持不变。正式发行只因两个模块的宿主兼容元数据变化而重建模块清单和归档；三个共享组件是否复用由目标内容清单和归档字节核验决定。

生产组件目录配置 `app/leap_desktop/release-config.json` 的地址与公钥保持空值；Sparkle 无生产 feed/key 时同样未配置。模拟下载目录、签名密钥和本机地址只用于开发侧验证，不进入交付源码或配置。完整离线 DMG 包含上述五份真实组件归档；公开主程序发布使用 `--main-only-dmg` 单独生成 `Project-Leap-1.0.0-macOS-arm64.dmg`，其中只有主应用和 Applications 链接。只有显式指定 `--dmg` 时才生成含五份组件的完整离线 DMG。
