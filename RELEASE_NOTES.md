## netboost v1.0.0 · 首次发布 / Initial Release

> 把「单连接限速」打穿的下载器。
> **粘贴链接 → 自动认出是什么文件 → 自动选好文件夹 → 确认即下。**

**简体中文** · [English](#english)

---

# 简体中文

![主界面](https://raw.githubusercontent.com/lx67621956-create/netboost/main/docs/gui-screenshot.png)

## 下载

| 文件 | 用途 | 说明 |
|---|---|---|
| `netboost-gui-windows-x64.exe` | **图形界面（推荐）** | 双击即用，无需安装 Python · 10.5 MB |
| `netboost-cli-windows-x64.exe` | 命令行版 | 适合批量 / 自动化 · 7.5 MB |

两个都是**单文件绿色版**，可直接拷到别的 Windows 电脑运行。

> 首次启动会慢 1~3 秒（单文件 exe 需解压运行时），属正常现象。

---

## 怎么用：三步

```
粘贴链接  →  自动识别类型  →  确认下载
```

粘贴 `...Interstellar.2014.2160p.mkv`，它会立刻告诉你：

> **▶ 视频** ｜ 依据：链接文件名扩展名 `.mkv`
> 保存到：`C:\Users\你\Downloads\视频`

不想改就直接回车。想改类型点一下别的分类，文件夹立刻跟着换。

### 自动识别结果确认框

![确认框](https://raw.githubusercontent.com/lx67621956-create/netboost/main/docs/confirm-screenshot.png)

自动识别 5 个分类：

| 类型 | 扩展名（举例） | 默认目录 |
|---|---|---|
| ▶ 视频 | mp4 mkv avi mov wmv rmvb ts… | `下载\视频` |
| ♪ 音乐 | mp3 flac wav aac m4a ogg ape… | `下载\音乐` |
| ▤ 文档 | doc docx xls xlsx ppt pptx pdf txt csv epub… | `下载\文档` |
| ⚙ 程序 | exe msi apk dmg zip rar 7z iso… | `下载\程序` |
| ■ 其他 | jpg png bin dat… | `下载\其他` |

判定顺序：**文件名扩展名 → 服务器 Content-Type → URL 路径 → 兜底**。

### 设置：每个分类的文件夹

![设置](https://raw.githubusercontent.com/lx67621956-create/netboost/main/docs/settings-screenshot.png)

### 浏览器接入（可选，默认全关）

![浏览器接入](https://raw.githubusercontent.com/lx67621956-create/netboost/main/docs/browser-screenshot.png)

---

## 主要功能

**多连接分片加速**
默认 8 连接起步，自动增压到「再加也不涨」为止。本地实测：单连接 257KB/s → 8 连接 1.53MB/s，**6.1 倍加速**。

**自动识别 5 类文件**
粘贴链接就知道是视频、音乐、文档、程序还是其他，自动选好对应文件夹，你只需点确认。

**视频字幕联动**
可附字幕链接或本地字幕文件，自动改成与影片同名同目录，播放器自动加载。多语言带后缀 `.chs.srt` / `.eng.srt`，互不覆盖。ASS 不会被误存成 SRT。

**断点续传**
中断后重下同链接自动从断点继续。续传前比对文件大小与 ETag，源文件变了就从头下，绝不拼出坏文件。

**可选限速**
填 `5M` / `800K` 即生效，多连接共享同一速率上限。

---

## 测试结果

| 测试 | 内容 | 结果 |
|---|---|---|
| 引擎 | 加速比 / 完整性 / 续传 / 退化 / 404 / 断连重试 / 暂停恢复 / 小文件 / 0字节 / 中文名 / 限速 / MD5 | **12/12** |
| 识别与协议 | 5 类识别 + 自动目录 + 协议可逆性 | **30/30** |
| 分类与字幕 | 分类目录 + 字幕联动 + 本地导入 | **28/28** |
| GUI 端到端 | 真实点击下载并校验 | 通过 |
| exe 启动 | 打包后启动验证 | 通过 |

---

## 诚实说明

**❌ 不绕过账号级限速。** 如果网盘是服务端按账号/IP 硬卡的限速，换什么客户端都没用——那是会员权益，不是技术问题。本工具不破解。

**❌ 没有「一键改浏览器默认下载器」。** Chrome、Edge、360、QQ 浏览器都是 Chromium 内核，**官方不提供修改默认下载器的接口**。任何声称能做到的方案都是改文件关联或装扩展，副作用大且难还原。本项目提供可逆的 `netboost://` 协议（默认关闭，取消即删除注册表项），但不会硬改你的浏览器。

**❌ 不是 BT/磁力客户端，不是视频解析器。**

**⚠️ 别把连接数拉满。** 对小站点开 64 连接属于滥用，可能被临时封 IP。默认 8 通常够用。

**⚠️ 请只下载你有权下载的内容。**

---

## 环境要求

- Windows 10/11
- exe 版无需 Python；源码运行需 Python 3.9+
- 零第三方依赖
- 许可证：MIT

---

# English

> **Breaks through per-connection throttling.**
> Paste a link → it detects what the file is → picks the right folder → you just confirm.

![Main window](https://raw.githubusercontent.com/lx67621956-create/netboost/main/docs/gui-screenshot.png)

## Downloads

| File | Purpose | Notes |
|---|---|---|
| `netboost-gui-windows-x64.exe` | **GUI (recommended)** | Double-click to run, no Python needed · 10.5 MB |
| `netboost-cli-windows-x64.exe` | Command line | For batch / automation · 7.5 MB |

Both are **single-file portable executables** — copy them to any Windows machine and run.

> First launch takes 1–3 seconds (single-file exes unpack their runtime), which is normal.

---

## Three-step workflow

```
Paste link  →  auto-detect type  →  confirm
```

Paste `...Interstellar.2014.2160p.mkv` and it immediately tells you:

> **▶ Video** | Basis: URL filename extension `.mkv`
> Save to: `C:\Users\you\Downloads\Video`

Press Enter to accept. Click another category to change the type — the folder follows instantly.

### Confirm dialog

![Confirm dialog](https://raw.githubusercontent.com/lx67621956-create/netboost/main/docs/confirm-screenshot.png)

Five auto-detected categories:

| Type | Extensions (examples) | Default folder |
|---|---|---|
| ▶ Video | mp4 mkv avi mov wmv rmvb ts… | `Downloads\Video` |
| ♪ Music | mp3 flac wav aac m4a ogg ape… | `Downloads\Music` |
| ▤ Document | doc docx xls xlsx ppt pptx pdf txt csv epub… | `Downloads\Document` |
| ⚙ Program | exe msi apk dmg zip rar 7z iso… | `Downloads\Program` |
| ■ Other | jpg png bin dat… | `Downloads\Other` |

Detection order: **filename extension → server Content-Type → URL path → fallback**.

### Settings: per-category folders

![Settings](https://raw.githubusercontent.com/lx67621956-create/netboost/main/docs/settings-screenshot.png)

### Browser integration (optional, off by default)

![Browser integration](https://raw.githubusercontent.com/lx67621956-create/netboost/main/docs/browser-screenshot.png)

---

## Features

**Multi-connection acceleration**
Starts at 8 connections with adaptive ramp-up that stops once adding more no longer helps. Local measurement: 257 KB/s single connection → 1.53 MB/s with 8 connections, a **6.1× speedup**.

**Auto-detects 5 file categories**
Knows whether a link is video, music, document, program, or other, and picks the matching folder — you just confirm.

**Subtitle pairing for video**
Attach subtitle URLs or local subtitle files; they're renamed to match the video and placed in the same folder for automatic player loading. Multi-language subtitles get a suffix (`.chs.srt` / `.eng.srt`) and don't overwrite each other. ASS is never mislabeled as SRT.

**Resumable downloads**
Re-adding the same URL after an interruption resumes from where it stopped. Size and ETag are compared before resuming, so a changed source triggers a clean restart — it never assembles a corrupt file.

**Optional rate limiting**
Enter `5M` or `800K` to cap total speed, shared across all connections.

---

## Test results

| Test | Coverage | Result |
|---|---|---|
| Engine | Speedup / integrity / resume / fallback / 404 / disconnect retry / pause-resume / small file / zero-byte / CJK filename / rate limit / MD5 | **12/12** |
| Detection & protocol | 5-category detection + auto folder + protocol reversibility | **30/30** |
| Categories & subtitles | Category folders + subtitle pairing + local import | **28/28** |
| GUI end-to-end | Real click-through download verified | pass |
| Packaged exe | Launch verification | pass |

---

## Honest limitations

**❌ Does not bypass account-level throttling.** If a cloud drive enforces server-side per-account/IP throttling, no client can change that — it's a paid-tier entitlement, not a technical problem. This tool does not crack it.

**❌ No "one-click browser default downloader replacement".** Chrome, Edge, 360, and QQ Browser are Chromium-based and **expose no official API for changing the default downloader**. Anything claiming to one-click replace them is really modifying file associations or installing an extension — heavy side effects, hard to undo. This project offers a reversible `netboost://` protocol (off by default, unchecking deletes the registry keys) but will not forcibly modify your browser.

**❌ Not a BitTorrent/magnet client, not a video-site ripper.**

**⚠️ Don't max out connections.** Opening 64 connections against a small site is abusive and may get your IP temporarily blocked. The default of 8 is usually enough.

**⚠️ Only download what you have the right to download.**

---

## Requirements

- Windows 10/11
- The exe needs no Python; source requires Python 3.9+
- Zero third-party dependencies
- License: MIT
