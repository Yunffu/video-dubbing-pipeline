#!/usr/bin/env python3
"""滑窗扫描，挑选"单人正脸、脸够大"的最佳片段（LipSync GT 选段器）。

在源视频上按固定窗长滑动，对每个窗采样若干帧做 YuNet 人脸检测，
按 单人正脸占比 → 最大脸高 打分排序，给出最优窗口的 --start/--dur。

用法:
    python scripts/select_segment.py <源视频> [--win 25] [--step 5] [--samples 8] [--top 5]
"""
import argparse
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_face import model_path, landmarks_of, yaw_deg  # noqa: E402


def detect_window(path: Path, start: float, dur: float, samples: int, det):
    cap = cv2.VideoCapture(str(path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 25
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(start * fps))
    n_frames = int(dur * fps)
    idxs = set(int(i * n_frames / samples) for i in range(samples))
    rows = []
    i = 0
    while i < n_frames:
        ok, frame = cap.read()
        if not ok:
            break
        if i in idxs:
            h, w = frame.shape[:2]
            det.setInputSize((w, h))
            _, faces = det.detect(frame)
            n = int(len(faces)) if faces is not None else 0
            fh = 0
            yaw = 0.0
            if n:
                f = max(faces, key=lambda b: b[2] * b[3])
                fh = int(f[3])
                yaw = yaw_deg(landmarks_of(f))
            rows.append((n, fh, yaw))
        i += 1
    cap.release()
    if not rows:
        return None
    single_front = sum(1 for n, _, yaw in rows if n == 1 and abs(yaw) <= 30)
    detected = sum(1 for n, _, _ in rows if n > 0)
    frontal = single_front / len(rows)
    max_h = max(fh for _, fh, _ in rows)
    multi = sum(1 for n, _, _ in rows if n > 1) / len(rows)
    return {"frontal": frontal, "detected": detected / len(rows),
            "max_face_h": max_h, "multi": multi, "samples": len(rows)}


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("source")
    ap.add_argument("--win", type=float, default=25.0)
    ap.add_argument("--step", type=float, default=5.0)
    ap.add_argument("--samples", type=int, default=8)
    ap.add_argument("--top", type=int, default=5)
    args = ap.parse_args()

    path = Path(args.source)
    cap = cv2.VideoCapture(str(path))
    total = cap.get(cv2.CAP_PROP_FRAME_COUNT)
    fps = cap.get(cv2.CAP_PROP_FPS) or 25
    cap.release()
    duration = total / fps
    det = cv2.FaceDetectorYN_create(model_path(), "", (640, 480), 0.6, 0.3, 5000)

    results = []
    t = 0.0
    while t + args.win <= duration:
        r = detect_window(path, t, args.win, args.samples, det)
        if r:
            r["start"] = t
            results.append(r)
        t += args.step

    results.sort(key=lambda r: (r["frontal"], r["max_face_h"]))
    print(f"{path.name} 总时长 {duration:.1f}s  窗口 {args.win:.0f}s 步长 {args.step:.0f}s")
    print(f"{'start':>7} {'单人正脸':>9} {'检出':>6} {'多人帧':>7} {'最大脸高':>8}")
    for r in results[:args.top]:
        print(f"{r['start']:7.0f} {r['frontal']*100:8.0f}% {r['detected']*100:5.0f}% "
              f"{r['multi']*100:6.0f}% {r['max_face_h']:7d}px")
    if results:
        best = results[0]
        print(f"\n推荐: --start {best['start']:.0f} --dur {args.win:.0f}  "
              f"(单人正脸 {best['frontal']*100:.0f}%, 最大脸高 {best['max_face_h']}px)")


if __name__ == "__main__":
    main()
