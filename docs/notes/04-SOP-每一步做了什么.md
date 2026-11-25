# 04 · SOP：每一步干了什么 / 改了什么 / 达到什么效果 / 你该检查什么

> 用途：让你能**独立复核**我这条工作线，而不是只能信我的结论。
> 复核入口：`git log --oneline --reverse`（10 个提交）→ 每个提交对应下面的一步。
> 原则：**每一步都必须留下可重跑的命令 + 可看的产物**，否则就是"我说的"。

---

## 总览

| 阶段 | 提交 | 一句话产出 | 你能自己重跑的命令 |
|---|---|---|---|
| 环境 | `96365be` | FFmpeg 能用了 | `ffmpeg -version` |
| 素材自检 | `96365be` | 一个媒体文件能被自动体检 | `python scripts/probe_media.py <file>` |
| 素材线闭环 | `e44939e` | 母版可按 SHA256 确定性重建 | `python scripts/fetch_assets.py` |
| 客观判定 | `2dd0944` | 「像不像正脸」用工具判，不靠我眼睛 | `python scripts/check_face.py assets/raw/M1_04.mp4` |
| 难例矩阵 | `2dd0944` | 20 条 case，每条带 GT 和指标 | `python scripts/build_cases.py` |
| 翻译层 | `f23a356` | 换模型只改配置，挂了自动降级 | `python tests/test_translate.py` |

---

## 步骤 1 · 环境：让 FFmpeg 真正可用

**干了什么**
- 你机器上其实**已经有** FFmpeg 9.1，但在 `D:\tools\ffmpeg\ffmpeg-9.1-essentials_build\bin`，**不在 PATH 里** → 所有 `ffmpeg` 命令都会 "not recognized"。

**改了什么**
- 把该目录写进**用户级 PATH**（用 `.ps1` 脚本写，因为 bash 里转义 `$env:` 会被吃掉）。
- 写脚本：`D:\ClaudeProjects\AI应用开发与Agent工具学习\_work\add_ffmpeg_path.ps1`。

**达到什么效果**
- `ffmpeg -version` → `ffmpeg version 9.1`；`ffprobe` 同版本。后续所有 FFmpeg 操作才有可能。

**你该检查什么**
```bash
ffmpeg -version | head -1        # 期望：ffmpeg version 9.1
ffprobe -version | head -1       # 期望：ffprobe version 9.1
```
> 注意：改的是**用户级** PATH，已经开着的终端不会自动生效 → **新开一个终端**再验。

**踩坑**：中途我试过 `winget install Gyan.FFmpeg`，被你打断（对，机器上已经有了，不该重复装）。

---

## 步骤 2 · 素材自检（`probe_media.py`）

**干了什么**
- 先要一个"体检表"，不然后面每个文件都靠肉眼看。

**改了什么**：新增 `scripts/probe_media.py`
- 用 `ffprobe` 输出 JSON：分辨率 / 帧率 / 视频编码 / 时长 / 音频编码 / 采样率 / 声道 / **音画时长差**
- 每项判 **PASS / FAIL / WARN / N/A**，同时写 JSON 和 Markdown 表

**达到什么效果**
- 产物：`docs/notes/probe_masters.json`、`docs/notes/probe_local_testvideos.json`、`docs/notes/00-ffprobe自检.md`
- 立刻暴露两个问题：M1_02 音量 mean **−37dB**（ASR 会听不清，必须 `loudnorm` 增益）；M1_02 是 **1080×1920 竖屏**

**你该检查什么**
```bash
python scripts/probe_media.py assets/raw/M1_02.mp4 --md /tmp/t.md
cat /tmp/t.md
```
看「音画时长差」这一列——差值大说明原片音轨和画面对不齐，是译制的大坑。

**踩坑**：Windows 终端默认 GBK，输出中文会乱码 → 所有脚本开头加 `sys.stdout.reconfigure(encoding="utf-8")`。

---

## 步骤 3 · 素材源定案：为什么是 Wikimedia Commons

