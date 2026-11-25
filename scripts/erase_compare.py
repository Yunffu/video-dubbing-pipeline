#!/usr/bin/env python3
"""字幕擦除对比实验：引擎 × mask 粒度（2×2 + 1 健全性对照）。

要回答的问题：**到底该优化「修补引擎」还是「mask 粒度」？**
一次性只动一个变量，四格对比：

    引擎        mask                含义
    ---------------------------------------------------------------
    opencv      全宽横带(启发式)      方案 A：边缘扩散 + 粗 mask
    lama        全宽横带(启发式)      方案 B：内容感知 + 粗 mask
    opencv      逐文字像素(oracle)   理想检测器 + 边缘扩散
    lama        逐文字像素(oracle)   理想检测器 + 内容感知
    ---------------------------------------------------------------
    lama        小方块(健全性)      引擎本身能不能用（不是 mask 太小的问题）

oracle mask = 用 GT 差分算出「字幕字形的真实像素」，**只在评测里可用**（生产没有 GT）。
它代表「如果检测器足够准（像方案 B 的 PaddleOCR 逐文字框），最好能到多少」。

用法:
    python scripts/erase_compare.py --case M1_04 --frames 4 --json _work/erase/compare_M1_04.json
"""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from eval.metrics import psnr, ssim  # noqa: E402
from pipeline import subtitle_erase as se  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def text_pixel_mask(derived: np.ndarray, master: np.ndarray) -> np.ndarray:
    """oracle：字幕字形的真实像素（GT 差分 + 轻微膨胀吃掉抗锯齿边）。"""
    diff = np.abs(derived.astype(np.int16) - master.astype(np.int16)).sum(axis=2)
    m = (diff > 30).astype(np.uint8) * 255
    return cv2.dilate(m, np.ones((3, 3), np.uint8), iterations=1)


def run_cell(engine: str, derived: np.ndarray, master: np.ndarray,
             mask: np.ndarray, lama=None) -> tuple[np.ndarray, float]:
    if engine == "opencv":
        out = se.inpaint_frame(derived, mask, "opencv_telea")
    else:
        out = lama.inpaint(derived, mask)
    return out, psnr(out, master)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", default="M1_04")
    ap.add_argument("--frames", type=int, default=4)
    ap.add_argument("--json")
    args = ap.parse_args()

    derived_p = ROOT / f"assets/derived/{args.case}_hard_sub_bottom.mp4"
    master_p = ROOT / f"assets/raw/{args.case}.mp4"
    if not derived_p.exists():
        sys.exit(f"缺素材：{derived_p}")

    band = se.detect_subtitle_band(derived_p)
    y0, y1 = int(band["y0"]), int(band["y1"])
    print(f"启发式横带 rows {y0}–{y1}（bands={band.get('bands')}）")

    cd = cv2.VideoCapture(str(derived_p))
    cm = cv2.VideoCapture(str(master_p))
    total = int(cd.get(cv2.CAP_PROP_FRAME_COUNT))
    idxs = np.linspace(0, total - 1, min(args.frames, total)).astype(int).tolist()

    from pipeline.inpaint.lama_onnx import LamaOnnx
    lama = LamaOnnx(normalize="unit")

    cells = {
        "opencv/全宽横带": ("opencv", "band"),
        "lama/全宽横带": ("lama", "band"),
        "opencv/逐文字像素(oracle)": ("opencv", "text"),
        "lama/逐文字像素(oracle)": ("lama", "text"),
        "lama/小方块(健全性)": ("lama", "box"),
    }
    acc = {k: {"in": [], "ssim": [], "out": []} for k in cells}
    black = 0

    for i in idxs:
        cd.set(cv2.CAP_PROP_POS_FRAMES, i)
        cm.set(cv2.CAP_PROP_POS_FRAMES, i)
        ok1, a = cd.read()
        ok2, b = cm.read()
        if not (ok1 and ok2):
            continue
        h = a.shape[0]
        band_mask = se.make_mask(h, a.shape[1], y0, y1)
        text_mask = text_pixel_mask(a, b)
        box_mask = np.zeros((h, a.shape[1]), dtype=np.uint8)
        cx, cy = a.shape[1] // 2, y0 - 80
        box_mask[max(cy, 0):cy + 64, cx - 64:cx + 64] = 255

        masks = {"band": band_mask, "text": text_mask, "box": box_mask}
        if float(np.mean(b)) < 8.0:      # 近黑帧：PSNR 无意义，单列不计入
            black += 1
            continue
        for name, (engine, mk) in cells.items():
            out, _ = run_cell(engine, a, b, masks[mk], lama=lama)
            acc[name]["in"].append(psnr(out, b, (y0, y1)))
            acc[name]["ssim"].append(ssim(out, b, (y0, y1)))
            acc[name]["out"].append(psnr(out, b, (0, y0)))
            if i == idxs[0]:
                cv2.imwrite(f"_work/erase/{args.case}_{engine}_{mk}.png", out)

    cd.release(); cm.release()

    def med(v):
        return round(float(np.median(v)), 3) if v else None
    print(f"\n{'配置':34s} {'字幕区PSNR':>10s} {'SSIM':>8s} {'区外PSNR':>10s}")
    print("-" * 68)
    rows = []
    for name in cells:
        d = acc[name]
        print(f"{name:34s} {med(d['in']):>10.2f} {med(d['ssim']):>8.3f} {med(d['out']):>10.2f}")
        rows.append({"cell": name, "psnr_in_mask": med(d["in"]),
                     "ssim_in_mask": med(d["ssim"]), "psnr_out_mask": med(d["out"])})

    print(f"\n（中位数口径；跳过近黑帧 {black} 帧）阈值参考: PSNR≥30dB, SSIM≥0.9")
    print("（oracle mask 只在评测里可用；生产要靠方案 B 的逐文字框检测器）")

    payload = {"case": args.case, "band": {"y0": y0, "y1": y1},
               "frames": len(idxs), "frames_black_skipped": black,
               "agg": "median", "rows": rows}
    if args.json:
        p = Path(args.json)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"-> {p}")


if __name__ == "__main__":
    main()
