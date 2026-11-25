# 选型结论与 badcase 清单（阶段 2 收口）

> 对应 JD 职责 2「效果测试和方案对比」、职责 3「评估效果、耗时、成本及稳定性」。
> 本文只写**有数据支撑**的结论；没跑过的方案一律标「未在本机验证」，不写结论。
> 复现命令见每节末尾。

---

## 0. 一句话结论

**字幕擦除这一环，方案 A（自研 OpenCV inpaint）只能当基线，不能当交付方案。**
4 条 case 的字幕区 PSNR 落在 **19–30dB**、SSIM **0.50–0.82**，**没有一条达到
初版阈值（30dB / 0.90）**；且字幕不在底部时**完全失效**（4/4 条 face case 走降级）。
真正要上的是**带字幕检测的方案 B**（video-subtitle-remover，Apache-2.0），
它解决的是 A 解决不了的两件事：**逐文字框 mask**（不误伤画面）与**时序一致性**。

这不是「A 写得不好」，是**A 的方法论天花板**：全宽横带 inpaint 必然把
不相干列的像素横向拉过来涂 → 结构必然不像原图。

---

## 1. 方案 A：OpenCV inpaint（自研基线）

### 1.1 实现

- `pipeline/subtitle_erase.py`：检测（横带边缘密度法）+ 修补（`cv2.inpaint`，TELEA/NS）
- `scripts/erase_subtitles.py`：单条出片（落盘 mp4v，可选 FFV1/MKV 无损）
- `scripts/erase_sweep.py`：参数扫描（mask 来源 × 余量 × 算法），不落盘
- 指标：`eval/metrics.py` 的 `subtitle_erase_in_memory()`

### 1.2 检测口径（这里踩了两个坑，都写进代码注释了）

v1 只取「边缘密度最高的**一条**横带」→ 双行字幕只擦到第二行，**残留半屏字**，
in-mask PSNR 只有 10.9dB。v2 改成「取**包含峰值带的连续合格段**」→ 15–18 带一起擦，
PSNR 升到 26–30dB。**同一个模型，检测逻辑改一行，指标翻倍。**

### 1.3 指标口径（这个坑更值钱）

**PSNR 必须带 baseline 对照。** 派生件 = 母版 + 烧字幕 + libx264 重编码，
所以「派生件 vs 母版」本身就有编码损失。直接拿「擦除后 vs 母版」的绝对值当成绩，
会把编码损失误判成擦除失败。实测：

| 口径 | 字幕区 PSNR | 区外 PSNR | 说明 |
|---|---|---|---|
| baseline（派生件 vs 母版） | 10.4dB | 49.1dB | 擦除前的天花板 |
| after（擦除后 vs 母版，**编码前**） | 26.5dB | 49.1dB | 区外 Δ=0 → 没误伤 |
| after（落盘 mp4v 后再测） | 26.5dB | **19.6dB** | ← 第二次有损编码把整帧拉垮 |

> 最后一行是踩过的坑：一开始区外 PSNR 只有 19.5dB，以为「擦除误伤了画面」，
> 查下来是 mp4v 重编码的锅。**结论：评测必须在编码前做**（`subtitle_erase_in_memory`），
> 落盘只用于出成片。否则你会把编码损失当成算法问题去优化，方向全错。
> （另一个坑：无损 FFV1/MKV 一条 25s 720p 片子 580MB → 评测必须不落盘。）

### 1.4 实测结果（`eval/runner.py --stage subtitle_erase --frames 6`）

run_id `2026-11-25_eraseA_v2`，case 集 = `assets/cases.json` 全部 8 条 hard_sub 类：

| case | 类型 | 字幕区 PSNR | baseline | Δ | SSIM | 区外Δ | 判定 |
|---|---|---|---|---|---|---|---|
| M1_01__hard_sub_bottom | 底部字幕 | 29.87 | 10.04 | +19.83 | 0.797 | 0.0 | FAIL（差 0.13dB） |
| M1_02__hard_sub_bottom | 底部字幕 | 22.03 | 7.23 | +14.80 | 0.771 | 0.0 | FAIL |
| M1_04__hard_sub_bottom | 底部字幕 | 29.89 | 10.37 | +19.51 | 0.505 | 0.0 | FAIL |
| M3_01__hard_sub_bottom | 底部字幕 | 19.07 | 8.75 | +10.32 | 0.821 | 0.0 | FAIL |
| M1_01/02/04、M3_01__hard_sub_face | 压脸字幕 | — | — | — | — | — | DEGRADED（未检出） |

- **区外 Δ 恒为 0.0、mask 外像素 PSNR = 99dB** → 证明 inpaint **只改 mask 内像素**，
  「不误伤」这条硬约束在方案 A 上是**可证明**的，不是靠肉眼看。
- **4/4 条 face case 走降级**：检测器只扫底部 30% → 字幕在画面上部时找不到带 →
  按 spec「原样输出 + 记 degraded」，**不静默假装擦过**。

### 1.5 参数扫描发现的规律（`scripts/erase_sweep.py`，M1_04，16 组配置）