**干了什么**
- 代理没开时逐个试了渠道可达性。

**改了什么**：无代码，产出是 `assets/manifest.json` + `assets/SOURCES.md` 的登记规范。

**达到什么效果**

| 渠道 | 无代理 | 有代理 | 结论 |
|---|---|---|---|
| Wikimedia Commons | ✅ | ✅ | **主源**：许可元数据是结构化的、可核验 |
| Internet Archive | ❌ 超时 | ✅ | 备选：多为用户**自述**许可，证据等级低 |
| Pexels / Pixabay | ❌ 403 | — | 需 API key，暂不用 |
| huggingface.co | ❌ 超时 | ✅ | 模型源走 hf-mirror / ModelScope |

**你该检查什么**：打开 `assets/SOURCES.md`，每条素材应能看到：许可名 + 许可链接 + 作者 + 原始文件链接。**没有"未知许可"的条目**。

**踩坑（重要）**：Commons 的 `.webm` 可能是**播客音频 + 1fps 封面图**，根本没有真实视频流 → 下载后才发现「没有视频轨」。**改成先 `ffprobe` 远程 URL 确认存在 `codec_type=video` 再下载**。

---

## 步骤 4 · 母版规范化 + 选段（`normalize.py` / `scan_segment.py` / `select_segment.py`）

**干了什么**
- 素材必须统一规格，否则后面每个指标都会被编码差异污染。

**改了什么**：新增 3 个脚本
- `normalize.py`：母版统一为 **H.264 / yuv420p / 25fps / AAC 48kHz 立体声 / 偶数分辨率 / faststart**
- `scan_segment.py`：滑窗 + `volumedetect`(mean dB) + `blackdetect` → 自动挑"有人声、画面不黑"的窗口
- `select_segment.py`：滑窗打分（单人正脸占比 → 脸高）→ 给出最优 `--start/--dur`

**达到什么效果**
- 4 条母版全部规范化；切片起点不再是拍脑袋：
  - M1_01 `start=0`、M1_02 `start=20`（跳过大音量差的片头）、M1_04 `start=15`、M3_01 `start=1740`（自动挑的最响窗口，mean −23.9dB）

**你该检查什么**
```bash
python scripts/probe_media.py assets/raw/M1_04.mp4 --md /tmp/m.md && cat /tmp/m.md
# 期望：1280x720 / 25 fps / h264 / aac / 48000 Hz / stereo
```

---

## 步骤 5 · 人脸校验：把「像不像正脸」从主观变客观（`check_face.py`）

**干了什么**
- **这是整个素材线的转折点**。之前的判断（"这条是单人正脸"）是我看帧图看的——面试时这句话不可证。

**改了什么**：新增 `scripts/check_face.py`
- 用 **YuNet ONNX**（OpenCV 官方 `opencv_zoo`）检测人脸
- 从 5 个关键点算**偏航角 yaw**：`(鼻尖x − 眼中点x) / 双眼间距 × 90°`
- 输出：单人正脸帧占比 / 多人帧占比 / 最大脸高 → **PASS/FAIL**

**达到什么效果（实测数字）**

> 口径：`python scripts/check_face.py assets/raw/<ID>.mp4`（默认 `--frames 24` 均匀采样），
> 与 `docs/notes/02-素材审计表.md`、`assets/manifest.json` 的 `desc` **完全一致**。

| 母版 | 单人正脸 | 多人帧 | 最大脸高 | 判定 |
|---|---|---|---|---|
| M1_04 NASA | **96%** | 0% | 88px | **PASS**（GT 主力） |
| M3_01 Grace Hopper | **92%** | 0% | 105px | **PASS**（低清 + SR） |
| M1_02 竖屏采访 | 29%（yaw −73°→−9°） | 0% | 126px | 侧脸难例 |
| M1_01 户外多人 | 8% | **67%** | 210px | 多人难例 |

**关键**：M1_04 单人正脸 96%、多人帧 0% → 客观证明它能当 LipSync 的 GT 基准。

