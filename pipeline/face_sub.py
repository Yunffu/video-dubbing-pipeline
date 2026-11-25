"""人脸字幕（压脸硬字幕）的检测 —— 方案 A 的前置。

为什么单独一个模块：
    底部硬字幕用「画面底部高密度横带」就能定位（见 subtitle_erase.detect_subtitle_band）。
    但人脸字幕烧在画面中部、盖在嘴上，底部那条路完全找不到
    （实测 4 个人脸 case 全部报「未检出稳定的底部高密度带」）。

思路（可解释，不用黑盒模型）：
    1. YuNet 检出人脸框 → 知道脸在哪；
    2. 字幕压住嘴/下巴 → 只在脸下半部附近找；
    3. 字幕是密集笔画，边缘密度远高于皮肤：
       先在**脸的横向范围内**做行方向边缘密度剖面 → 找文字行；
    4. 再在文字行内做列方向剖面 → 找文字左右边界（字幕通常比脸宽）。

三条否决判据（缺一个都不算「压脸字幕」）：
    - 横向跨度 ≥ 1.5 倍脸宽（嘴、牙齿、下巴只占 ~0.6 倍脸宽）
    - 文字行必须**跨过嘴**（嘴上方的衣服/背景纹理不算）
    - 文字块高度 ≥ 0.30 倍脸高（实测压脸字幕 ≈0.55 倍脸高）

准确性怎么证明：
    用「合成 GT 法」造 oracle —— 派生件 = 母版 + 烧录字幕，两者相减
    （多帧中位数消编码噪声）就是字幕像素的精确位置。
    检测器矩形与 oracle 矩形求 IoU，这个数可复现，是客观指标而非「看着对」。
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

# 文字行判定：行边缘密度 ≥ 脸区中位密度 × ROW_RATIO 且 ≥ 绝对下限
ROW_RATIO = 1.8
ROW_ABS_FLOOR = 120.0
# 列扩展：文字行内向左右找密度连续的列，≥ 背景(p10) × COL_RATIO
COL_RATIO = 4.0
COL_ABS_FLOOR = 1500.0
COL_MAX_GAP = 4
# 文字横向跨度下限（×脸宽）：字幕比脸宽得多，嘴/下巴这些面部特征不会
MIN_TEXT_SPAN_RATIO = 1.5
# 文字块高度下限（×脸高）：实测压脸字幕 ≈0.55×脸高
MIN_TEXT_HEIGHT_RATIO = 0.30

MODEL = Path(__file__).resolve().parent.parent / "models" / "face_detection_yunet_2023mar.onnx"


def model_path() -> str:
    """OpenCV 的 dnn 读不了含非 ASCII 字符的路径，必要时复制到临时目录。"""
    p = MODEL
    if not p.exists():
        return ""
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


def _landmarks_of(row) -> np.ndarray:
    """兼容 OpenCV 4/5 的 YuNet 输出布局，取出 5 个关键点。

    OpenCV 5: [x, y, w, h, lm0..lm9, score]（score 在末尾）
    OpenCV 4: [x, y, w, h, score, lm0..lm9]
    """
    arr = np.asarray(row, dtype=float).reshape(-1)
    if arr.size >= 15:
        return arr[4:14].reshape(5, 2)
    if arr.size >= 14:
        return arr[5:15].reshape(5, 2)
    raise ValueError("无法从检测结果中解析关键点")


def _sample_frames(path: Path, n: int):
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        return [], 0, 0.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    want = {int(i * total / n) for i in range(n)} if total > 0 else set()
    frames, i = [], 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if i in want:
            frames.append((i, frame))
        i += 1
    cap.release()
    return frames, total, fps


def face_boxes(video: Path, n_frames: int = 16):
    """逐帧检出人脸 → 每帧最大人脸框 + 嘴部关键点。"""
    mp = model_path()
    if not mp:
        return [], 0, 0.0, []
    frames, total, fps = _sample_frames(video, n_frames)
    out = []
    for idx, fr in frames:
        h, w = fr.shape[:2]
        det = cv2.FaceDetectorYN_create(mp, "", (w, h), 0.6, 0.3, 5000)
        _, faces = det.detect(fr)
        if faces is None or not len(faces):
            out.append((idx, None, None))
            continue
        b = max(faces, key=lambda x: x[2] * x[3])
        lm = _landmarks_of(b)
        mouth = ((lm[3][0] + lm[4][0]) / 2, (lm[3][1] + lm[4][1]) / 2)
        out.append((idx, [int(v) for v in b[:4]], mouth))
    return out, total, fps, frames


def _strip_density(gray: np.ndarray) -> np.ndarray:
    """每行的拉普拉斯方差（边缘密度代理）。"""
    lap = cv2.Laplacian(gray, cv2.CV_64F)
    return np.array([lap[i, :].var() for i in range(lap.shape[0])], dtype=float)


def _col_density(band: np.ndarray) -> np.ndarray:
    """每列的拉普拉斯方差。

    不能对 1 列宽的切片单独做 Laplacian —— 3×3 核在边界上算出的数值不可比。
    这里先对整条带做 2D Laplacian，再按列取方差。
    """
    lap = cv2.Laplacian(band, cv2.CV_64F)
    return np.array([lap[:, c].var() for c in range(lap.shape[1])], dtype=float)


def detect(video: Path, n_frames: int = 16) -> dict:
    """检测压在人脸上的字幕。返回 {found, rect:[x0,y0,x1,y1], face, ...}。"""
    boxes, total, fps, frames = face_boxes(video, n_frames)
    det_boxes = [b for _, b, _ in boxes if b]
    if not det_boxes:
        return {"found": False, "reason": "检不到人脸"}
    bx, by, bw, bh = [int(v) for v in np.median(np.array(det_boxes), axis=0).astype(int)]
    if not frames:
        return {"found": False, "reason": "读不到帧", "face": [bx, by, bw, bh]}
    h, w = frames[0][1].shape[:2]

    # 搜索范围：脸下半部起（字幕压嘴），横向放宽（字幕比脸宽）
    y_lo = max(0, by + int(0.25 * bh))
    y_hi = min(h, by + int(1.40 * bh))
    x_lo = max(0, bx - int(3.0 * bw))
    x_hi = min(w, bx + int(4.0 * bw))

    mid = frames[len(frames) // 2][1]
    gray = cv2.cvtColor(mid, cv2.COLOR_BGR2GRAY)

    # 1) 行剖面：只看脸的横向范围（确定有字的地方）
    face_x0, face_x1 = bx, min(w, bx + bw)
    row_d = _strip_density(gray[:, face_x0:face_x1])
    seg = row_d[y_lo:y_hi]
    if seg.size == 0:
        return {"found": False, "reason": "搜索区为空", "face": [bx, by, bw, bh]}
    med = float(np.median(seg))
    thr = max(ROW_ABS_FLOOR, med * ROW_RATIO)
    hit_rows = [y_lo + i for i, v in enumerate(seg) if v >= thr]
    if not hit_rows:
        return {"found": False, "reason": "脸下半部没有高密度文字行",
                "face": [bx, by, bw, bh], "row_density_median": round(med, 1),
                "row_threshold": round(thr, 1)}

    # 分段后按行距合并：两行字幕之间有一小段空隙（实测 7 行 ≈0.09 脸高），
    # 不合并就只会擦到第一行。
    runs, cur = [], [hit_rows[0]]
    for r in hit_rows[1:]:
        if r == cur[-1] + 1:
            cur.append(r)
        else:
            runs.append(cur)
            cur = [r]
    runs.append(cur)
    max_gap = max(2, int(0.30 * bh))
    merged = [runs[0]]
    for r in runs[1:]:
        if r[0] - merged[-1][-1] - 1 <= max_gap:
            merged[-1] = merged[-1] + r
        else:
            merged.append(r)
    peak = max(hit_rows, key=lambda r: row_d[r])
    run = next((g for g in merged if g[0] <= peak <= g[-1]), max(merged, key=len))
    y0, y1 = run[0], run[-1] + 1

    # 2) 列剖面：在文字行内找左右边界
    band = gray[y0:y1, x_lo:x_hi]
    col_d = _col_density(band)
    # 背景基准用 p10 而不是中位数：文字本身可能占掉搜索区一半以上的列
    # （实测 64%），用中位数会把阈值抬到文字核心笔画上，列范围反而缩成一条。
    cbg = float(np.percentile(col_d, 10))
    cthr = max(COL_ABS_FLOOR, cbg * COL_RATIO)
    cx = bx + bw // 2
    hit_cols = [x_lo + i for i, v in enumerate(col_d) if v >= cthr]
    if hit_cols:
        cruns, ccur = [], [hit_cols[0]]
        for c in hit_cols[1:]:
            if c - ccur[-1] <= COL_MAX_GAP:
                ccur.append(c)
            else:
                cruns.append(ccur)
                ccur = [c]
        cruns.append(ccur)
        pick = next((g for g in cruns if g[0] <= cx <= g[-1]),
                    min(cruns, key=lambda g: abs(g[len(g) // 2] - cx)))
        x0, x1 = pick[0], pick[-1] + 1
    else:
        x0, x1 = x_lo, x_hi

    # 否决判据一：横向跨度（嘴、牙齿、下巴只占 ~0.6 倍脸宽）
    span = x1 - x0
    if span < MIN_TEXT_SPAN_RATIO * bw:
        return {"found": False,
                "reason": f"脸下半部有高密度行，但横向跨度仅 {span}px"
                          f"（< {MIN_TEXT_SPAN_RATIO:.1f}×脸宽 {bw}px）→ 判为面部特征而非字幕",
                "face": [bx, by, bw, bh], "candidate_rect": [x0, y0, x1, y1],
                "row_density_median": round(med, 1), "row_threshold": round(thr, 1)}

    # 否决判据二：文字行必须**跨过嘴**。嘴上方的衣服纹理/背景高对比带
    # 会被行剖面误当成字幕（M1_02 竖屏采访就踩过这个坑）。
    mouths = [mo for _, _, mo in boxes if mo]
    mouth_y = int(np.median([m[1] for m in mouths])) if mouths else by + int(0.72 * bh)
    if not (y0 <= mouth_y <= y1):
        return {"found": False,
                "reason": f"高密度行 y[{y0}-{y1}] 未跨过嘴部（嘴在 y={mouth_y}）"
                          f"→ 判为背景/衣物纹理而非压脸字幕",
                "face": [bx, by, bw, bh], "candidate_rect": [x0, y0, x1, y1],
                "mouth_y": mouth_y}

    # 否决判据三：文字块高度要占脸高的相当比例（实测 ≈0.55×脸高）
    band_h = y1 - y0
    if band_h < MIN_TEXT_HEIGHT_RATIO * bh:
        return {"found": False,
                "reason": f"高密度行仅 {band_h}px 高（< {MIN_TEXT_HEIGHT_RATIO:.2f}×脸高 {bh}px）"
                          f"→ 不构成字幕块",
                "face": [bx, by, bw, bh], "candidate_rect": [x0, y0, x1, y1],
                "mouth_y": mouth_y}

    return {"found": True, "face": [bx, by, bw, bh],
            "rect": [int(x0), int(y0), int(x1), int(y1)],
            "row_density_median": round(med, 1), "row_threshold": round(thr, 1),
            "col_density_p10": round(cbg, 1), "col_threshold": round(cthr, 1),
            "mouth_y": mouth_y,
            "frames": total, "fps": round(fps, 2), "resolution": f"{w}x{h}",
            "reason": f"脸下半部检出高密度文字行（{len(run)} 行，跨过嘴 y={mouth_y}），"
                      f"横向 x[{x0}-{x1}]（{span}px ≈ {span / max(bw, 1):.1f}×脸宽）"}


def oracle_mask(derived: Path, master: Path, n_frames: int = 9, thr: float = 60.0):
    """GT 口径：母版与派生件相减 → 字幕像素的精确位置（多帧中位数消编码噪声）。"""
    A, B = [], []
    for p, acc in ((master, A), (derived, B)):
        cap = cv2.VideoCapture(str(p))
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        for i in np.linspace(0, max(total - 1, 0), n_frames).astype(int):
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(i))
            ok, fr = cap.read()
            if ok:
                acc.append(fr.astype(np.int16))
        cap.release()
    n = min(len(A), len(B))
    if not n:
        return None
    diff = np.median([np.abs(A[i] - B[i]).sum(2) for i in range(n)], axis=0)
    mask = (diff > thr).astype(np.uint8)
    ys, xs = np.nonzero(mask)
    if not len(ys):
        return None
    return mask, [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1]


def iou(a, b) -> float:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    inter = max(0, ix1 - ix0) * max(0, iy1 - iy0)
    ua = (ax1 - ax0) * (ay1 - ay0) + (bx1 - bx0) * (by1 - by0) - inter
    return round(inter / ua, 3) if ua > 0 else 0.0
