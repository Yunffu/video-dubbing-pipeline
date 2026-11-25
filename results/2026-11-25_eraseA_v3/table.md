# 跑分结果 2026-11-25_eraseA_v3

stage: `subtitle_erase` · spec: `spec/subtitle_erase.yaml` · 通过 **0/8**

| case | 难度 | 结果 | 字幕区 PSNR | 基线 | Δ | SSIM | 区外Δ | 备注 |
|---|---|---|---|---|---|---|---|---|
| M1_01__hard_sub_bottom | hard_sub_bottom | FAIL | 29.87 | 10.043 | 19.827 | 0.797 | 0.0 | psnr_in_mask;ssim_in_mask |
| M1_01__hard_sub_face | hard_sub_face | DEGRADED | - | - | - | - | - | 未检出稳定的底部高密度带 |
| M1_02__hard_sub_bottom | hard_sub_bottom | FAIL | 22.028 | 7.225 | 14.803 | 0.771 | 0.0 | psnr_in_mask;ssim_in_mask |
| M1_02__hard_sub_face | hard_sub_face | DEGRADED | - | - | - | - | - | 未检出稳定的底部高密度带 |
| M1_04__hard_sub_bottom | hard_sub_bottom | FAIL | 29.887 | 10.373 | 19.514 | 0.505 | 0.0 | psnr_in_mask;ssim_in_mask |
| M1_04__hard_sub_face | hard_sub_face | DEGRADED | - | - | - | - | - | 未检出稳定的底部高密度带 |
| M3_01__hard_sub_bottom | hard_sub_bottom | FAIL | 19.072 | 8.752 | 10.32 | 0.821 | 0.0 | psnr_in_mask;ssim_in_mask |
| M3_01__hard_sub_face | hard_sub_face | DEGRADED | - | - | - | - | - | 未检出稳定的底部高密度带 |
