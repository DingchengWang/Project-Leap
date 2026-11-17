# Project Leap 发布指南

发布从已验证的主程序 DMG、组件归档和对应构建报告开始。发布工具为这些文件生成更新索引与签名；随后将归档上传到 GitHub Releases，把更新文件部署到网站，并核验实际下载。

当前公开版本为 **v1.0.0**，主程序和组件更新通道均已发布。后续发行须保留已发布的 DMG、组件归档与签名文件，并将上一版更新文件作为生成器的输入。

## 1. 核对发行配置与输入

[`tools/build/release-settings.json`](../tools/build/release-settings.json) 保存应用身份、版本、首次公开构建号、正式 URL、公钥、钥匙串账户名和发行文件映射。发布前使配置与本次归档一致；工具使用配置中的精确下载 URL，不根据本地文件名推测地址。

| 发布文件 | 正式地址 |
|---|---|
| 主程序更新列表 | `https://dingchengwang.github.io/Project-Leap/updates/appcast.xml` |
| 更新列表的发布器签名 | `https://dingchengwang.github.io/Project-Leap/updates/appcast.xml.sig` |
| 组件目录 | `https://dingchengwang.github.io/Project-Leap/updates/components.json` |
| 当前主程序 DMG | `https://github.com/DingchengWang/Project-Leap/releases/download/v1.0.0/Project-Leap-1.0.0-macOS-arm64.dmg` |

主程序使用 Sparkle 更新，分析模块及其依赖使用签名组件目录。客户端的 [`app/leap_desktop/release-config.json`](../app/leap_desktop/release-config.json) 必须与发布设置中的组件目录地址、公钥一致。构建器将 `application.feed_url` 和 `application.public_key` 写入应用的 `SUFeedURL` 与 `SUPublicEDKey`；配置修改后需要重新构建，已有 DMG 的内容保持不变。

应用保留标识 `org.projectleap.prototype.d`，最低系统为 macOS 27。公开构建号为全局递增正整数，应用版本变化后也不重置。每个新构建使用新的固定版本下载 URL，旧地址继续提供原归档。五个组件 `project-leap-2d`、`cell-counting`、`science-runtime`、`cellpose-model`、`fiji` 的分发版本和精确 URL 由 `components.assets` 指定。

按[构建说明](../tools/build/README.md)完成源码装配、构建、归档验证和安装验收。生成更新文件前，准备好以下输入：

- 最终主程序专用 DMG 及其 `build-report.json`。报告的 `main_only_dmg` 必须记录该镜像的路径、长度和 SHA-256。
- `packages/catalog.json` 及其中列出的五份组件归档。模块描述符和目录记录的宿主 API、最低构建要求及平台须与本次发行匹配。
- 对应的独立验证记录。验证命令的 `--expected-build` 使用报告中的 `build_number`，具体命令见构建说明。

测试归档不能作为生产输入。归档进入签名流程后保持字节不变，复制为配置中的发行文件名时也应保留内容。

## 2. 准备签名工具与历史输入

使用官方 **Sparkle 2.10.0** 工具包，通过 `--sparkle-bin` 指定包含 `sign_update` 的 `bin` 目录。运行机器还需要 Apple Command Line Tools；归档检查使用 `hdiutil`，验签使用 Swift 和 CryptoKit。

| 签名对象 | macOS 钥匙串账户 |
|---|---|
| 主程序 DMG 与 `appcast.xml` | `project-leap-app-update-v1` |
| 组件目录有效载荷 | `project-leap-component-catalog-v1` |

使用已经建立的正式账户。私钥保留在钥匙串中，发布设置只保存公钥和账户名；工具用对应公钥复核签名。

**已有公开版本的通道必须传入 `--previous`。** 从当前正式地址取得 `components.json`、`appcast.xml` 和 `appcast.xml.sig`，保存到本地只读输入目录。XML 与同名 `.sig` 必须相邻，例如：

```text
published-previous/
  components.json
  appcast.xml
  appcast.xml.sig
```

组件生成器用旧签名目录保留历史包记录；如果遗漏 `--previous`，它会从空历史开始，无法保留旧版本的修复入口。主程序生成器先认证旧 XML 的相邻 `.sig`，再保留全部历史条目。

## 3. 生成后续发行的更新文件

下面的命令在装配后的源码目录执行。`LEAP_RELEASE` 指向本次已验证的构建输出，`LEAP_PACKAGES` 指向对应组件目录，`LEAP_PREVIOUS` 指向上一步保存的历史输入。将 Sparkle 路径替换为本机实际路径，并为 `LEAP_PUBLISH` 选择尚不存在的候选目录。主程序 DMG 路径直接读取本次构建报告。

