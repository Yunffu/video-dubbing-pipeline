# video-dubbing-pipeline

## Purpose

多语言视频译制 Pipeline：串联 ASR → 翻译 → TTS → 字幕处理 → LipSync → 视频合成，
并对各环节的方案做横向对比与量化评估（效果 / 耗时 / 显存 / 成本 / 失败率）。

## Status

- [x] 环境就绪（FFmpeg 9.1 @ `D:\tools\ffmpeg` / Docker 29.2.1 / RTX 4060 8GB）
- [x] 素材自检脚本 + 派生难例生成脚本（`scripts/`）
- [ ] 素材就绪（case 集 + 合成 GT）
- [ ] 端到端跑通
- [ ] 三方向方案对比（LipSync / 字幕擦除 / 超分）
- [ ] 评测报告与 badcase 清单

## Layout

- `docs/notes/`   学习笔记与调研（含自检表）
- `scripts/`      可执行脚本（媒体自检、难例派生）
- `assets/`       素材与素材来源登记表（`SOURCES.md`）；`assets/derived/` 由脚本生成，不入库

## How to run

```bash
# 素材入库自检（输出 JSON + Markdown 自检表）
python scripts/probe_media.py <file.mp4> --json out.json --md out.md

# 从干净母版派生难例（母版即 GT）：底部硬字幕 / 压脸硬字幕 / 低码率 / 噪声
python scripts/make_derived.py <母版.mp4> --outdir assets/derived --name m1_01
```

> FFmpeg 需在 PATH 中（本机路径：`D:\tools\ffmpeg\ffmpeg-9.1-essentials_build\bin`）。

## Notes

见 `docs/notes/`。
