# video-dubbing-pipeline

## Purpose

多语言视频译制 Pipeline：串联 ASR → 翻译 → TTS → 字幕处理 → LipSync → 视频合成，
并对各环节的方案做横向对比与量化评估（效果 / 耗时 / 显存 / 成本 / 失败率）。

## Status

- [x] 环境就绪（FFmpeg 9.1 @ `D:\tools\ffmpeg` / Docker 29.2.1 / RTX 4060 8GB）
- [x] 素材线闭环（3 母版 / 15 条 case / SHA256 确定性重建）
- [ ] 端到端跑通
- [ ] 三方向方案对比（LipSync / 字幕擦除 / 超分）
- [ ] 评测报告与 badcase 清单

## Layout

- `docs/notes/`   学习笔记与调研（含自检表、帧图）
- `scripts/`      可执行脚本（自检 / 统一 / 选段 / 派生 / 取素材 / 装 case 集）
- `assets/`       `manifest.json`（来源+许可+SHA256）、`cases.json`（case 集）、`SOURCES.md`（登记表）
- `assets/raw/`   母版（不入库，由脚本重建）/ `assets/derived/` 派生难例（不入库）

## How to run

```bash
# 1) 重建母版：按 manifest.json 下载 → 校验 SHA256 → 规格统一（25fps/h264/aac 48k）
python scripts/fetch_assets.py

# 2) 生成 case 集（每条 case 记录其 GT 母版与对应指标）
python scripts/build_cases.py

# 3) 单个文件的入库自检（JSON + Markdown 表）
python scripts/probe_media.py <file.mp4> --json out.json --md out.md

# 4) 从母版派生难例（母版即 GT）
python scripts/make_derived.py <母版.mp4> --outdir assets/derived --name M1_01

# 5) 自动挑"有人声、画面不黑"的片段窗口
python scripts/scan_segment.py <源文件>
```

> FFmpeg 需在 PATH 中（本机路径：`D:\tools\ffmpeg\ffmpeg-9.1-essentials_build\bin`）。

## Notes

见 `docs/notes/`。
