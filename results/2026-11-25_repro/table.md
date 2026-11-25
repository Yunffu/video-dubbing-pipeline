# 跑分结果 2026-11-25_repro

stage: `subtitle_erase` · spec: `spec/subtitle_erase.yaml` · 通过 **8/8**（其中 WARN 8 条：硬约束过了，只是没达到本档调优指标）

判定口径：硬约束（区外不误伤 / untouched≥60dB）不过 → **FAIL**；字幕区 PSNR/SSIM 按方案分档：A档 25dB/0.75、B档 30dB/0.90，未达本档 → **WARN**（不判 FAIL）。

| case | 难度 | 结果 | 档位 | 字幕区 PSNR | 基线 | Δ | SSIM | 区外Δ | A档 | B档 | 备注 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| M1_01__hard_sub_bottom | hard_sub_bottom | WARN | A | 15.87 | 10.25 | 5.62 | 0.765 | 0.0 | ❌✅ | ❌❌ |  |
| M1_01__hard_sub_face | hard_sub_face | WARN | A | 14.64 | 8.1 | 6.54 | 0.557 | 0.0 | ❌❌ | ❌❌ |  |
| M1_02__hard_sub_bottom | hard_sub_bottom | WARN | A | 21.93 | 7.24 | 14.69 | 0.765 | 0.0 | ❌✅ | ❌❌ |  |
| M1_02__hard_sub_face | hard_sub_face | WARN | A | 21.5 | 7.425 | 14.075 | 0.64 | 0.0 | ❌❌ | ❌❌ |  |
| M1_04__hard_sub_bottom | hard_sub_bottom | WARN | A | 16.03 | 10.67 | 5.36 | 0.404 | 0.0 | ❌❌ | ❌❌ |  |
| M1_04__hard_sub_face | hard_sub_face | WARN | A | 17.01 | 8.13 | 8.88 | 0.454 | 0.0 | ❌❌ | ❌❌ |  |
| M3_01__hard_sub_bottom | hard_sub_bottom | WARN | A | 19.075 | 8.75 | 10.325 | 0.826 | 0.0 | ❌✅ | ❌❌ |  |
| M3_01__hard_sub_face | hard_sub_face | WARN | A | 23.285 | 7.74 | 15.545 | 0.819 | 0.0 | ❌✅ | ❌❌ |  |
