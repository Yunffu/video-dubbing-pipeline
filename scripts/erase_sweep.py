#!/usr/bin/env python3
"""字幕擦除方案 A 的参数扫描（对比表的 A 列数据来源）。

只对采样帧做修补并与母版比 PSNR，**不落盘** —— 目的���隔离「修补算法」本身，
不掺编码损失（这是我们踩过的坑，见 docs/notes/10-选型结论与badcase清单.md）。

扫描维度：
    margin  : mask 上下余量（行）。验证「余量宁可多给，别让 mask 边界贴着字」
    scheme  : opencv_telea / opencv_ns
    band    : heuristic（启发式检出）/ oracle（用 GT 差分定位真值带，仅评测用）

用法:
    python scripts/erase_sweep.py --case M1_04 --frames 8 --json _work/erase/sweep_M1_04.json
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

from eval.metrics import psnr, ssim  # noqa: E402
from pipeline import subtitle_erase as se  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def true_band(derived: Path, master: Path, n_frames: int = 6) -> tuple[int, int]:
    """用 GT 差分定位字幕真实行区间（仅评测用，生产不可用）。"""
    cd = cv2.VideoCapture(str(derived))
    cm = cv2.VideoCapture(str(master))
    total = int(cd.get(cv2.CAP_PROP_FRAME_COUNT))
    rows: dict[int, int] = {}
    for i in np.linspace(0, max(total - 1, 0), n_frames).astype(int):
        cd.set(cv2.CAP_PROP_POS_FRAMES, int(i))
        cm.set(cv2.CAP_PROP_POS_FRAMES, int(i))
        ok1, a = cd.read()
        ok2, b = cm.read()
        if not (ok1 and ok2):
            continue
        diff = np.abs(a.astype(np.int16) - b.astype(np.int16)).sum(axis=2) > 30
        for y in range(diff.shape[0]):
            rows[y] = rows.get(y, 0) + int(diff[y].sum())
    cd.release(); cm.release()
    if not rows:
        return (0, 0)
    top = max(rows.values())
    active = [y for y, v in rows.items() if v > top * 0.02]
    return (min(active), max(active))


def score(derived: Path, master: Path, mask: tuple[int, int], scheme: str,
          n_frames: int) -> dict:
    cd = cv2.VideoCapture(str(derived))
    cm = cv2.VideoCapture(str(master))
    total = int(cd.get(cv2.CAP_PROP_FRAME_COUNT))
    idxs = np.linspace(0, max(total - 1, 0), min(n_frames, total)).astype(int)
    y0, y1 = mask
    flag = cv2.INPAINT_TELEA if scheme == "opencv_telea" else cv2.INPAINT_NS
    ins, outs, sins = [], [], []
    for i in idxs:
        cd.set(cv2.CAP_PROP_POS_FRAMES, int(i))
        cm.set(cv2.CAP_PROP_POS_FRAMES, int(i))
        ok1, a = cd.read()
        ok2, b = cm.read()
        if not (ok1 and ok2):
            continue
        m = np.zeros(a.shape[:2], dtype=np.uint8)
        m[y0:y1, :] = 255
        out = cv2.inpaint(a, m, 4, flag)
        ins.append(psnr(out, b, (y0, y1)))
        sins.append(ssim(out, b, (y0, y1)))
        outs.append(psnr(out, b, (0, y0)))
        outs.append(psnr(out, b, (y1, out.shape[0])))
    cd.release(); cm.release()
    avg = lambda v: round(sum(v) / len(v), 3) if v else None  # noqa: E731
    return {"psnr_in_mask": avg(ins), "ssim_in_mask": avg(sins),
            "psnr_out_mask": avg(outs), "frames": len(ins)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", default="M1_04")
    ap.add_argument("--frames", type=int, default=8)
    ap.add_argument("--margins", default="4,12,24,40")
    ap.add_argument("--json")
    args = ap.parse_args()

    derived = ROOT / f"assets/derived/{args.case}_hard_sub_bottom.mp4"
    master = ROOT / f"assets/raw/{args.case}.mp4"
    if not derived.exists() or not master.exists():
        sys.exit(f"缺素材：{derived} / {master}")

    band = se.detect_subtitle_band(derived)
    tb = true_band(derived, master)
    print(f"启发式带: rows {band.get('y0')}–{band.get('y1')}  bands={band.get('bands')}")
    print(f"真值带(GT差分): rows {tb[0]}–{tb[1]}\n")

    rows = []
    for scheme in ["opencv_telea", "opencv_ns"]:
        for mg in [int(x) for x in args.margins.split(",")]:
            for src in ["heuristic", "oracle"]:
                if src == "heuristic":
                    y0 = max(int(band["y0"]) - mg + 4, 0)
                    y1 = min(int(band["y1"]) + mg - 4, 10 ** 6)
                else:
                    y0 = max(tb[0] - mg, 0)
                    y1 = tb[1] + mg
                s = score(derived, master, (y0, y1), scheme, args.frames)
                row = {"scheme": scheme, "mask_src": src, "margin": mg,
                       "mask": [y0, y1], **s}
                rows.append(row)
                print(f"{scheme:14s} {src:9s} margin={mg:3d} mask={y0}-{y1} "
                      f"in={s['psnr_in_mask']:6.2f}dB ssim={s['ssim_in_mask']:.3f} "
                      f"out={s['psnr_out_mask']:6.2f}dB")

    best = max(rows, key=lambda r: r["psnr_in_mask"])
    print(f"\n最佳: {best['scheme']} {best['mask_src']} margin={best['margin']} "
          f"in={best['psnr_in_mask']}dB (阈值 30dB → "
          f"{'PASS' if best['psnr_in_mask'] >= 30 else 'FAIL'})")

    payload = {"case": args.case, "heuristic_band": band, "true_band": list(tb),
               "rows": rows, "best": best,
               "threshold": {"psnr_in_mask": 30}}
    if args.json:
        p = Path(args.json)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"-> {p}")


if __name__ == "__main__":
    main()
