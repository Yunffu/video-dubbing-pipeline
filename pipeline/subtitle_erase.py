"""字幕擦除（硬字幕 → 抹掉重绘）。

两种方案（对比表的两列）：
  A  opencv_telea / opencv_ns  —— 自研基线，零额外依赖，纯 CPU，**可解释**
  B  video-subtitle-remover     —— Apache-2.0，内置 STTN/LaMa/ProPainter/OpenCV，CPU 可跑

为什么先做 A：它是我们唯一能**用 GT 算指标**的方案（合成 GT 法：母版 + 烧录字幕 = 派生件，
母版就是 GT），先把指标口径和评测链路打通，B 方案再作为「效果更好但更重」的对照组接进来。

擦除区间的确定：复用 scripts/detect_hardsub.py 的横带边缘密度法找底部高密度带，
取该带上下各留一点余量作为字幕区（全宽）。这是「可解释的启发式」，不是黑盒模型。

指标口径（对应 spec/subtitle_erase.yaml）：
  - psnr_in_mask   字幕区内 PSNR —— 擦得干不干净（越高越好）
  - psnr_out_mask  区外 PSNR —— 有没有误伤（应≈原片）
  两套数：in_memory（修补算法本身，编码前）/ file（成片落盘后，含重编码损失）。
  **两者必须分开报**：mp4v 重编码本身就有损，混在一起会把编码损失误判成擦除失败。
"""
from __future__ import annotations

import subprocess
import time
from pathlib import Path

import cv2
import numpy as np

from .media import probe as media_probe

N_BANDS = 20
BOTTOM_FRACTION = 0.30
ABS_FLOOR = 800.0
REL_FACTOR = 2.0

# 落盘编码器：mp4v = MPEG-4 Part 2，通用但有损；ffv1 走 MKV 无损（用于纯算法评测）
LOSSLESS_FOURCC = "FFV1"


def band_densities(gray: np.ndarray) -> np.ndarray:
    """每条横带的拉普拉斯方差（边缘密度代理）。"""
    h = gray.shape[0]
    bh = h // N_BANDS
    out = np.zeros(N_BANDS)
    for k in range(N_BANDS):
        strip = gray[k * bh:(k + 1) * bh, :]
        if strip.size:
            out[k] = cv2.Laplacian(strip, cv2.CV_64F).var()
    return out


def _candidate_run(d: np.ndarray, med: float) -> tuple[int, int] | None:
    """在底部区域找「连续合格带」的整体范围。

    v1 的 bug：只取 argmax 那一带 → 双行字幕只擦到第二行，残留半屏字。
    现在取所有同时满足绝对下限与相对倍数的带，再取**包含峰值带的连续段**，
    避免把底部零散的高频带并进来（误伤）。
    """
    k_min = int((1 - BOTTOM_FRACTION) * N_BANDS)
    rel = d / max(med, 1.0)
    cand = [k for k in range(k_min, N_BANDS)
            if d[k] >= ABS_FLOOR and rel[k] >= REL_FACTOR]
    if not cand:
        return None
    k_best = max(cand, key=lambda k: d[k])
    runs, cur = [], [cand[0]]
    for k in cand[1:]:
        if k == cur[-1] + 1:
            cur.append(k)
        else:
            runs.append(cur)
            cur = [k]
    runs.append(cur)
    for run in runs:
        if k_best in run:
            return run[0], run[-1]
    return runs[0][0], runs[0][-1]


def detect_subtitle_band(video: Path, n_frames: int = 12) -> dict:
    """找硬字幕所在的横带。返回 {y0, y1, bands, hit_ratio, mean_ratio, found}。

    口径与 scripts/detect_hardsub.py 一致：底部 30% 区域找边缘密度高的带，
    要求同时满足绝对下限（ABS_FLOOR）与相对倍数（REL_FACTOR），避免暗帧假阳性。
    取「包含峰值带的连续合格段」作为字幕带（双行字幕要一起擦）。
    """
    cap = cv2.VideoCapture(str(video))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if total <= 0:
        cap.release()
        return {"found": False, "error": "读不到帧"}
    idxs = np.linspace(0, total - 1, min(n_frames, total)).astype(int)
    hits = []
    for i in idxs:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(i))
        ok, fr = cap.read()
        if not ok:
            continue
        gray = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY)
        d = band_densities(gray)
        med = float(np.median(d))
        run = _candidate_run(d, med)
        if run is None:
            continue
        hits.append((run, float(d[run[1]] / max(med, 1.0))))
    cap.release()
    if not hits:
        return {"found": False, "reason": "未检出稳定的底部高密度带"}
    h = None
    cap = cv2.VideoCapture(str(video))
    ok, fr = cap.read()
    if ok:
        h = fr.shape[0]
    cap.release()
    k_lo = min(r[0] for r, _ in hits)
    k_hi = max(r[1] for r, _ in hits)
    bh = (h or 720) // N_BANDS
    return {"found": True, "bands": [k_lo, k_hi],
            "y0": max(k_lo * bh - 4, 0), "y1": min((k_hi + 1) * bh + 4, h or 720),
            "hit_ratio": round(len(hits) / max(len(idxs), 1), 2),
            "mean_ratio": round(sum(v for _, v in hits) / len(hits), 2)}


