# Project Leap 发布指南

本指南说明如何从已验证的主程序 DMG 和组件归档生成正式更新文件。正式地址和公钥已进入源码；线上更新服务尚未发布，公开下载尚未启用。当前还没有可用于生成生产更新文件的最终 DMG。下列命令在真实发行文件完成后执行，生成的本地候选文件仍需另行发布和核验。

主程序使用 Sparkle 更新；分析模块及其共享依赖使用签名组件目录。两者的发布文件位于同一网站的 `updates/` 目录，但使用不同密钥。

## 1. 核对发行配置

发布工具默认读取 [`tools/build/release-settings.json`](../tools/build/release-settings.json)。其中包含网站和更新地址、应用身份、首次公开构建号、两组公钥与钥匙串账户名，以及每个发行文件的精确下载 URL。`--settings` 可显式指定这份文件。发布前应使它与本次真实归档一致；工具不会根据文件名猜测下载地址。

| 用途 | 正式地址 |
|---|---|
| 主程序更新列表 | `https://dingchengwang.github.io/Project-Leap/updates/appcast.xml` |
| 主程序更新列表的发布器签名 | `https://dingchengwang.github.io/Project-Leap/updates/appcast.xml.sig` |
| 组件目录 | `https://dingchengwang.github.io/Project-Leap/updates/components.json` |
| 首发主程序 DMG | `https://github.com/DingchengWang/Project-Leap/releases/download/v1.0.0/Project-Leap-1.0.0-macOS-arm64.dmg` |

客户端组件配置位于 [`app/leap_desktop/release-config.json`](../app/leap_desktop/release-config.json)，其目录地址、公钥必须与发布设置一致。构建器还会将 `application.feed_url` 和 `application.public_key` 写入应用的 `SUFeedURL`、`SUPublicEDKey`。更新配置后须重新构建应用，编辑源码不会改变已有 DMG。

首发为应用版本 `1.0.0`、构建号 `1`，最低系统为 macOS `27.0`，应用标识保留 `org.projectleap.prototype.d`。以后公开构建号按 `1 → 2 → 3 → …` 全局递增，应用版本变化时也不重置。每个新构建使用新的、固定版本的下载 URL，保留以前构建的归档。

首发五份组件的下载文件映射如下。实际完整 URL 以发布设置中的 `components.assets` 为准。

| 组件 ID | 分发版本 | GitHub 仓库与 Release tag | 发行文件名 |
|---|---|---|---|
| `project-leap-2d` | `1.1.0` | `Project-Leap-2D-Integrated-IHC-fluorescence-analysis` / `v1.1.0` | `project-leap-2d-1.1.0-macos-arm64.tar.gz` |
| `cell-counting` | `1.0.0` | `Project-Leap-Cell-Counting` / `v1.0.0` | `project-leap-cell-counting-1.0.0-macos-arm64.tar.gz` |
| `science-runtime` | `1.0.0` | `Project-Leap` / `v1.0.0` | `project-leap-science-runtime-1.0.0-macos-arm64.tar.gz` |
| `cellpose-model` | `4.2.1.1` | `Project-Leap` / `v1.0.0` | `project-leap-cellpose-model-4.2.1.1.tar.gz` |
| `fiji` | `20260718.0417` | `Project-Leap` / `v1.0.0` | `project-leap-fiji-20260718.0417-macos-arm64.tar.gz` |

荧光模块的分发版本 `1.1.0` 对应科学版本 `1.0.1`。原荧光仓库的脚本版 `v1.0.1` 继续保留在原仓库，不作为新组件目录中的模块版本。

## 2. 准备签名工具与最终归档

在 macOS 上使用官方 **Sparkle 2.10.0** 的工具包，并为每个生成命令传入 `--sparkle-bin`，提供包含 `sign_update` 的官方 `bin` 目录。下文示例使用绝对路径，避免路径含义随执行目录变化。这组发布工具通过 `sign_update` 调用以下 macOS 钥匙串账户：

| 签名对象 | 钥匙串账户 |
|---|---|
| 主程序 DMG、`appcast.xml` | `project-leap-app-update-v1` |
| 组件目录有效载荷 | `project-leap-component-catalog-v1` |

使用已经建立的正式账户；私钥保留在钥匙串中，不导出为文件或写入仓库。发布设置只保存公钥和账户名。工具以对应公钥复核生成的签名。运行机器还需具备 Apple Command Line Tools；归档检查使用 `hdiutil`，公钥验签使用 Swift 和 CryptoKit。

按[本机构建说明](../tools/build/README.md)完成源码装配、构建、归档验证和安装验收。以下步骤沿用其工作目录：在装配目录 `build/source` 内执行，真实发行文件位于相邻的 `build/release-1.0.0`。生成更新文件前应已具备：

- 最终 `Project-Leap-1.0.0-macOS-arm64.dmg` 及对应 `build-report.json`；报告中的 `main_only_dmg` 必须记录该文件的长度和 SHA-256。
- `packages/catalog.json` 以及其中列出的五份真实组件归档。模块描述符和目录记录应采用本次正式的宿主 API、最低构建号和平台约束。
- 与这些文件对应的独立归档验证记录；首发主程序验证要求 `--expected-build 1`。

