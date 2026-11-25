#!/usr/bin/env python3
"""检测**压在脸上的硬字幕**（人脸字幕），输出待擦除的矩形区域。

实现全部在 pipeline/face_sub.py（库里），这里只做 CLI 包装 ——
评测链路和这个脚本必须用同一份实现，否则「脚本测出来的」和
「跑分器算出来的」会漂移。

准确性怎么证明（不靠"看着对"）：
  用「合成 GT 法」造 oracle —— 派生件 = 母版 + 烧录字幕，两者相减就是
  字幕像素的精确位置。检测器结果与 oracle 求 IoU，这个数是可复现的客观指标。
  加 --oracle <母版> 即可输出该对照。

用法:
    python scripts/detect_face_sub.py <video.mp4> [--frames 16] [--json out.json]
    python scripts/detect_face_sub.py <derived.mp4> --oracle assets/raw/M1_04.mp4
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline import face_sub  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--frames", type=int, default=16)
    ap.add_argument("--json")
    ap.add_argument("--oracle", help="母版路径：给出后同时输出与 GT 的 IoU")
    args = ap.parse_args()

    res = face_sub.detect(Path(args.video), args.frames)
    if args.oracle:
        om = face_sub.oracle_mask(Path(args.video), Path(args.oracle))
        if om:
            mask, rect = om
            res["oracle_rect"] = rect
            res["oracle_pixels"] = int(mask.sum())
            if res.get("found"):
                res["iou_with_oracle"] = face_sub.iou(res["rect"], rect)
                d = res["rect"]
                res["det_vs_oracle"] = {
                    "det": d, "oracle": rect,
                    "dy0": d[1] - rect[1], "dy1": d[3] - rect[3],
                    "dx0": d[0] - rect[0], "dx1": d[2] - rect[2]}
            else:
                res["iou_with_oracle"] = 0.0
        else:
            res["oracle_error"] = "算不出 oracle（母版与派生件一致？）"

    print(json.dumps(res, ensure_ascii=False, indent=2))
    if args.json:
        Path(args.json).write_text(json.dumps(res, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
        print(f"-> {args.json}")


if __name__ == "__main__":
    main()
