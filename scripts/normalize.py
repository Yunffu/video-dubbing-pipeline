#!/usr/bin/env python3
"""规格统一（入库标准化）：把任意来源素材转成 Pipeline 统一母版格式。

统一为：H.264 / yuv420p / 25fps / AAC 48kHz 立体声 / 偶数分辨率 / 10–60s 片段。
母版一旦生成即视为该 case 的 GT（合成 GT 法的基准）。

用法:
    python scripts/normalize.py <输入> --out assets/raw/M1_01.mp4 [--start 10] [--dur 25]
"""
import argparse
import shutil
import subprocess
import sys
from pathlib import Path


def ffmpeg_bin() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        sys.exit("找不到 ffmpeg：把 FFmpeg 的 bin 目录加进 PATH")
    return exe


def probe(path: Path) -> dict:
    out = subprocess.run(
        [shutil.which("ffprobe"), "-v", "error", "-print_format", "json",
         "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    import json
    return json.loads(out.stdout)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("source")
    ap.add_argument("--out", required=True)
    ap.add_argument("--start", type=float, default=10.0, help="起始秒")
    ap.add_argument("--dur", type=float, default=25.0, help="片段时长秒")
    ap.add_argument("--fps", type=int, default=25)
    args = ap.parse_args()

    src = Path(args.source)
    info = probe(src)
    total = float(info["format"]["duration"])
    if args.start + args.dur > total:
        args.start = max(0.0, total - args.dur)
    print(f"源: {src.name} 原始时长 {total:.2f}s → 取 [{args.start:.1f}, {args.start+args.dur:.1f}]s")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    vf = f"scale=trunc(iw/2)*2:trunc(ih/2)*2,fps={args.fps},format=yuv420p"
    cmd = [ffmpeg_bin(), "-y", "-v", "error",
           "-ss", f"{args.start}", "-t", f"{args.dur}", "-i", str(src),
           "-vf", vf,
           "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-profile:v", "high",
           "-c:a", "aac", "-ar", "48000", "-ac", "2", "-b:a", "128k",
           "-movflags", "+faststart", str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        sys.exit("ffmpeg 失败:\n" + r.stderr)
    print(f"母版已生成: {out}  {out.stat().st_size/1e6:.1f}MB")


if __name__ == "__main__":
    main()