def make_mask(h: int, w: int, y0: int, y1: int) -> np.ndarray:
    """字幕区掩膜（全宽横带）。评测路径与生产路径共用同一个函数，避免两套实现漂移。"""
    mask = np.zeros((h, w), dtype=np.uint8)
    mask[max(y0, 0):min(y1, h), :] = 255
    return mask


def make_mask_rect(h: int, w: int, x0: int, y0: int, x1: int, y1: int) -> np.ndarray:
    """字幕区掩膜（矩形）。人脸字幕不是全宽横带，必须按矩形擦。"""
    mask = np.zeros((h, w), dtype=np.uint8)
    mask[max(y0, 0):min(y1, h), max(x0, 0):min(x1, w)] = 255
    return mask


def detect_face_subtitle(video: Path, n_frames: int = 16) -> dict:
    """找压在脸上的字幕，返回带 "rect" 的 band 结构。

    和 detect_subtitle_band 返回同一套字段，这样擦除/评测链路
    不需要为「人脸字幕」单开一条路径。
    """
    from . import face_sub

    res = face_sub.detect(video, n_frames=n_frames)
    if not res.get("found"):
        return {"found": False, "reason": res.get("reason", "未检出人脸字幕"),
                "kind": "face", "detail": res}
    x0, y0, x1, y1 = res["rect"]
    return {"found": True, "kind": "face", "rect": [x0, y0, x1, y1],
            "face": res.get("face"), "detail": res}


def region_of(band: dict, w: int) -> tuple[int, int, int, int]:
    """把 band 结构统一成矩形 (x0, y0, x1, y1)。

    底部字幕 → 全宽横带；人脸字幕 → 检测出的矩形。
    """
    if band.get("rect"):
        x0, y0, x1, y1 = band["rect"]
        return int(x0), int(y0), int(x1), int(y1)
    return 0, int(band["y0"]), int(w), int(band["y1"])


def inpaint_frame(fr: np.ndarray, mask: np.ndarray, scheme: str = "opencv_telea") -> np.ndarray:
    """单帧修补。scheme: opencv_telea / opencv_ns。"""
    flag = cv2.INPAINT_TELEA if scheme == "opencv_telea" else cv2.INPAINT_NS
    return cv2.inpaint(fr, mask, 4, flag)


def _psnr(a: np.ndarray, b: np.ndarray, region=None) -> float:
    """PSNR（dB）。

    region 可以是 (y0, y1)（全宽行带）或 (x0, y0, x1, y1)（矩形，人脸字幕用）。
    """
    if region:
        if len(region) == 2:
            a = a[region[0]:region[1]]
            b = b[region[0]:region[1]]
        else:
            x0, y0, x1, y1 = region
            a = a[y0:y1, x0:x1]
            b = b[y0:y1, x0:x1]
    if a.size == 0:
        return 99.0
    mse = float(np.mean((a.astype(np.float64) - b.astype(np.float64)) ** 2))
    if mse <= 1e-9:
        return 99.0
    return round(10 * np.log10((255.0 ** 2) / mse), 2)


