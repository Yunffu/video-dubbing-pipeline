#!/usr/bin/env python3
"""引擎健全性测试：**与字幕无关**的「挖洞重建」能力。

为什么单独做：
  erase_compare.py 里的格子都带着「字幕」这个混淆项 —— 引擎表现好坏
  一半来自「洞合不合适」。本脚本把字幕摘掉：
    1. 拿**母版**（无字幕）当输入；
    2. 人为挖一个矩形洞（干净背景处）；
    3. 让引擎补；
    4. 只在洞内跟母版比 PSNR/SSIM。

  结果解释：
    - OpenCV 边缘扩散在**平滑背景**上近乎完美（大洞 PSNR 可到 40dB+），
      在有纹理的背景上会糊；
    - LaMa 是**大洞模型**，大洞上应该显著优于边缘扩散；
    - 若两者都很差 → 是我们的包装（预处理/后处理/分块）有问题，不是模型不行。

这条测试是**负控 + 正控**都有：它验证「引擎本身能不能用」，
把「字幕擦不好」的原因从「引擎不行」里摘出去。
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from eval.metrics import psnr_mask, ssim  # noqa: E402
from pipeline import subtitle_erase as se  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def hole_mask(h: int, w: int, x0: int, y0: int, x1: int, y1: int) -> np.ndarray:
    m = np.zeros((h, w), dtype=np.uint8)
    m[y0:y1, x0:x1] = 255
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", default="M1_04")
    ap.add_argument("--frames", type=int, default=6)
    ap.add_argument("--hole", default="256x128", help="洞大小 WxH")
    ap.add_argument("--json")
    args = ap.parse_args()

    master_p = ROOT / f"assets/raw/{args.case}.mp4"
    if not master_p.exists():
        sys.exit(f"缺素材：{master_p}")
    wh, hh = (int(v) for v in args.hole.split("x"))

    cap = cv2.VideoCapture(str(master_p))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    idxs = np.linspace(0, total - 1, min(args.frames, total)).astype(int).tolist()

    from pipeline.inpaint.lama_onnx import LamaOnnx
    lama = LamaOnnx(normalize="unit")

    acc = {"opencv": {"psnr": [], "ssim": []}, "lama": {"psnr": [], "ssim": []}}
    per_pos: dict[str, dict[str, list]] = {}
    for i in idxs:
        cap.set(cv2.CAP_PROP_POS_FRAMES, i)
        ok, fr = cap.read()
        if not ok:
            continue
        h, w = fr.shape[:2]
        # 多个洞位：只测一个区域会得出「这个区域难」而不是「这个引擎弱」的结论。
        # 三个位置分别落在画面上部（多为背景）、中部、左下（避开底部字幕带）。
        positions = {
            "top": (w // 2 - wh // 2, max(h // 6, 0)),
            "center": (w // 2 - wh // 2, h // 2 - hh // 2),
            "left_low": (max(w // 6, 0), h - hh - max(h // 6, 0)),
        }
        for pname, (x0, y0) in positions.items():
            if x0 < 0 or y0 < 0 or x0 + wh > w or y0 + hh > h:
                continue
            mask = hole_mask(h, w, x0, y0, x0 + wh, y0 + hh)
            for eng in ("opencv", "lama"):
                out = (se.inpaint_frame(fr, mask, "opencv_telea") if eng == "opencv"
                       else lama.inpaint(fr, mask))
                p = psnr_mask(out, fr, mask)
                s = ssim(out, fr, (x0, y0, x0 + wh, y0 + hh))
                acc[eng]["psnr"].append(p)
                acc[eng]["ssim"].append(s)
                per_pos.setdefault(pname, {"opencv": [], "lama": []})[eng].append(p)
            if i == idxs[0]:
                cv2.imwrite(f"_work/erase/sanity_master_{args.case}.png", fr)
                cv2.imwrite(f"_work/erase/sanity_opencv_{args.case}.png",
                            se.inpaint_frame(fr, mask, "opencv_telea"))
                cv2.imwrite(f"_work/erase/sanity_lama_{args.case}.png",
                            lama.inpaint(fr, mask))
    cap.release()

    def med(v):
        return round(float(np.median(v)), 3) if v else None

    print(f"\n引擎健全性：{args.case} 母版挖洞 {wh}x{hh}"
          f"（洞内 vs 母版，中位数，{len(idxs)} 帧 × 3 个洞位）")
    print(f"{'引擎':10s} {'洞内PSNR':>10s} {'洞内SSIM':>10s}")
    print("-" * 34)
    rows = []
    for eng in ("opencv", "lama"):
        p = med(acc[eng]["psnr"])
        s = med(acc[eng]["ssim"])
        print(f"{eng:10s} {p:>10.2f} {s:>10.3f}")
        rows.append({"engine": eng, "psnr_in_hole": p, "ssim_in_hole": s})
    print("\n分洞位（中位数）：")
    print(f"{'洞位':10s} {'opencv':>10s} {'lama':>10s} {'Δ':>8s}")
    for pname, d in per_pos.items():
        o, l = med(d["opencv"]), med(d["lama"])
        print(f"{pname:10s} {o:>10.2f} {l:>10.2f} {l - o:>8.2f}")
    print("\n读法：平滑背景上 OpenCV 边缘扩散接近完美；"
          "大洞上 LaMa 应显著更好（它是大洞模型）。")
    print("若两者都差 → 查包装（预处理/后处理/分块），不是模型不行。")

    if args.json:
        p = Path(args.json)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"case": args.case, "hole": args.hole,
                                 "frames": len(idxs), "agg": "median",
                                 "rows": rows,
                                 "per_position": {k: {e: med(v) for e, v in d.items()}
                                                  for k, d in per_pos.items()}},
                                ensure_ascii=False, indent=2),
                     encoding="utf-8")
        print(f"-> {p}")


if __name__ == "__main__":
    main()
