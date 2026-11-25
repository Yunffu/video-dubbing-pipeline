#!/usr/bin/env python3
"""检测视频是否含**硬字幕**（烧录进画面的字幕）。

为什么需要它：JD 职责 1 的「字幕擦除与重绘」只对**已经有硬字幕**的源片有意义，
而且擦除流程的入口判断不能靠肉眼，得能在批量跑分时自动分流。

原理（可解释，不用黑盒模型）：
硬字幕是**压在画面底部的一块高密度文本带**，因此把画面切成 N 条横带，
比较每条带的边缘密度（Laplacian 方差）：
  1. 底部区域（BOTTOM_FRACTION 以内）存在一条**绝对密度很高**的带
     （文字笔画的边缘远多于自然画面）；
  2. 且该带的密度**显著高于整帧中位数**（不是"比它上面那条高"——
     字幕可能占很大垂直跨度，用相邻带做分母会自我抵消）。

两个阈值都必须满足，缺一不可：
  - 绝对下限：避免整帧过暗的视频被比值误判成有字幕；
  - 相对倍数：避免画面本身就布满细节的视频被误判。

用法:
    python scripts/detect_hardsub.py <video.mp4> [--frames 20] [--json out.json]
"""
import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

N_BANDS = 20
BOTTOM_FRACTION = 0.30   # 只在画面底部 30% 里找字幕带
ABS_FLOOR = 800.0        # 该带的绝对边缘密度下限
REL_FACTOR = 2.0         # 相对整帧中位数的倍数下限


def sample_frames(path: Path, n: int):
    cap = cv2.VideoCapture(str(path))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    frames = []
    if total > 0:
        for i in np.linspace(0, total - 1, min(n, total)).astype(int):
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(i))
            ok, fr = cap.read()
            if ok:
                frames.append((int(i), fr))
    cap.release()
    return frames, total, fps


def band_densities(gray: np.ndarray) -> np.ndarray:
    h = gray.shape[0]
    bh = h // N_BANDS
    out = np.zeros(N_BANDS)
    for k in range(N_BANDS):
        strip = gray[k * bh:(k + 1) * bh, :]
        if strip.size:
            out[k] = cv2.Laplacian(strip, cv2.CV_64F).var()
    return out


def analyze(path: Path, n_frames: int):
    frames, total, fps = sample_frames(path, n_frames)
    if not frames:
        return {"file": str(path), "error": "读不到帧"}
    h, w = frames[0][1].shape[:2]

    per_frame = []
    for idx, fr in frames:
        gray = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY)
        d = band_densities(gray)
        med = float(np.median(d))
        k_min = int((1 - BOTTOM_FRACTION) * N_BANDS)
        bottom = d[k_min:]
        k_best = int(k_min + int(np.argmax(bottom)))
        per_frame.append({"frame": idx,
                          "median": round(med, 1),
                          "best_band": k_best,
                          "best_density": round(float(d[k_best]), 1),
                          "ratio": round(float(d[k_best]) / max(med, 1.0), 2)})

    ratios = np.array([r["ratio"] for r in per_frame])
    bests = np.array([r["best_density"] for r in per_frame])
    med_ratio = float(np.median(ratios))
    med_best = float(np.median(bests))
    # 多数帧都满足才算硬字幕（避免单帧偶然的高对比）
    hit = sum(1 for r, b in zip(ratios, bests) if r >= REL_FACTOR and b >= ABS_FLOOR)
    frac = hit / len(per_frame)

    bands_hit = [per_frame[i]["best_band"] for i, (r, b) in enumerate(zip(ratios, bests))
                 if r >= REL_FACTOR and b >= ABS_FLOOR]
    band_span = (f"B{min(bands_hit)}-B{max(bands_hit)}"
                 f" (y≈{min(bands_hit) * h // N_BANDS}-{(max(bands_hit) + 1) * h // N_BANDS}px)"
                 if bands_hit else "-")

    if frac >= 0.5:
        verdict = "HARD_SUB"
        reason = (f"{frac:.0%} 的采样帧在底部找到高密度文本带"
                  f"（中位比值 {med_ratio:.2f}×，中位绝对密度 {med_best:.0f}，位置 {band_span}）")
    elif frac >= 0.2:
        verdict = "BORDERLINE"
        reason = (f"仅 {frac:.0%} 的采样帧命中底部高密度带"
                  f"（中位比值 {med_ratio:.2f}×，中位绝对密度 {med_best:.0f}），需人工确认")
    else:
        reason = (f"底部未见稳定高密度文本带"
                  f"（中位比值 {med_ratio:.2f}×，中位绝对密度 {med_best:.0f}，"
                  f"低于绝对下限 {ABS_FLOOR:.0f} 或相对倍数 {REL_FACTOR:.1f}×）")
        verdict = "SOFT_OR_NONE"

    return {"file": str(path), "resolution": f"{w}x{h}", "fps": round(fps, 2),
            "total_frames": total, "frames_sampled": len(per_frame),
            "n_bands": N_BANDS, "bottom_fraction": BOTTOM_FRACTION,
            "abs_floor": ABS_FLOOR, "rel_factor": REL_FACTOR,
            "median_ratio": round(med_ratio, 2),
            "median_best_density": round(med_best, 1),
            "frames_hit": hit, "hit_fraction": round(frac, 2),
            "band_span": band_span, "verdict": verdict, "reason": reason,
            "per_frame": per_frame}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--frames", type=int, default=20)
    ap.add_argument("--json")
    args = ap.parse_args()

    res = analyze(Path(args.video), args.frames)
    print(f"文件      : {res.get('file')}")
    print(f"规格      : {res.get('resolution')} @ {res.get('fps')}fps  "
          f"采样 {res.get('frames_sampled')}/{res.get('total_frames')} 帧")
    print(f"底部/整帧 中位比值 : {res.get('median_ratio')}   "
          f"中位绝对密度 : {res.get('median_best_density')}   "
          f"命中 {res.get('frames_hit')}/{res.get('frames_sampled')} 帧 ({res.get('hit_fraction')})")
    print(f"字幕带位置: {res.get('band_span')}")
    print(f"判定      : {res.get('verdict')}")
    print(f"依据      : {res.get('reason')}")
    if args.json:
        Path(args.json).write_text(json.dumps(res, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
        print(f"-> {args.json}")


if __name__ == "__main__":
    main()
