#!/usr/bin/env python3
"""扫描素材，自动挑出"有人声、画面不黑"的片段窗口（入库选段器）。

用法:
    python scripts/scan_segment.py <源文件> [--step 15] [--win 25]
"""
import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path


def ffprobe_duration(path: Path) -> float:
    out = subprocess.run(
        [shutil.which("ffprobe"), "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    return float(out.stdout.strip() or 0)


def measure(path: Path, start: float, dur: float) -> tuple[float, float]:
    """返回 (平均音量 dB, 黑帧占比)。"""
    r = subprocess.run(
        [shutil.which("ffmpeg"), "-v", "info", "-ss", f"{start}", "-t", f"{dur}", "-i", str(path),
         "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    m = re.search(r"mean_volume:\s*(-?\d+(?:\.\d+)?) dB", r.stderr)
    vol = float(m.group(1)) if m else -99.0

    b = subprocess.run(
        [shutil.which("ffmpeg"), "-v", "info", "-ss", f"{start}", "-t", f"{dur}", "-i", str(path),
         "-vf", "blackdetect=d=0.5:pix_th=0.10", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    black = 0.0
    for mm in re.finditer(r"black_duration:([\d.]+)", b.stderr):
        black += float(mm.group(1))
    return vol, black / dur if dur else 0.0


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("source")
    ap.add_argument("--step", type=float, default=15.0)
    ap.add_argument("--win", type=float, default=25.0)
    args = ap.parse_args()

    path = Path(args.source)
    total = ffprobe_duration(path)
    print(f"{path.name} 总时长 {total:.1f}s")
    print(f"{'start':>7} {'mean_dB':>8} {'black%':>7}  判定")
    best = None
    t = 0.0
    while t + args.win <= total:
        vol, black = measure(path, t, args.win)
        ok = vol > -30 and black < 0.2
        flag = "OK" if ok else ("静音" if vol <= -30 else "黑场多")
        print(f"{t:7.0f} {vol:8.1f} {black*100:6.1f}%  {flag}")
        if ok and (best is None or vol > best[1]):
            best = (t, vol, black)
        t += args.step
    if best:
        print(f"\n推荐片段: --start {best[0]:.0f} --dur {args.win:.0f}  (mean {best[1]:.1f} dB)")
    else:
        print("\n没有全部达标窗口，需人工挑段")


if __name__ == "__main__":
    main()
