# video-dubbing-pipeline

  ## Purpose

  多语言视频译制 Pipeline：串联 ASR → 翻译 → TTS → 字幕处理 → LipSync → 视频合成，
  并对各环节的方案做横向对比与量化评估（效果 / 耗时 / 显存 / 成本 / 失败率）。

  ## Status

  - [ ] 环境就绪（FFmpeg / Docker / GPU）
  - [ ] 素材就绪（case 集 + 合成 GT）
  - [ ] 端到端跑通
  - [ ] 三方向方案对比（LipSync / 字幕擦除 / 超分）
  - [ ] 评测报告与 badcase 清单

  ## Layout

  - `docs/notes/`   学习笔记与调研
  - `scripts/`      可执行脚本（媒体自检等）
  - `assets/`       素材与素材来源登记表（`SOURCES.md`）

  ## How to run

  待补（D3 端到端跑通后填写真实命令）。

  ## Notes

  见 `docs/notes/`。