# 素材来源登记表

> 机器可读版本：`assets/manifest.json`（来源 URL + SHA256 + 许可 + 切片 + 选段依据）
> 重建命令：`python scripts/fetch_assets.py` → 母版落 `assets/raw/`
> 派生难例：`python scripts/build_cases.py` → 落 `assets/derived/`，case 清单 `assets/cases.json`
> 审计表：`docs/notes/02-素材审计表.md`（规格自检 + 人脸校验，由 `scripts/audit_assets.py` 生成）
> 表中人脸数字的**唯一口径**：`python scripts/check_face.py assets/raw/<ID>.mp4`（默认 `--frames 24` 均匀采样），
> 与审计表、`manifest.json` 的 `desc` **完全一致**，可逐条复跑核对。

## 母版（GT 基准）

| ID | 定位 | 内容 | 许可 | 作者 / 署名 | 可否再分发 | 切片 | 人脸校验 |
|---|---|---|---|---|---|---|---|
| M1_04 | **GT** | NASA 宇航员单人访谈 1280×720 英文 | Public domain (NASA) | NASA Johnson | ✅ | 15–40s | 单人正脸 96%，多人帧 0%，脸高 88px → **PASS** |
| M3_01 | **GT / 低清** | 1982 年 NSA 演讲录像 654×480 英文 | Public domain（美国政府作品 / NSA） | Rear Admiral Grace Hopper / NSA | ✅ | 1740–1765s | 单人正脸 92%，多人帧 0%，脸高 105px → **PASS** |
| M1_02 | 侧脸难例 | 竖屏采访 1080×1920 | CC BY-SA 4.0 | RG72 | ✅（派生需同协议） | 20–45s | yaw −73°→−9°，单人正脸仅 29%，多人帧 0%，脸高 126px |
| M1_01 | 多人难例 | 户外多人对话 1920×1080 | CC BY 3.0 | Canal Madeira | ✅（需署名） | 0–25s | 多人帧占比 67%，单人正脸仅 8%，脸高 210px |

> **为什么按"定位"而不是"素材类型"编号**：素材先按「能否当 LipSync 基准」筛一遍，筛不上的不扔，转成难例母版。
> M1_02 侧脸、M1_01 多人，正是难例矩阵里最硬的两类（侧脸是 LipSync 主要失败区；多人字幕重叠是擦除的边界）。

**红线（不可破）**
- ❌ 不用 B 站站内视频做素材
- ❌ 不把许可不明的片段 commit 进仓库（例如本地 `_work/video-subtitle-remover/test/` 下的 demo，只做本地实验）
- ✅ 仓库只放：CC / 公共领域素材 + 由它们派生的难例；母版本身不入库（体积），由 `fetch_assets.py` + SHA256 确定性重建

## 派生难例（由母版生成，非独立素材）

| 类型 | 造法 | 验证什么 |
|---|---|---|
| `hard_sub_bottom` | FFmpeg 烧 ASS 大字硬字幕（画面下三分之一） | 字幕擦除 Inpainting 的常规场景，mask 内算 PSNR/SSIM |
| `hard_sub_face` | 字幕上移到人脸区域 | 擦除是否会伤及人脸 |
| `low_bitrate` | `-b:v 400k` 重编码 | 超分 SR 前后对比 |
| `noisy` | 叠加白噪（`anoisesrc` + `amix`） | ASR 识别率下降 |
| `mouth_occluded` | 半透明黑矩形压住画面中下中央 | LipSync 在嘴部遮挡下的崩坏 |

> 每个 case 的母版 SHA256 与用途见 `assets/cases.json`，保证「同一个 case 永远由同一份 GT 生成」。当前 **20 条 case**（4 母版 × 5 难例）。

## 待补（case 集扩到 8–10 条母版）

- M2 带硬字幕的现实素材 3–4 条（Wikimedia 需逐条看 CC 协议）
- M4 参考多语种音频 3 条（男 / 女 / 童声各一）
- 1080p 单人正脸一条（当前 GT 为 720p / 480p；LipSync 裁脸后统一 resize 96×96，影响可控）

## 已知环境约束（2026-11-25 实测）

**代理开启后**可达：`archive.org`、`huggingface.co`、`hf-mirror.com`、`modelscope.cn`、`raw.githubusercontent.com`、Edge-TTS 端点、DeepSeek/DashScope API。

**代理关闭时**不可达：`archive.org`、`huggingface.co`（超时）；Pexels/Pixabay 首页 403（需 API key）。

→ 因此**素材源仍以 Wikimedia Commons 为主**（其许可元数据规范、可程序化核验），archive.org 仅作补充（多为上传者自述许可，证据等级更低）；模型下载统一走 hf-mirror / ModelScope。

**导入素材的坑**
- 部分 Commons `.webm` 实为**播客音频 + 1fps 封面图**，没有真实视频流 → 必须先 `ffprobe` 确认存在视频流
- `r_frame_rate=1000/1` 是 VFR 标记，不是 1000fps
- `anoisesrc` 是源滤镜，不能直接 `-af`，要用 `-f lavfi -i anoisesrc` + `amix` 混音
- Commons 批量下载会 429 限流，需串行 + 间隔
- OpenCV 5 移除了 Haar 级联（`cv2.CascadeClassifier` 不存在），人脸检测改用 YuNet ONNX；且 dnn 读不了含中文的路径，需复制到纯 ASCII 临时目录