def erase_video(video: Path, out_path: Path, scheme: str = "opencv_telea",
                band: dict | None = None, n_frames: int = 12,
                gt: Path | None = None, lossless: bool = False,
                eval_frames: int = 12) -> dict:
    """把硬字幕带抹掉（逐帧流式处理，不缓存整片）。

    scheme: opencv_telea / opencv_ns（方案 A）
    gt: 母版路径（仅「合成 GT 法」的派生件有）。给了就算 in_memory 指标。
    lossless: 用 FFV1/MKV 无损落盘（评测用；成片仍建议 mp4v）
    返回 detail：band / scheme / 处理帧数 / 耗时 / 是否检出字幕 / PSNR（若给 gt）
    """
    t0 = time.time()

    if band is None:
        band = detect_subtitle_band(video, n_frames=n_frames)
    if not band.get("found"):
        return {"ok": False, "degraded": True,
                "error": band.get("reason") or band.get("error", "未检出字幕"),
                "band": band}

    fb = media_probe.ffmpeg_bin()
    if not fb:
        return {"ok": False, "degraded": True, "error": "找不到 ffmpeg", "band": band}

    cap = cv2.VideoCapture(str(video))
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    # 统一成矩形：底部字幕是全宽横带，人脸字幕是检测出的矩形
    x0, y0, x1, y1 = region_of(band, w)

    cap_gt = None
    if gt is not None:
        cap_gt = cv2.VideoCapture(str(gt))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    if lossless:
        out_path = out_path.with_suffix(".mkv")
        fourcc = cv2.VideoWriter_fourcc(*LOSSLESS_FOURCC)
    else:
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    vw = cv2.VideoWriter(str(out_path), fourcc, fps, (w, h))

    flag = cv2.INPAINT_TELEA if scheme == "opencv_telea" else cv2.INPAINT_NS
    mask = make_mask_rect(h, w, x0, y0, x1, y1)

    # 区外区域 = 掩膜补集。底部字幕时退化成上下两条横带（与旧口径一致），
    # 人脸字幕时多出左右两条竖带。
    out_regions = [(0, 0, w, y0), (0, y1, w, h)]
    if x0 > 0:
        out_regions.append((0, y0, x0, y1))
    if x1 < w:
        out_regions.append((x1, y0, w, y1))

    # 采样帧索引：均匀抽 eval_frames 帧算 PSNR（长片不必逐帧比）
    n_eval = 0
    if cap_gt is not None and total > 0:
        n_eval = min(eval_frames, total)
        eval_idx = set(np.linspace(0, total - 1, n_eval).astype(int).tolist())
    else:
        eval_idx = set()

    psnr_in, psnr_out = [], []
    idx = 0
    frames = 0
    while True:
        ok, fr = cap.read()
        if not ok:
            break
        out = cv2.inpaint(fr, mask, 4, flag)
        vw.write(out)
        frames += 1

        if idx in eval_idx and cap_gt is not None:
            cap_gt.set(cv2.CAP_PROP_POS_FRAMES, idx)
            okg, fg = cap_gt.read()
            if okg:
                psnr_in.append(_psnr(out, fg, (x0, y0, x1, y1)))
                for reg in out_regions:
                    psnr_out.append(_psnr(out, fg, reg))
        idx += 1

    cap.release()
    if cap_gt is not None:
        cap_gt.release()
    vw.release()

    if not frames:
        return {"ok": False, "degraded": True, "error": "没有读到帧", "band": band}

    res = {"ok": True, "degraded": False, "error": "",
           "scheme": scheme, "band": band, "frames": frames,
           "elapsed_ms": int((time.time() - t0) * 1000),
           "output": str(out_path),
           "mask_rows": [y0, y1], "mask_rect": [x0, y0, x1, y1],
           "mask_area_ratio": round(((x1 - x0) * (y1 - y0)) / max(w * h, 1), 4),
           "note": "自研基线：逐帧独立修复，无时序一致性（运动镜头会闪）"}
    if psnr_in:
        res["psnr_in_mask"] = round(sum(psnr_in) / len(psnr_in), 2)
        res["psnr_out_mask"] = round(sum(psnr_out) / len(psnr_out), 2)
        res["eval_frames"] = len(psnr_in)
        res["psnr_source"] = "in_memory（编码前，纯修补算法）"
    return res


def evaluate_erase(erased: Path, gt: Path, mask_rows: tuple[int, int],
                   n_frames: int = 12) -> dict:
    """评估**落盘文件**的擦除质量（合成 GT 法）。

    gt = 母版（无字幕）；erased = 擦除后的派生件。
    - 字幕区 PSNR：擦得干不干净（越高越好）
    - 区外 PSNR：有没有误伤（应≈原片，>40dB 视为无可见损失）

    注意：这里量到的是「文件」→ 包含重编码损失。想隔离算法本身请用
    erase_video(..., gt=...) 返回的 in_memory 指标。
    """
    cap_e = cv2.VideoCapture(str(erased))
    cap_g = cv2.VideoCapture(str(gt))
    n_e = int(cap_e.get(cv2.CAP_PROP_FRAME_COUNT))
    n_g = int(cap_g.get(cv2.CAP_PROP_FRAME_COUNT))
    n = min(n_e, n_g)
    if n <= 0:
        cap_e.release(); cap_g.release()
        return {"error": "读不到帧"}
    idxs = np.linspace(0, n - 1, min(n_frames, n)).astype(int)
    in_vals, out_vals = [], []
    for i in idxs:
        cap_e.set(cv2.CAP_PROP_POS_FRAMES, int(i))
        cap_g.set(cv2.CAP_PROP_POS_FRAMES, int(i))
        ok1, fe = cap_e.read()
        ok2, fg = cap_g.read()
        if not (ok1 and ok2):
            continue
        in_vals.append(_psnr(fe, fg, mask_rows))
        out_vals.append(_psnr(fe, fg, (0, mask_rows[0])))
        out_vals.append(_psnr(fe, fg, (mask_rows[1], fe.shape[0])))
    cap_e.release(); cap_g.release()
    if not in_vals:
        return {"error": "没有可比帧"}
    return {"psnr_in_mask": round(sum(in_vals) / len(in_vals), 2),
            "psnr_out_mask": round(sum(out_vals) / len(out_vals), 2),
            "frames_compared": len(in_vals),
            "threshold_hint": {"psnr_in_mask": 30, "psnr_out_mask": 40},
            "psnr_source": "file（落盘后，含重编码损失）",
            "source": "合成 GT 法（母版即 GT）"}
