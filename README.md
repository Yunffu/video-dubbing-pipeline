# video-dubbing-pipeline

## Purpose

多语言视频译制 Pipeline：串联 ASR → 翻译 → TTS → 字幕处理 → LipSync → 视频合成，
并对各环节的方案做横向对比与量化评估（效果 / 耗时 / 显存 / 成本 / 失败率）。

## Status

- [x] 环境就绪（FFmpeg 9.1 @ `D:\tools\ffmpeg` / Docker 29.2.1 / RTX 4060 8GB）
- [x] 素材线闭环（4 母版 + 5 场景素材 / 20 条 case / SHA256 确定性重建）
- [x] 翻译层（可插拔 provider + 自动降级 + 19 项离线自测）
- [x] ASR（faster-whisper int8 CPU）/ TTS（edge-tts）/ 合成（时间轴回贴 + 变速 + 烧字幕）
- [x] 端到端跑通（`results/scenarios/*/final.mp4`，4 次真实 DeepSeek 调用）
- [x] 字幕擦除：OpenCV inpaint（方案 A）落地 + LaMa ONNX（方案 B）对照
- [x] 评测层（`eval/runner.py` 跑分 + 回归 diff + 人评工作台 + 成本账单）
- [x] 抗压与降级（7/7 故障注入 PASS：断网 / 401 / 429 / 5xx / 媒体损坏）
- [x] 接入层（凭据治理 / 白名单 / 限流 / 退避 / 熔断 / 审计日志）
- [ ] LipSync（**明确不做**，见 `docs/notes/15-技术报告.md` 的能力边界声明）
- [ ] 人评打分（工具已就绪，**等人打分**）

## Layout

- `docs/notes/`   学习笔记与调研（含自检表、帧图、选型结论、复盘记录）
- `pipeline/`     各环节实现（`contracts.py` 定义统一数据契约；`translate/` 是翻译层；
                  `gateway/` 是接入层；`inpaint/` 是字幕擦除引擎）
- `scripts/`      可执行脚本（自检 / 统一 / 选段 / 派生 / 取素材 / 装 case 集 / 翻译 / 跑分 / 场景）
- `eval/`         `runner.py`（跑分+回归）、`metrics.py`（指标口径）、`cost.py`（成本账单）、
                  `human_review/`（人评工作台）
- `spec/`         六环节标准 + `_fault.yaml`（每条阈值注明来源）
- `tests/`        不联网的自测（`python tests/test_translate.py` / `python tests/test_pricing.py`）
- `assets/`       `manifest.json`（来源+许可+SHA256）、`cases.json`（case 集）、`glossary.json`（术语表）、`SOURCES.md`（登记表）
- `assets/raw/`   母版（不入库，由脚本重建）/ `assets/derived/` 派生难例（不入库）

## 文档索引

| 文件 | 内容 |
|---|---|
| `docs/notes/05-业务标准与阈值.md` | 每条标准来自哪、谁拍板 |
| `docs/notes/06-八股速记.md` | 场景 → 知识点 → 答案骨架 → 本项目证据 |
| `docs/notes/09-抗压与失败归类.md` | 故障注入矩阵 + 失败四分类（含我们自己的 5 个 bug） |
| `docs/notes/10-选型结论与badcase清单.md` | 引擎/mask 粒度对照实验 + 选谁 + badcase |
| `docs/notes/12-复盘与迭代记录.md` | R1–R6 迭代记录（含一次结论反转的完整留痕） |
| `docs/notes/13-接入层与网关实践.md` | 网关五职责 + 实测延迟 + 熔断语义 |
| `docs/notes/14-成本口径与账单.md` | token→美元 口径 + 账单 + 降本该往哪儿使劲 |
| `docs/notes/15-技术报告.md` | 作品本体（简历/面试用） |
| `docs/notes/技术选型检索台账.md` | 每条技术主张 × 结论 × 证据 URL × 是否采用 |

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

# 6) 翻译层：看当前会用到哪些后端
python scripts/translate_cli.py --describe

# 7) 翻译层：离线跑通（不需要任何密钥）
python scripts/translate_cli.py --text "Welcome to the finals." --src en --tgt zh

# 8) 翻译层自测（不联网）
python tests/test_translate.py

# 9) 成本口径自测（不联网）
python tests/test_pricing.py

# 10) 成本账单（含反事实对比：本地栈 vs 托管 API）
python eval/cost.py --scan results/scenarios

# 11) 字幕擦除跑分（8 条 case，出 metrics.jsonl + table.md + badcases.md）
python eval/runner.py --case-set all --plan baseline

# 12) 人评工作台：出题 → （人打分）→ 反推阈值
python eval/human_review/make_items.py --cases all
python eval/human_review/build_packet.py build --items results/human_review/items/items.json \
       --out results/human_review/packet.json
python eval/human_review/build_packet.py summarize --packet results/human_review/packet.json \
       --reviews results/human_review/reviews.json --out results/human_review/thresholds.json

# 13) 抗压：故障注入（断网 / 401 / 429 / 5xx / 媒体损坏）
python scripts/fault_test.py --case fault_01 --inject dns_fail,timeout,429
```

> FFmpeg 需在 PATH 中（本机路径：`D:\tools\ffmpeg\ffmpeg-9.1-essentials_build\bin`）。

## 翻译层

设计要点：**只认协议不认厂商**，换模型只改配置，不改代码。

| 后端 | 配置 | 说明 |
|---|---|---|
| `deepseek` | `DEEPSEEK_API_KEY` | 默认 `https://api.deepseek.com/v1` + `deepseek-chat`（官方定价页当前只列 `deepseek-flash` / `deepseek-v4-pro`；旧名实测仍可调用，成本按 flash 价折算，见 `pipeline/pricing.py`） |
| `openai_compatible` | `OPENAI_API_KEY` / `OPENAI_BASE_URL` / `OPENAI_MODEL` | Kimi、通义、GLM、vLLM、Ollama 共用同一套实现 |
| `offline` | 无 | Argos-Translate → 术语表替换 → 原文透传（三级降级） |
| `auto`（默认） | — | DeepSeek → 自定义端点 → 离线，**任一失败自动降级下一级并留痕** |

密钥放在项目根目录 `.env`（不入库）：

```
DEEPSEEK_API_KEY=sk-...
TRANSLATE_GLOSSARY=assets/glossary.json
```

降级不会静默发生：每次降级都会写进 `stat.extra["attempts"]`、`stat.degraded` 和 `warnings`，
这是 JD 职责 3「评估稳定性」要的数据来源。

## Notes

见 `docs/notes/`。
