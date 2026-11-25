# 素材来源登记表

> 机器可读版本：`assets/manifest.json`（来源 URL + SHA256 + 切片参数 + 许可）
> 重建命令：`python scripts/fetch_assets.py` → 母版落 `assets/raw/`
> 派生难例：`python scripts/build_cases.py` → 落 `assets/derived/`，case 清单 `assets/cases.json`

## 母版（GT 基准）

| ID | 用途 | 来源 | 许可 | 作者 / 署名 | 可否再分发 | 切片 |
|---|---|---|---|---|---|---|
| M1_01 | M1 户外对话片段 1080p 横屏 | [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:-3_O_Levezinho_encontra_o_amigo.webm) | CC BY 3.0 | Canal Madeira | ✅（需署名） | 0–25s |
| M1_02 | M1 竖屏采访片段 1080×1920 | [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:Marina_Smirnova_%C4%89i%C4%89eronas_en_la_muzeo_de_Grigorij_Rasputin.webm) | CC BY-SA 4.0 | RG72 | ✅（派生需同协议） | 20–45s |
| M3_01 | M3 1982 年老片 654×480 | [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:Grace_Hopper_-_Future_Possibilities_-_Data,_Hardware,_Software,_and_People.webm) | Public domain（美国政府作品 / NSA） | Rear Admiral Grace Hopper / NSA | ✅ | 1740–1765s |

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

> 每个 case 的母版 SHA256 与用途见 `assets/cases.json`，保证「同一个 case 永远由同一份 GT 生成」。

## 待补（case 集扩到 8–10 条）

- M2 带硬字幕的现实素材 3–4 条（Wikimedia 需逐条看 CC 协议）
- M4 参考多语种音频 3 条（男 / 女 / 童声各一）
- 单人正脸、嘴部全程可见的 M1 补充条（当前 M1_01 / M1_02 需人工确认嘴部可见性）

## 已知环境约束（2026-11-25 实测）

- `archive.org`、`huggingface.co` 在本机**不可达**（超时）→ 模型下载走 `hf-mirror.com` 或 ModelScope；素材来源因此锁定 Wikimedia Commons
- Pexels / Pixabay 首页返回 403，需 API key 才能走下载通道
- 导入 Commons 视频时注意：部分 `.webm` 实为**播客音频 + 封面图**（1 fps artwork），必须先 `ffprobe` 确认存在真实视频流
