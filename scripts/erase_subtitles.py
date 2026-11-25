#!/usr/bin/env python3
"""字幕擦除（方案 A：OpenCV inpaint）+ 合成 GT 法评测。

用法:
    # 只擦
    python scripts/erase_subtitles.py --video assets/derived/M1_04_hard_sub_bottom.mp4 \
        --out _work/erase/M1_04_erased.mp4 --scheme opencv_telea

    # 擦 + 与母版比（合成 GT 法：母版 = GT）
    python scripts/erase_subtitles.py --video assets/derived/M1_04_hard_sub_bottom.mp4 \
        --gt assets/raw/M1_04.mp4 --out _work/erase/M1_04_erased.mp4 --json out.json
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline import subtitle_erase as se  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--gt", help="母版（GT）路径，仅派生件有")
    ap.add_argument("--out", required=True)
    ap.add_argument("--scheme", default="opencv_telea",
                    choices=["opencv_telea", "opencv_ns"])
    ap.add_argument("--lossless", action="store_true",
                    help="FFV1/MKV 无损落盘（隔离重编码损失）")
    ap.add_argument("--frames", type=int, default=12, help="检测/评测采样帧数")
    ap.add_argument("--json")
    args = ap.parse_args()

    video = Path(args.video)
    gt = Path(args.gt) if args.gt else None
    out = Path(args.out)

    res = se.erase_video(video, out, scheme=args.scheme, gt=gt,
                         lossless=args.lossless, n_frames=args.frames)

    # 落盘后再算一次「文件口径」，与 in_memory 口径对照 → 分离重编码损失
    if res.get("ok") and gt is not None:
        file_res = se.evaluate_erase(Path(res["output"]), gt,
                                     tuple(res["mask_rows"]), n_frames=args.frames)
        res["file_metrics"] = file_res

    print(json.dumps(res, ensure_ascii=False, indent=2))
    if args.json:
        Path(args.json).write_text(json.dumps(res, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
    sys.exit(0 if res.get("ok") else 1)


if __name__ == "__main__":
    main()
