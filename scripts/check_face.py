#!/usr/bin/env python3
"""人脸/嘴部可用性客观校验（LipSync GT 的前置判据）。

在视频上均匀采样若干帧，用 OpenCV YuNet 检测人脸，输出：
  - 每帧人脸数、脸框尺寸、偏航角估计（由鼻尖与眼中点相对位置推算）
  - 汇总：单人正脸占比、最大脸高、是否够 LipSync 用

判定规则：
  单人正脸帧占比 ≥ 80% 且 最大脸高 ≥ 80px → PASS（可做 LipSync GT；
  阈值依据：LipSync 方案裁脸后统一 resize 到 96×96，源脸高 ≥80px 即有足够像素）
  检出人脸但脸太小/多人/多侧脸            → WARN
  基本检不到人脸                          → FAIL

依赖: models/face_detection_yunet_2023mar.onnx（opencv_zoo）
用法:
    python scripts/check_face.py <video.mp4> [--frames 24] [--json out.json]
"""
import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

MODEL = Path(__file__).resolve().parent.parent / "models" / "face_detection_yunet_2023mar.onnx"


def model_path() -> str:
    """OpenCV 的 dnn 读不了含非 ASCII 字符的路径，必要时复制到临时目录。"""
    p = MODEL
    try:
        p.resolve().as_posix().encode("ascii")
    except UnicodeEncodeError:
        import shutil
        import tempfile
        dst = Path(tempfile.gettempdir()) / "face_detection_yunet_2023mar.onnx"
        if not dst.exists() or dst.stat().st_size != p.stat().st_size:
            shutil.copyfile(p, dst)
        p = dst
    return str(p)


def sample_frames(path: Path, n: int):
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise SystemExit(f"打不开视频: {path}")
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 25
    if total <= 0:
        cap.release()
        raise SystemExit("读不到帧数（可能不是 CFR 视频）")
    want = {int(i * total / n) for i in range(n)}
    frames = []
    i = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if i in want:
            frames.append((i, frame))
        i += 1
    cap.release()
    return frames, total, fps


def landmarks_of(row) -> np.ndarray:
    """兼容 OpenCV 4/5 的 YuNet 输出布局，取出 5 个关键点。

    OpenCV 5: [x, y, w, h, lm0..lm9, score]（score 在末尾）
    OpenCV 4: [x, y, w, h, score, lm0..lm9]
    """
    arr = np.asarray(row, dtype=float).reshape(-1)
    if arr.size >= 15:
        return arr[4:14].reshape(5, 2)
    if arr.size >= 14:
        return arr[5:15].reshape(5, 2)
    raise SystemExit("无法从检测结果中解析关键点")


def yaw_deg(landmarks) -> float:
    """由 YuNet 关键点粗估偏航角：鼻尖相对眼中点的水平偏移 / 眼距。"""
    right_eye, left_eye, nose = landmarks[0], landmarks[1], landmarks[2]
    eye_mid_x = (right_eye[0] + left_eye[0]) / 2
    eye_dist = max(abs(left_eye[0] - right_eye[0]), 1e-6)
    return (nose[0] - eye_mid_x) / eye_dist * 90.0


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--frames", type=int, default=24)
    ap.add_argument("--json")
    args = ap.parse_args()

    if not MODEL.exists():
        raise SystemExit(f"缺模型 {MODEL}（从 opencv_zoo 下载 face_detection_yunet_2023mar.onnx）")

    path = Path(args.video)
    frames, total, fps = sample_frames(path, args.frames)
    rows = []
    for idx, frame in frames:
        h, w = frame.shape[:2]
        det = cv2.FaceDetectorYN_create(model_path(), "", (w, h), 0.6, 0.3, 5000)
        _, faces = det.detect(frame)
        n = int(len(faces)) if faces is not None else 0
        face_h = face_w = 0
        yaw = 0.0
        if n:
            f = max(faces, key=lambda b: b[2] * b[3])
            face_w, face_h = int(f[2]), int(f[3])
            yaw = yaw_deg(landmarks_of(f))
        rows.append({"frame": idx, "faces": n, "face_h": face_h, "face_w": face_w,
                     "yaw_deg": round(yaw, 1), "frame_h": h, "frame_w": w})

    n_sampled = len(rows)
    detected = sum(1 for r in rows if r["faces"] > 0)
    single_frontal = sum(1 for r in rows if r["faces"] == 1 and abs(r["yaw_deg"]) <= 30)
    frontal_ratio = single_frontal / n_sampled if n_sampled else 0
    max_face_h = max((r["face_h"] for r in rows), default=0)
    multi_ratio = sum(1 for r in rows if r["faces"] > 1) / n_sampled if n_sampled else 0

    if frontal_ratio >= 0.8 and max_face_h >= 80:
        verdict = "PASS"
    elif detected / n_sampled >= 0.3:
        verdict = "WARN"
    else:
        verdict = "FAIL"

    print(f"文件: {path.name}  采样 {n_sampled}/{total} 帧 @ {fps:.2f}fps")
    print(f"检出人脸帧: {detected}/{n_sampled}   多人帧占比: {multi_ratio*100:.0f}%")
    print(f"单人正脸帧占比(|yaw|≤30°): {frontal_ratio*100:.0f}%   最大脸高: {max_face_h}px")
    print(f"判定: {verdict}")

    if args.json:
        Path(args.json).write_text(json.dumps(
            {"file": str(path), "frames": total, "fps": fps, "rows": rows,
             "frontal_ratio": frontal_ratio, "max_face_h": max_face_h,
             "verdict": verdict}, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