**你该检查什么**
```bash
python scripts/check_face.py assets/raw/M1_04.mp4
python scripts/check_face.py assets/raw/M1_01.mp4
```
前者应 PASS，后者应多人占比高 → 两者**判定必须不同**，如果一样说明脚本没生效。

**踩坑（两个都值得记住）**
1. **OpenCV 5.0 已删掉 Haar 级联**：`cv2.CascadeClassifier` 属性不存在 → 必须换 YuNet。
2. **`cv2.dnn` 读不了含中文的路径**：仓库路径 `AI应用开发与Agent工具学习` 有中文 → ONNX 加载报 "Can't read ONNX file" → 解决：把模型复制到纯 ASCII 临时目录再用。
3. **YuNet 输出布局 OpenCV 4/5 不同**：v5 是 `[x,y,w,h, lm0..lm9, score]`（分数在**最后**），v4 是分数在第 5 位 → 写 `landmarks_of()` 两种都兼容。
4. **脸高阈值**：一开始定 100px，把 M1_04（85px）误判成 FAIL → 改为 **80px**，依据是 LipSync 裁脸后统一 resize 到 96×96，源脸高 ≥80px 够用。**阈值必须写清依据**，否则就是拍脑袋。

---

## 步骤 6 · 派生难例：5 类 × 4 母版 = 20 条 case

**干了什么**：母版 = GT 基准，派生件 = 输入。

**改了什么**：新增 `scripts/make_derived.py` + `scripts/build_cases.py`
- `make_derived.py` 合成 5 类难例，每类都写一个 `<name>_derived.json` 记录合成参数
- `build_cases.py` 遍历母版跑派生，装配 `assets/cases.json`

**达到什么效果**

| 派生类型 | 合成手法 | 模拟的业务棘手 |
|---|---|---|
| `hard_sub_bottom` | ASS 烧底部字幕 | 已有硬字幕 → 擦除重绘 |
| `hard_sub_face` | 字幕条**压到脸上** | 擦除不能伤脸 |
| `low_bitrate` | `-b:v 400k` | 低码率 → 超分 |
| `noisy` | `anoisesrc` + `amix` | 噪声打崩 ASR → 字幕错 |
| `mouth_occluded` | `drawbox` 遮嘴 | 嘴部遮挡 → LipSync 崩坏 |

**你该检查什么**
```bash
python scripts/probe_media.py assets/derived/M1_04_noisy.mp4 --md /tmp/n.md && cat /tmp/n.md
```
看音频波形/音量是否被拉噪、视频是否变糊 → 与派生类型对得上。

**踩坑**
- `anoisesrc=...:d=dur` 语法不对 → 改 `d=3600`
- `anoisesrc` 是**源滤镜**不能直接 `-af` → 必须 `-f lavfi -i anoisesrc=...` 再 `-filter_complex "[0:a][1:a]amix=inputs=2:duration=first:weights=1 1[a]"`
- `r_frame_rate = 1000/1` **不是 1000fps**，是 VFR 标记 → 别当 bug 修
- Commons 批量下载 **429 限流** → 串行 + 加间隔

**踩坑（补记，写这份 SOP 时才发现）**
- **`audit_assets.py` 原来用 `--frames 16`，而 `check_face.py` 默认 `--frames 24`** →
  同一个文件在审计表和命令行下算出**不同的正脸占比**（M1_04：94% vs 96%；M1_01：19% vs 8%）。
  这种"文档和命令对不上"在面试现场是致命的 → 已统一为 24，并写进 SOURCES.md 和审计表脚注。
  **教训：任何出现在报告里的数字，必须能指向一条可复跑的命令。**

---

## 步骤 7 · 确定性重建：素材不入库，但随时能重建（`fetch_assets.py`）

**干了什么**
- 母版 60–120MB，直接进 git 会把仓库撑爆，而且有再分发问题。

**改了什么**
- `assets/manifest.json`：URL + **SHA256** + 字节数 + 许可 + 切片参数 + **选段依据**
- `scripts/fetch_assets.py`：下载 → **校验 SHA256** → 规范化 → 写 `<id>.meta.json`
- `.gitignore` 加 `assets/raw/*.mp4`、`assets/derived/`