| 观察 | 数据 | 解释 |
|---|---|---|
| mask 边界**落在字上** → 崩 | oracle margin=0/4：18.7 / 20.1dB | 边界行含字形抗锯齿 → 被拉进填充区 |
| mask 边界**离字 ≥12 行** → 稳 | oracle margin=12/24：29.96 / 29.47dB | 边界取到干净背景行 |
| **余量不是越大越好** | heuristic margin=24（516–708）掉到 19.41dB | 下边界落到画面内容复杂处 → 拉进无关像素 |
| TELEA vs NS | 全网格差距 <0.5dB | 两者都是边缘扩散，差别在噪声纹理 |
| SSIM 恒 <0.9 | 0.43–0.51 | **结构上就不像原图**，阈值不可能过 |

> **可迁移的结论**：全宽横带 inpaint 的质量**强烈依赖 mask 边界落在画面的什么内容上**，
> 与字幕本身无关。这不是调参能救的 → 必须换成逐文字框 mask（方案 B 的做法）。

---

## 2. 方案 B：video-subtitle-remover（对照组，未在本机跑通）

| 项 | 结论 | 依据 |
|---|---|---|
| 存在性 | ✅ 真实，13912★，Apache-2.0 | github.com/YaoFANGUK/video-subtitle-remover（检索台账 §1） |
| 能力 | 内置 `--inpaint-mode {sttn-auto,sttn-det,lama,propainter,opencv}` | 官方 README |
| 运行模式 | README 明写支持 CUDA / CPU（无 GPU）/ DirectML / macOS | 官方 README |
| 指标代码 | `backend/inpaint/video/core/metrics.py` 有现成 PSNR/SSIM/VFID | 检索台账 §4 |
| **本机状态** | 🔶 **未跑通** | 依赖 paddleocr（字幕检测）+ 权重下载；本机 torch 是 CPU 版 |
| 若接入要改的 | ① 全图口径 → **mask 加权**（MFVSR 口径）；② `compare_ssim(multichannel=True)` 在新版 scikit-image 已移除 → 改 `channel_axis=-1` | 检索台账 §4 |

**为什么 B 值得接**：A 的两个死结（误伤画面、无时序一致性）B 都有对应解法
（PaddleOCR 逐文字框 mask；STTN/ProPainter 做时序传播）。
**为什么现在还没接**：它要的是「检测 + 权重 + 一套依赖」，和本项目「零额外依赖、可解释、
能算 GT 指标」的基线目标是两回事；接进来要先解决依赖隔离（独立 venv）与权重落地。

---

## 3. badcase 清单与归因

归因四分类（模型 / 素材 / 环境 / 代码）——**这四类处理方式完全不同**：

| case | 现象 | 归因 | 处理 |
|---|---|---|---|
| M1_01__hard_sub_bottom | 29.87dB，差 0.13dB 不到阈值 | **方法论**（全宽带） | 换方案 B；不是调参问题 |
| M1_02__hard_sub_bottom | 22.03dB | **方法论 + 素材**（背景复杂） | 同上 |
| M1_04__hard_sub_bottom | 29.89dB 但 SSIM 0.505 | **方法论**（结构不像） | 同上 |
| M3_01__hard_sub_bottom | 19.07dB | **素材**（底部内容复杂）+ 方法论 | 同上 |
| 4 条 hard_sub_face | 未检出 → 降级 | **方案边界**（只扫底部） | 必须换有检测器的方案 B |
| （开发期）检测器只取 argmax 带 | 残留半屏字 | **我们的代码 bug** | v2 已修；已写成注释与 sweep 脚本 |

> **第四类「代码有 bug」是我们自己的责任**，不能推给模型。
> 上面「检测器只取一条带」就是典型：同一条 case 从 10.9dB → 26.5dB。
> 写进报告比藏起来值钱 —— 面试时讲「我自己的检测器第一版有 bug，怎么定位到是
> 「mask 只覆盖半行字幕」的」比讲任何指标都有说服力。

---

## 4. 阈值现状（诚实版）

`spec/subtitle_erase.yaml` 里的 30dB / 0.90 是**我按行业常识填的初版（C 级）**，
不是业务判断。当前数据表明：

- **30dB**：方案 A 稳定达不到 → 阈值要么等方案 B 的数据来定，要么用户拍板「多大修补痕迹能接受」。
- **0.90 SSIM**：全宽 inpaint 结构上就不可能过 → 这个阈值对「整带修补」类方案不适用，
  对「逐文字框修补」才有意义。
- **区外 PSNR 不低于原片**：这条**已被证明可满足**（Δ=0.0，mask 外 99dB）→ 保留为硬约束。

**要用户拍板**：字幕擦除的「可接受」到底是多少 dB / 看不看得出来？
建议做法：我按方案 B 跑出数据 → 你盲看 A/B 两个成片打分 → 阈值取「你判不行」样本的分界处
（`docs/notes/05-业务标准与阈值.md` §4 的做法）。

---

## 5. 复现命令

```bash
cd video-dubbing-pipeline
python eval/runner.py --stage subtitle_erase --case-set all --frames 6 \
       --run-id <run_id>                      # 跑分 → results/<run_id>/
python eval/runner.py --stage subtitle_erase --case-set all \
       --baseline <上一轮 run_id>            # 回归 diff（只许变好）
python scripts/erase_sweep.py --case M1_04 --frames 6   # 参数扫描
python scripts/erase_subtitles.py --video assets/derived/M1_04_hard_sub_bottom.mp4 \
       --gt assets/raw/M1_04.mp4 --out _work/erase/out.mp4   # 出成片（落盘口径）
```

**数字口径纪律**：评测一律**编码前**（`subtitle_erase_in_memory`）；
落盘口径只在需要出成片时用，且必须和 baseline 成对报。
