# 跑分结果 2026-11-25_erase_v2

stage: `subtitle_erase` · spec: `spec/subtitle_erase.yaml` · 通过 **8/8**（其中 WARN 8 条：硬约束过了，只是没达到本档调优指标）

判定口径：硬约束（区外不误伤 / untouched≥60dB）不过 → **FAIL**；字幕区 PSNR/SSIM 按方案分档：A档 25dB/0.75、B档 30dB/0.90，未达本档 → **WARN**（不判 FAIL）。

| case | 难度 | 结果 | 档位 | 字幕区 PSNR | 基线 | Δ | SSIM | 区外Δ | A档 | B档 | 备注 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| M1_01__hard_sub_bottom | hard_sub_bottom | WARN | A | 16.53 | 10.37 | 6.16 | 0.77 | 0.0 | ❌✅ | ❌❌ |  |
| M1_01__hard_sub_face | hard_sub_face | WARN | A | 17.1 | 8.0 | 9.1 | 0.648 | 0.0 | ❌❌ | ❌❌ |  |
| M1_02__hard_sub_bottom | hard_sub_bottom | WARN | A | 21.85 | 7.255 | 14.595 | 0.773 | 0.0 | ❌✅ | ❌❌ |  |
| M1_02__hard_sub_face | hard_sub_face | WARN | A | 21.4 | 7.47 | 13.93 | 0.663 | 0.0 | ❌❌ | ❌❌ |  |
| M1_04__hard_sub_bottom | hard_sub_bottom | WARN | A | 16.08 | 10.66 | 5.42 | 0.403 | 0.0 | ❌❌ | ❌❌ |  |
| M1_04__hard_sub_face | hard_sub_face | WARN | A | 17.11 | 8.14 | 8.97 | 0.459 | 0.0 | ❌❌ | ❌❌ |  |
| M3_01__hard_sub_bottom | hard_sub_bottom | WARN | A | 18.81 | 8.755 | 10.055 | 0.807 | 0.0 | ❌✅ | ❌❌ |  |
| M3_01__hard_sub_face | hard_sub_face | WARN | A | 22.74 | 7.735 | 15.005 | 0.81 | 0.0 | ❌✅ | ❌❌ |  |