**达到什么效果**
- 仓库很轻，但**任何人 clone 下来跑一条命令就能得到完全一样的母版**（SHA256 相同）。
- 这就是记忆里那条「case 集与 GT 由**确定性构造器**生成而非收集」的落地。

**你该检查什么**
```bash
python scripts/fetch_assets.py          # 重跑应显示 SHA256 一致
git ls-files assets/raw/                # 期望：只有 .meta.json，没有 .mp4
```

**踩坑**：`fetch_assets.py` 报 `KeyError: 'role'` —— manifest 里字段叫 `case_role`，脚本里写的是 `role` → 改成 `a.get("role", a.get("case_role",""))`。

---

## 步骤 8 · 审计表：所有判定汇总成一张（`audit_assets.py`）

**改了什么**：新增 `scripts/audit_assets.py`，汇总规格自检 + 人脸校验 → `docs/notes/02-素材审计表.md`

**达到什么效果**：一页纸看完 4 条母版的全部判定 + 派生件清单 + 判定规则说明。

**踩坑**：脚本把「脸高」写进了「单人正脸」列、`face_h` 列空了 —— 两个值打在同一行 stdout，**正则只吃到一个** → 改成单条正则同时捕获两个。

---

## 步骤 9 · 翻译层：可插拔 + 自动降级（`f23a356`）

**干了什么**
- 翻译是这个项目里**唯一必须接外部 API** 的环节，也是「换个模型要不要改代码」的问题。

**改了什么**

| 文件 | 作用 |
|---|---|
| `pipeline/contracts.py` | 统一数据契约。`StageStat` 自带 `elapsed_ms / provider / model / degraded / error / extra` → **对比表由运行日志生成，不手工填** |
| `pipeline/translate/base.py` | provider 协议（`available()` + `translate()`）→ **只认协议不认厂商** |
| `pipeline/translate/openai_compatible.py` | 仅用标准库 `urllib` 实现 `/chat/completions`。**DeepSeek / Kimi / 通义 / GLM / vLLM / Ollama 共用这一套**，差异只有 `base_url + model + api_key` |
| `pipeline/translate/offline.py` | 离线降级三级：**Argos-Translate → 术语表替换 → 原文透传**；`offline_strict=1` 时不产出伪译文 |
| `pipeline/translate/registry.py` | `auto` 模式：DeepSeek → 自定义端点 → 离线，**任一失败自动降级下一级，并把失败原因写进 `stat.extra["attempts"]`** |
| `pipeline/config.py` | 全部走环境变量，**不硬编码任何密钥**；支持项目根目录 `.env` |
| `assets/glossary.json` | 35 条电竞译制术语表（离线替换与 prompt 注入共用） |
| `scripts/translate_cli.py` | 命令行：`--text` / `--segments` / `--describe` / `--json` |
| `tests/test_translate.py` | **19 项断言**，不联网、不需要密钥 |

**达到什么效果（已实跑）**
```
$ python tests/test_translate.py
[1] LLM 编号输出解析     6 PASS
[2] LLM 漏行 → 回退原文并告警   3 PASS
[3] LLM 失败 → 自动降级离线并留痕 4 PASS
[4] 离线术语表 / 严格模式   5 PASS
ALL PASS
```
```
$ python scripts/translate_cli.py --text "Welcome to the finals." --src en --tgt zh
后端: offline argos/glossary
降级: True
⚠ 离线降级：未命中 Argos 模型，译文为术语替换/原文透传，不可用于对外交付
  [0] Welcome to the 总决赛.
```

**你该检查什么**
```bash
python tests/test_translate.py                 # 19 PASS
python scripts/translate_cli.py --describe     # 看 provider 链
# 翻译层是否真的厂商无关：只在开头的说明注释里出现厂商名，不能有针对某厂商的分支判断
grep -in "deepseek\|moonshot\|qwen\|glm" pipeline/translate/openai_compatible.py
# 期望：输出全部落在文件头 1–5 行的 docstring 里；正文没有 if/elif 厂商分支
```

