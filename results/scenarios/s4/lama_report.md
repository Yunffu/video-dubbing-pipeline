# 方案B（LaMa ONNX）本机实测

- 模型：`D:\ClaudeProjects\AI应用开发与Agent工具学习\video-dubbing-pipeline\_work\models\lama_fp32.onnx`（208.0MB，Apache-2.0）
- session 加载：7843ms

| 归一化 | ms/帧(720p) | PSNR in | PSNR out | SSIM in |
|---|---|---|---|---|
| unit | 9521.7 | 25.16 | 99.0 | 0.8564 |
| imagenet | 8922.0 | 10.98 | 99.0 | 0.5635 |