只有这些输入完成后，才创建生产候选目录。测试 fixture 和旧的最低宿主构建号为 `17` 的组件包不属于本次正式输入。归档进入签名流程后保持字节不变；将包复制为上表中的发行文件名时也应保留原始归档内容。

## 3. 生成首发更新文件

在下列命令中，将 `/absolute/path/to/Sparkle-2.10.0/bin` 替换为已准备好的官方工具目录。生成器只写入本地文件，不上传 GitHub Release 或部署网站。

```sh
mkdir ../release-1.0.0/publish-candidate-1

python3 tools/sign_component_catalog.py \
  ../release-1.0.0/packages/catalog.json \
  --packages ../release-1.0.0/packages \
  --settings tools/build/release-settings.json \
  --sparkle-bin /absolute/path/to/Sparkle-2.10.0/bin \
  --output ../release-1.0.0/publish-candidate-1/components.json

python3 tools/generate_appcast.py \
  --archive ../release-1.0.0/Project-Leap-1.0.0-macOS-arm64.dmg \
  --build-report ../release-1.0.0/build-report.json \
  --settings tools/build/release-settings.json \
  --sparkle-bin /absolute/path/to/Sparkle-2.10.0/bin \
  --output ../release-1.0.0/publish-candidate-1/appcast.xml
```

组件生成器从本地 `catalog.json` 读取路径，逐归档核对长度、SHA-256、内部内容清单、运行描述符及依赖，再用发布设置中的精确 URL 生成在线记录。输出 `components.json` 使用 schema 1 签名封装，封装内为 schema 2 有效载荷，包含全部 `packages` 和各组件的 `latest` 版本。

主程序生成器先核对 DMG 与构建报告的长度及 SHA-256，再只读挂载 DMG，读取其中应用的 `Info.plist`，确认应用身份、版本、构建号、最低系统和更新配置。它为 DMG 生成 Ed25519 签名，并将签名、归档 URL 和字节长度写入 `appcast.xml`。命令同时生成 `appcast.xml.sig`，并在标准输出中给出版本、构建号、归档哈希、输出路径和历史条目数的 JSON 摘要。

最终候选目录包含：

```text
publish-candidate-1/
  components.json
  appcast.xml
  appcast.xml.sig
```

`appcast.xml.sig` 供发布工具在下一次发行时认证上一版 XML 的完整内容。客户端仍通过 Sparkle 验证每个下载归档的 Ed25519 签名；本流程不启用 `SURequireSignedFeed`。两种签名的用途应分别保留。

## 4. 发布后续版本并保留历史

下一次发行时，先取得上一版实际发布的 `components.json`、`appcast.xml` 和 `appcast.xml.sig`，保存为只读输入。XML 与同名 `.sig` 放在同一目录，例如 `../published-previous/`。首次发行省略 `--previous`；后续发行必须传入上一版文件。

更新发布设置，使当前版本、归档名称和下载 URL 与新文件一致，然后在新的候选目录生成结果。例如第二次发行使用 `publish-candidate-2`，两个命令分别增加：

```text
sign_component_catalog.py: --previous ../published-previous/components.json
generate_appcast.py:       --previous ../published-previous/appcast.xml
```

组件生成器先验证旧目录的签名，再保留其中所有历史 `id/version` 记录、精确包 URL 和依赖，只为本次明确提供的版本更新 `latest`。同一 `id/version` 的记录必须完全一致；内容、哈希、依赖或 URL 改变时，应使用新组件版本。保留旧记录及其依赖归档，才能按已安装版本修复旧安装。

主程序生成器先用相邻 `.sig` 认证旧 XML，再保留全部历史条目，并将新构建放在最前。新的构建号必须大于历史最大值，下载 URL 也必须是新的不可变地址。同一构建号只能对应同一份归档和元数据；完全相同的最新构建可原样复用旧 XML，不能把旧构建重新标为最新。

组件生成器拒绝覆盖已有输出文件；主程序生成器拒绝覆盖内容不同的输出。每次准备发行都使用新的候选目录，保留上一版发布文件。

## 5. 上线与核验

本地生成完成后，按以下顺序执行实际发布：

1. 将 DMG 和五份组件归档上传到发布设置指定的仓库、tag 和资产文件名。随后从每个正式 URL 下载，核对长度和 SHA-256 与本地已签名文件一致。
2. 将候选目录中的三个更新文件部署到网站 `updates/`，保持 `appcast.xml` 与 `appcast.xml.sig` 成对保存。重新获取线上文件，核对它们与候选文件字节一致。
3. 在独立应用数据目录和设置目录中验收在线模块安装、同版本修复和更新检查；有后续应用版本时，再验收 Sparkle 的下载、签名验证和重启更新。
4. 以上正式地址和实际下载通过核验后，启用网站下载入口，并更新用户指南中的发布状态。

保留本次发布设置、构建报告、归档验证记录、实际发行归档和三个更新文件，供下一次发行核对。源码配置完成、本地文件生成、线上发布和实际安装验收分别记录其完成状态。