**踩坑（三个）**
1. **术语表语义**：我一开始以为 `TRANSLATE_GLOSSARY` 收内联 JSON，实际实现是**文件路径** → 改文档 + 补 `assets/glossary.json`。
2. **严格模式有 bug**：术语替换循环**原地修改** `s.text`，导致「是否命中术语表」永远判成真 → 改成先取 `original` 再替换。这个 bug 是我写完自测才发现的，**说明自测是必须的**。
3. **监工拦截「命令里带密钥」** → 所以密钥走 `.env` 文件，不走命令行（顺带避免进 shell history）。

---

## 步骤 10 · 文档层（这一层是给你检查用的）

| 文档 | 回答什么问题 |
|---|---|
| `docs/notes/00-ffprobe自检.md` | 怎么给媒体文件做体检 |
| `docs/notes/02-素材审计表.md` | 4 条母版的全部客观判定 |
| `docs/notes/03-业务场景-棘手问题-素材映射.md` | 业务场景是什么、棘手情况清单、素材逐条对应哪个问题、**诚实缺口** |
| `docs/notes/04-SOP-每一步做了什么.md` | **本文件**：每步干了什么 / 改了什么 / 效果 / 你该检查什么 |
| `assets/SOURCES.md` | 素材来源、许可、待补清单、环境约束 |
| `market_scan/B站视频译制岗-JD原文与语种场景.md` | JD 原文 + 语种场景推测（带证据等级） |
| `market_scan/B站视频译制岗-作战计划.md` | 6 天冲刺排期 + 每日进展日志 |

---

## 我建议你按这个顺序复核（每条 1 分钟内可完成）

| # | 命令 | 期望 | 对应本文件 |
|---|---|---|---|
| 1 | `git log --oneline --reverse` | 10 个提交 | 步骤 1–10 |
| 2 | `python tests/test_translate.py` | ALL PASS (19) | 步骤 9 |
| 3 | `python scripts/check_face.py assets/raw/M1_04.mp4` | PASS | 步骤 5 |
| 4 | `python scripts/check_face.py assets/raw/M1_01.mp4` | 多人占比高（≠ 上一条） | 步骤 5 |
| 5 | `git ls-files assets/raw/` | 只有 `.meta.json` | 步骤 7 |
| 6 | `git log -p \| grep -i "sk-"` | 只出现文档里的占位符 `sk-...`，**没有真实密钥** | 步骤 9 |
| 7 | 打开 `docs/notes/frames/M1_04.jpg` | 宇航员正脸，字幕不在脸上 | 步骤 5 |
| 8 | 打开 `assets/SOURCES.md` | 每条都有许可+作者+链接 | 步骤 3 |

---

## 下一步（等你复核后开始，按结对模式：我给规格你写核心）

| 阶段 | 环节 | 状态 |
|---|---|---|
| D3 | ASR（faster-whisper，权重走 hf-mirror） | ⬜ 待你写 |
| D3 | TTS（Edge-TTS，中英各一） | ⬜ 待你写 |
| D3 | 字幕（ASS，烧录 + 时间轴重对齐） | ⬜ 待你写 |
| D3 | LipSync（Wav2Lip，8GB 显存可行） | ⬜ 待你写 |
| D3 | 合成（FFmpeg 封装） | ⬜ 待你写 |
| D3 | 端到端跑通 1 条 → 出对比表 | ⬜ 依赖上面 5 步 |
| D4 | 三方向方案对比 + Dockerfile | ⬜ |
| D5 | 简历 + 评测报告 + badcase 清单 | ⬜ |
| D6 | 投递（**11-30 截止**） | ⬜ |

**我这边等你两件事**：
1. `video-dubbing-pipeline/.env` 里写好 `DEEPSEEK_API_KEY=sk-...`（你写，我不经手）
2. 复核完上面 8 条检查项，告诉我哪条不对
