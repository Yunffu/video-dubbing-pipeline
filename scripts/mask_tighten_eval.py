#!/usr/bin/env python3
"""量化「收紧底部字幕 mask」到底值多少分（任务 #6 的证据）。

对照三组，**同一套引擎、同一批帧**，只换 mask：
  1. 粗带      —— 20 带粒度法给的整条带（当前评测口径）
  2. 收紧带    —— 带内再定位文字行（生产链路的行为）
  3. Oracle    —— 母版 vs 派生件相减得到的**逐像素**字幕掩膜（理论上限）

三个问题：
  - 收紧带的**文字召回率**够不够（漏掉的行 = 擦不干净的字）
  - mask 面积少了多少
  - mask 内 PSNR/SSIM 提升了多少，离 oracle 还差多少

用法:
    python scripts/mask_tighten_eval.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from eval import metrics as M  # noqa: E402
from pipeline import subtitle_erase as se  # noqa: E402
from pipeline.face_sub import oracle_mask  # noqa: E402


def row_recall(mask: np.ndarray, y0: int, y1: int) -> float:
    rows = np.nonzero(mask.any(axis=1))[0]
    if not len(rows):
        return 0.0
    o0, o1 = int(rows.min()), int(rows.max()) + 1
    hit = sum(1 for y in range(o0, o1) if y0 <= y < y1)
    return round(hit / max(o1 - o0, 1), 3)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    cases = json.loads((ROOT / "assets" / "cases.json").read_text(encoding="utf-8"))["cases"]
    out = []
    for c in cases:
        if c["difficulty_type"] != "hard_sub_bottom":
            continue
        derived = ROOT / c["derived_file"]
        master = ROOT / c["master"]
        if not derived.exists() or not master.exists():
            continue
        band = se.detect_subtitle_band(derived)
        if not band.get("found"):
            continue
        cap = cv2.VideoCapture(str(derived))
        W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        cap.release()

        t0, t1 = band["y0"], band["y1"]
        n0, n1 = se.tighten_band(derived, t0, t1)
        coarse = (0, t0, W, t1)
        tight = (0, n0, W, n1)

        om = oracle_mask(derived, master)
        orc_rect = om[1] if om else None

        res = {"case_id": c["case_id"], "height": H,
               "coarse": {"rect": list(coarse),
                          "area": round((t1 - t0) / H, 4),
                          "recall": row_recall(om[0], t0, t1) if om else None},
               "tight": {"rect": list(tight),
                         "area": round((n1 - n0) / H, 4),
                         "recall": row_recall(om[0], n0, n1) if om else None},
               "oracle_rect": orc_rect,
               "psnr": {}}
        for name, rect in (("coarse", coarse), ("tight", tight)):
            mm = M.subtitle_erase_in_memory(derived, master, rect,
                                            scheme="opencv_telea", n_frames=12)
            if mm.get("error"):
                res["psnr"][name] = {"error": mm["error"]}
                continue
            res["psnr"][name] = {
                "psnr_in_mask": mm["after"]["psnr_in_mask"]["median"],
                "ssim_in_mask": mm["after"]["ssim_in_mask"]["median"],
                "psnr_out_mask_delta": mm["delta"]["psnr_out_mask"],
            }
        print(f"{c['case_id']:26s} 粗带面积{res['coarse']['area']:.3f} recall{res['coarse']['recall']}"
              f" → 收紧{res['tight']['area']:.3f} recall{res['tight']['recall']}"
              f" | PSNR in {res['psnr'].get('coarse',{}).get('psnr_in_mask')}"
              f" → {res['psnr'].get('tight',{}).get('psnr_in_mask')}")
        out.append(res)

    (ROOT / "results" / "mask_tighten_eval.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("-> results/mask_tighten_eval.json")


if __name__ == "__main__":
    main()
