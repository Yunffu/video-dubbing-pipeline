#!/usr/bin/env python3
"""生成人评条目：成片 + 指标 + 成本，凑成一份「可看可打分」的清单。

每条包含三样东西，正好对应用户提的三件事：
  - **成片**（人看的）      → mp4（OpenCV 修补，快）
  - **自动指标**（参考列）  → 与 `eval/runner.py` 同口径（中位数 + 剔除近黑帧）
  - **成本**（钱）         → 从 `eval/cost.py` 的口径取（离线翻译时为 0）

用法:
    python eval/human_review/make_items.py --cases all --out results/human_review/items
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import cv2  # noqa: E402

from eval.metrics import subtitle_erase_in_memory  # noqa: E402
from pipeline import subtitle_erase as se  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def render(derived: Path, master: Path, out_mp4: Path, band: dict,
           scheme: str = "opencv_telea") -> dict:
    """出片：字幕擦除后的成片（人评看的就是这个）。"""
    cap = cv2.VideoCapture(str(derived))
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    x0, y0, x1, y1 = se.region_of(band, w)
    mask = se.make_mask_rect(h, w, x0, y0, x1, y1)
    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    vw = cv2.VideoWriter(str(out_mp4), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    frames = 0
    while True:
        ok, fr = cap.read()
        if not ok:
            break
        vw.write(se.inpaint_frame(fr, mask, scheme))
        frames += 1
    cap.release()
    vw.release()
    return {"output": str(out_mp4.relative_to(ROOT)), "frames": frames,
            "mask_rect": [x0, y0, x1, y1], "fps": fps}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default="all")
    ap.add_argument("--out", default="results/human_review/items")
    ap.add_argument("--frames", type=int, default=12, help="算指标用的采样帧数")
    args = ap.parse_args()

    cases = json.loads((ROOT / "assets" / "cases.json").read_text(encoding="utf-8"))["cases"]
    if args.cases != "all":
        want = set(args.cases.split(","))
        cases = [c for c in cases if c["case_id"] in want or c["difficulty_type"] in want]

    outdir = ROOT / args.out
    items = []
    for c in cases:
        dt = c.get("difficulty_type")
        if dt not in ("hard_sub_bottom", "hard_sub_face"):
            continue
        derived = ROOT / c["derived_file"]
        master = ROOT / c["master"]
        if not derived.exists() or not master.exists():
            print(f"跳过（素材缺失）{c['case_id']}")
            continue
        is_face = dt == "hard_sub_face"
        band = se.detect_face_subtitle(derived) if is_face else se.detect_subtitle_band(derived)
        if not band.get("found"):
            print(f"跳过（未检出字幕）{c['case_id']}")
            continue
        rect = se.region_of(band, int(cv2.VideoCapture(str(derived)).get(cv2.CAP_PROP_FRAME_WIDTH)))

        mm = subtitle_erase_in_memory(derived, master, rect, scheme="opencv_telea",
                                      n_frames=args.frames)
        if "error" in mm:
            print(f"跳过（{mm['error']}）{c['case_id']}")
            continue

        vid = outdir / f"{c['case_id']}.mp4"
        meta = render(derived, master, vid, band)

        items.append({
            "id": c["case_id"],
            "title": f"{c['case_id']}（{dt}）",
            "difficulty_type": dt,
            "video": meta["output"],
            "master": c["master"],
            "metrics": {
                "psnr_in_mask": mm["after"]["psnr_in_mask"]["median"],
                "psnr_in_mask_p25": mm["after"]["psnr_in_mask"]["p25"],
                "psnr_in_mask_p75": mm["after"]["psnr_in_mask"]["p75"],
                "ssim_in_mask": mm["after"]["ssim_in_mask"]["median"],
                "psnr_baseline": mm["baseline"]["psnr_in_mask"]["median"],
                "psnr_delta": mm["delta"]["psnr_in_mask"],
                "untouched_out_mask": mm["untouched_out_mask"],
            },
            "cost": {"usd": 0.0, "note": "本条为字幕擦除环节，无模型调用；"
                                         "译制环节成本见 eval/cost.py"},
            "auto_note": "指标仅供参考，判定权在人（overall 字段由人填）",
        })
        print(f"✓ {c['case_id']}  psnr={items[-1]['metrics']['psnr_in_mask']} "
              f"ssim={items[-1]['metrics']['ssim_in_mask']}  → {vid.name}")

    outdir.mkdir(parents=True, exist_ok=True)
    p = outdir / "items.json"
    p.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n→ {p}（{len(items)} 条）")
    print("下一步：python eval/human_review/build_packet.py "
          f"--items {args.out}/items.json")


if __name__ == "__main__":
    main()