```sh
LEAP_SETTINGS=tools/build/release-settings.json
LEAP_RELEASE=../release-candidate
LEAP_PACKAGES=../release-1.0.0/packages
LEAP_PREVIOUS=../published-previous
LEAP_PUBLISH="$LEAP_RELEASE/publish-candidate"
LEAP_SPARKLE_BIN=/absolute/path/to/Sparkle-2.10.0/bin
LEAP_REPORT="$LEAP_RELEASE/build-report.json"
LEAP_DMG="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["main_only_dmg"]["path"])' "$LEAP_REPORT")"
mkdir "$LEAP_PUBLISH"

python3 tools/sign_component_catalog.py \
  "$LEAP_PACKAGES/catalog.json" \
  --packages "$LEAP_PACKAGES" \
  --previous "$LEAP_PREVIOUS/components.json" \
  --settings "$LEAP_SETTINGS" \
  --sparkle-bin "$LEAP_SPARKLE_BIN" \
  --output "$LEAP_PUBLISH/components.json"

python3 tools/generate_appcast.py \
  --archive "$LEAP_DMG" \
  --build-report "$LEAP_REPORT" \
  --previous "$LEAP_PREVIOUS/appcast.xml" \
  --settings "$LEAP_SETTINGS" \
  --sparkle-bin "$LEAP_SPARKLE_BIN" \
  --output "$LEAP_PUBLISH/appcast.xml"
```

这两个命令只生成本地文件。组件生成器逐归档核对长度、SHA-256、内部清单、运行描述符和依赖，再按发布设置中的 URL 生成记录。`components.json` 使用 schema 1 签名封装，内含 schema 2 有效载荷，记录全部 `packages` 和各组件的 `latest` 版本。

主程序生成器先比对 DMG 与报告中的长度和 SHA-256，再只读挂载镜像，读取应用的 `Info.plist`，核对身份、版本、构建号、最低系统和更新配置。它为 DMG 生成 Ed25519 签名，将签名、URL 和字节长度写入 `appcast.xml`，并生成相邻的 `appcast.xml.sig`。标准输出中的 JSON 摘要记录版本、构建号、归档哈希、输出路径和历史条目数。

候选目录最终包含 `components.json`、`appcast.xml` 和 `appcast.xml.sig`。其中 `.sig` 用于下一次发布时认证旧 XML 的完整内容；客户端 Sparkle 验证的是下载归档的 Ed25519 签名，本流程不启用 `SURequireSignedFeed`。

### 历史记录与覆盖规则

组件生成器保留旧目录中的全部 `id/version`、URL 和依赖，只为本次提供的版本更新 `latest`。同一 `id/version` 的记录必须完全一致；内容、哈希、依赖或 URL 改变时使用新组件版本。旧记录及其依赖归档继续用于已安装版本的修复。

主程序生成器将新构建置于历史条目前。新构建号必须高于历史最大值，并使用新的不可变下载 URL；同一构建号只能对应同一归档和元数据。完全相同的最新构建可以复用原 XML，旧构建不能重新标为最新。

组件生成器拒绝覆盖已有输出，主程序生成器拒绝覆盖内容不同的输出，因此每次发行均使用新的候选目录。

### 仅用于首次建立更新通道

一个从未发布过的通道没有历史输入，可在上述命令中省略该通道的 `--previous` 参数，其余参数和新建候选目录的要求不变。首次主程序发行的构建号必须等于配置的 `application.first_public_build`，构建时从该字段读取。当前 Project Leap 的两个通道均已有历史，应保留 `--previous`。

## 4. 上传、部署与核验

1. 将新 DMG 和本次需要发布的组件归档上传到配置指定的仓库、tag 和资产文件名。从每个正式 URL 下载，核对长度和 SHA-256 与本地签名输入一致；已发布且未变化的组件继续使用原地址。
2. 将候选目录中的三个更新文件部署到网站 `updates/`，保持 `appcast.xml` 与 `appcast.xml.sig` 成对保存。重新获取线上文件，核对与候选文件字节一致。
3. 使用独立应用数据目录和设置目录，检查在线模块安装、同版本修复和更新检查。发布新的应用构建时，还要检查 Sparkle 下载、签名验证和重启更新。
4. 正式下载通过核验后，更新网站和用户指南中的版本及下载入口。

保存本次发布配置、构建报告、验证记录、实际发行归档及三个更新文件，作为下一次发行的历史输入。
