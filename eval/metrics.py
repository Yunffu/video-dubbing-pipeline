"""评测指标（零额外依赖，可解释）。

为什么自己实现而不是装库：
  - skimage / Levenshtein / rapidfuzz 本机都没有，装库对离线复现有额外风险；
  - 面试时能说清「SSIM 的公式、CER 的 DP」比说「我 import 了 sklearn」值钱；
  - 口径必须唯一且可复跑 —— 自己实现最容易锁死口径。

口径纪律（重要）：
  **PSNR 必须带 baseline 对照。**
  派生件 = 母版 + 烧录字幕 + libx264 重编码 → 即使什么都没做，
  派生件与母版的 PSNR 也只有 ~20dB（编码损失）。
  直接拿「擦除后 vs 母版」的绝对值当成绩，会把编码损失误判成擦除失败。
  所以每个数字都成对给：baseline（擦除前）→ after（擦除后）→ delta。
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

# ---------------------------------------------------------------- 文本指标


def levenshtein(a: str, b: str) -> int:
    """编辑距离（滚动数组 DP）。"""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def cer(ref: str, hyp: str) -> float:
    """字符错误率 = 编辑距离 / 参考长度（参考为空时返回 0 表示不判）。"""
    ref = (ref or "").strip()
    hyp = (hyp or "").strip()
    if not ref:
        return 0.0
    return round(levenshtein(ref, hyp) / len(ref), 4)


def wer(ref: str, hyp: str) -> float:
    """词错误率（按空白切词）。"""
    r = (ref or "").split()
    h = (hyp or "").split()
    if not r:
        return 0.0
    return round(levenshtein(" ".join(r), " ".join(h)) / len(r), 4)


# ---------------------------------------------------------------- 图像指标


def _as_rect(mask_region, w: int) -> tuple[int, int, int, int]:
    """把掩膜描述统一成矩形 (x0, y0, x1, y1)。

    (y0, y1) → 全宽横带（底部字幕）；(x0, y0, x1, y1) → 矩形（人脸字幕）。
    """
    if len(mask_region) == 4:
        return tuple(int(v) for v in mask_region)
    y0, y1 = int(mask_region[0]), int(mask_region[1])
    return 0, y0, int(w), y1


def _crop(img: np.ndarray, region) -> np.ndarray:
    """按区域裁剪。region 可以是 (y0,y1)（全宽行带）或 (x0,y0,x1,y1)（矩形）。"""
    if region is None:
        return img
    if len(region) == 2:
        return img[region[0]:region[1]]
    x0, y0, x1, y1 = region
    return img[y0:y1, x0:x1]


def psnr(a: np.ndarray, b: np.ndarray, region=None) -> float:
    """PSNR(dB)。

    region 可以是 (y0,y1)（只算该行区间，底部字幕用）
    或 (x0,y0,x1,y1)（矩形，人脸字幕用）。
    """
    if region:
        a = _crop(a, region)
        b = _crop(b, region)
    if a.size == 0:
        return 99.0
    mse = float(np.mean((a.astype(np.float64) - b.astype(np.float64)) ** 2))
    if mse <= 1e-9:
        return 99.0
    return round(10 * np.log10((255.0 ** 2) / mse), 2)


def ssim(a: np.ndarray, b: np.ndarray, region=None) -> float:
    """SSIM（灰度 + 11x11 高斯窗 sigma=1.5，标准 Wang et al. 2004 口径）。

    a/b 可以是彩色图 → 转灰度后计算（我们只关心结构相似，不关心色偏）。
    """
    if a.ndim == 3:
        a = cv2.cvtColor(a, cv2.COLOR_BGR2GRAY)
    if b.ndim == 3:
        b = cv2.cvtColor(b, cv2.COLOR_BGR2GRAY)
    if region:
        a = _crop(a, region)
        b = _crop(b, region)
    if a.size == 0:
        return 1.0
    a = a.astype(np.float64)
    b = b.astype(np.float64)
    C1, C2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    mu_a = cv2.GaussianBlur(a, (11, 11), 1.5)
    mu_b = cv2.GaussianBlur(b, (11, 11), 1.5)
    mu_a2, mu_b2, mu_ab = mu_a ** 2, mu_b ** 2, mu_a * mu_b
    sa = cv2.GaussianBlur(a * a, (11, 11), 1.5) - mu_a2
    sb = cv2.GaussianBlur(b * b, (11, 11), 1.5) - mu_b2
    sab = cv2.GaussianBlur(a * b, (11, 11), 1.5) - mu_ab
    num = (2 * mu_ab + C1) * (2 * sab + C2)
    den = (mu_a2 + mu_b2 + C1) * (sa + sb + C2)
    return round(float(np.mean(num / np.maximum(den, 1e-12))), 4)


def _agg(vals: list[float]) -> dict:
    """一组数的稳健统计：主指标取**中位数**（原因见 subtitle_erase_in_memory）。"""
    if not vals:
        return {"median": None, "mean": None, "p25": None, "p75": None,
                "min": None, "max": None, "n": 0}
    a = np.array(vals, dtype=np.float64)
    return {"median": round(float(np.median(a)), 3),
            "mean": round(float(a.mean()), 3),
            "p25": round(float(np.percentile(a, 25)), 3),
            "p75": round(float(np.percentile(a, 75)), 3),
            "min": round(float(a.min()), 3),
            "max": round(float(a.max()), 3),
            "n": len(a)}


def subtitle_erase_in_memory(derived: Path, master: Path,
                             mask_rows: tuple[int, int],
                             scheme: str = "opencv_telea",
                             n_frames: int = 24) -> dict:
    """字幕擦除指标（**不落盘**，纯内存口径）。

    为什么不在评测时落盘：
      - 无损 FFV1/MKV 一条 25s 720p 片子要 ~580MB，20 条 case 直接把盘撑爆；
      - 落盘会掺进编码损失，把「修补算法好不好」和「编码器损不损」混在一起
        （我们踩过：mp4v 重编码让区外 PSNR 从 49dB 掉到 19.5dB，一度以为误伤）。
    所以评测口径 = 编码前；成片落盘只在 scripts/erase_subtitles.py 里做。

    **主指标为什么取中位数（v2 修正，重要）**：
      v1 用「采样帧的均值」，结果随采样数剧烈漂移（同一条 case M1_04：
      4 帧→16.07dB、6 帧→29.89dB、8 帧→26.46dB、12 帧→16.09dB）。
      原因查清了：**素材里有近黑帧**（母版整帧均亮度≈0），黑帧上「字幕擦除」
      = 擦完还是纯黑 → 与母版逐像素相同 → PSNR=99dB。采到黑帧就拉高均值。
      → 修正：① 近黑帧（母版帧均亮度<8）单独计数、不进主指标；
             ② 主指标用**中位数**；③ 同时报 p25/p75/样本数。
      这是本项目「口径纪律」的活例子：**指标口径错了，结论会反过来**
      （按 v1 的 29.89dB，方案 A 看起来「差一点就过 30dB 阈值」；
        按修正后的中位数，它其实差得很远）。
    """
    from pipeline import subtitle_erase as se  # 局部导入，避免与 pipeline 形成环

    cd = cv2.VideoCapture(str(derived))
    cm = cv2.VideoCapture(str(master))
    total = int(cd.get(cv2.CAP_PROP_FRAME_COUNT))
    if total <= 0:
        cd.release(); cm.release()
        return {"error": "读不到派生件帧数"}
    idxs = np.linspace(0, total - 1, min(n_frames, total)).astype(int).tolist()

    base_in, base_out = [], []
    after_in, after_out, ssim_in = [], [], []
    untouched = []
    black_frames = 0

    for i in idxs:
        cd.set(cv2.CAP_PROP_POS_FRAMES, int(i))
        cm.set(cv2.CAP_PROP_POS_FRAMES, int(i))
        ok1, a = cd.read()
        ok2, b = cm.read()
        if not (ok1 and ok2):
            continue
        h = a.shape[0]
        w = a.shape[1]
        # 近黑帧：整帧几乎无内容，PSNR 无意义（擦完还是黑 → 恒 99dB）
        if float(np.mean(b)) < 8.0:
            black_frames += 1
            continue
        rect = _as_rect(mask_rows, w)
        mask = se.make_mask_rect(h, w, *rect)
        out = se.inpaint_frame(a, mask, scheme)

        # 区外 = 掩膜补集。行带时是上下两条；矩形时多出左右两条。
        y0, y1 = rect[1], rect[3]
        out_regs = [(0, 0, w, y0), (0, y1, w, h)]
        if rect[0] > 0:
            out_regs.append((0, y0, rect[0], y1))
        if rect[2] < w:
            out_regs.append((rect[2], y0, w, y1))

        base_in.append(psnr(a, b, rect))
        after_in.append(psnr(out, b, rect))
        ssim_in.append(ssim(out, b, rect))
        for reg in out_regs:
            base_out.append(psnr(a, b, reg))
            after_out.append(psnr(out, b, reg))
            untouched.append(psnr(out, a, reg))

    cd.release(); cm.release()
    if not base_in:
        return {"error": "没有可比帧（非黑帧）"}

    agg_in = _agg(after_in)
    agg_base = _agg(base_in)
    agg_out = _agg(after_out)
    agg_base_out = _agg(base_out)
    return {
        "frames_compared": len(base_in),
        "frames_black_skipped": black_frames,
        "mask_rows": [y0, y1],
        "scheme": scheme,
        "baseline": {"psnr_in_mask": agg_base, "psnr_out_mask": agg_base_out},
        "after": {"psnr_in_mask": agg_in, "psnr_out_mask": agg_out,
                  "ssim_in_mask": _agg(ssim_in)},
        "delta": {"psnr_in_mask": round(agg_in["median"] - agg_base["median"], 3),
                  "psnr_out_mask": round(agg_out["median"] - agg_base_out["median"], 3)},
        "untouched_out_mask": _agg(untouched)["median"],
        "psnr_source": "in_memory（编码前，纯修补算法）",
        "agg_note": "主指标=中位数；近黑帧不计入（见函数 docstring）",
        "source": "合成 GT 法（母版即 GT）",
    }


def _read_frames(path: Path, idxs: list[int]):
    cap = cv2.VideoCapture(str(path))
    out = []
    for i in idxs:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(i))
        ok, fr = cap.read()
        if ok:
            out.append((i, fr))
    cap.release()
    return out


def subtitle_erase_metrics(derived: Path, erased: Path, master: Path,
                            mask_rows: tuple[int, int],
                            n_frames: int = 12) -> dict:
    """字幕擦除的完整指标（合成 GT 法）。

    derived = 派生件（带硬字幕，是擦除的输入）→ 母版 + 烧字幕 + 重编码
    erased  = 擦除后的成片
    master  = 母版 = GT

    给出三组数：
      baseline  derived vs master  ← 派生件本身的编码损失（任何方案都逃不掉的天花板）
      after     erased  vs master  ← 擦除后与母版的差距
      delta     after - baseline   ← 擦除带来的净收益（字幕区应显著为正）
      untouched erased  vs derived (区外) ← 是否误伤区外像素（应≈99dB）
    """
    cap = cv2.VideoCapture(str(derived))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.release()
    if total <= 0:
        return {"error": "读不到派生件帧数"}
    idxs = np.linspace(0, total - 1, min(n_frames, total)).astype(int).tolist()

    base_in, base_out = [], []
    after_in, after_out = [], []
    ssim_in, ssim_out = [], []
    untouched_out = []

    fr_d = _read_frames(derived, idxs)
    fr_e = _read_frames(erased, idxs)
    fr_m = _read_frames(master, idxs)
    m_by_idx = {i: fr for i, fr in fr_m}
    e_by_idx = {i: fr for i, fr in fr_e}

    for i, fd in fr_d:
        fg = m_by_idx.get(i)
        fe = e_by_idx.get(i)
        if fg is None or fe is None:
            continue
        h, w = fd.shape[0], fd.shape[1]
        rect = _as_rect(mask_rows, w)
        y0, y1 = rect[1], rect[3]
        out_regs = [(0, 0, w, y0), (0, y1, w, h)]
        if rect[0] > 0:
            out_regs.append((0, y0, rect[0], y1))
        if rect[2] < w:
            out_regs.append((rect[2], y0, w, y1))

        base_in.append(psnr(fd, fg, rect))
        after_in.append(psnr(fe, fg, rect))
        ssim_in.append(ssim(fe, fg, rect))
        for reg in out_regs:
            base_out.append(psnr(fd, fg, reg))
            after_out.append(psnr(fe, fg, reg))
            ssim_out.append(ssim(fe, fg, reg))
            untouched_out.append(psnr(fe, fd, reg))

    if not base_in:
        return {"error": "没有可比帧"}

    def avg(v):
        return round(sum(v) / len(v), 3) if v else None

    return {
        "frames_compared": len(base_in),
        "mask_rows": list(mask_rows),
        "mask_rect": list(rect) if rect else None,
        "baseline": {"psnr_in_mask": avg(base_in), "psnr_out_mask": avg(base_out),
                     "note": "派生件 vs 母版 = 派生件自身的重编码损失（擦除前的天花板）"},
        "after": {"psnr_in_mask": avg(after_in), "psnr_out_mask": avg(after_out),
                  "ssim_in_mask": avg(ssim_in), "ssim_out_mask": avg(ssim_out),
                  "note": "擦除后 vs 母版"},
        "delta": {"psnr_in_mask": round(avg(after_in) - avg(base_in), 3),
                  "psnr_out_mask": round(avg(after_out) - avg(base_out), 3),
                  "note": "净收益：字幕区应为正；区外接近 0（不误伤）"},
        "untouched_out_mask": {"psnr": avg(untouched_out),
                               "note": "擦除后 vs 派生件 的区外区域，应≈99dB（inpaint 不改 mask 外像素）"},
        "threshold_hint": {"psnr_in_mask": 30, "psnr_out_mask": 40,
                           "ssim_in_mask": 0.9},
        "source": "合成 GT 法（母版即 GT）",
    }
