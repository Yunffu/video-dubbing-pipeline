#!/usr/bin/env python3
"""素材入库自检：对视频文件跑 ffprobe，输出 JSON 与 Markdown 自检表。

用法:
    python scripts/probe_media.py <file> [<file> ...] [--json out.json] [--md out.md]

判定规则见 market_scan/B站视频译制岗-作战计划.md §3。
"""
import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path


def probe(path: Path) -> dict:
    exe = shutil.which("ffprobe")
    if not exe:
        sys.exit("找不到 ffprobe：把 FFmpeg 的 bin 目录加进 PATH（见 README）")
    out = subprocess.run(
        [exe, "-v", "error", "-print_format", "json",
         "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if out.returncode != 0:
        return {"file": str(path), "error": out.stderr.strip() or "ffprobe failed"}

    raw = json.loads(out.stdout)
    v = next((s for s in raw["streams"] if s["codec_type"] == "video"), {})
    a = next((s for s in raw["streams"] if s["codec_type"] == "audio"), {})
    fmt = raw.get("format", {})

    def num(x):
        try:
            return float(x)
        except (TypeError, ValueError):
            return None

    v_dur = num(v.get("duration"))
    a_dur = num(a.get("duration"))
    av_diff_ms = None
    if v_dur is not None and a_dur is not None:
        av_diff_ms = round((v_dur - a_dur) * 1000, 1)

    return {
        "file": str(path),
        "size_bytes": int(num(fmt.get("size")) or 0),
        "container_duration": num(fmt.get("duration")),
        "width": v.get("width"),
        "height": v.get("height"),
        "r_frame_rate": v.get("r_frame_rate"),
        "avg_frame_rate": v.get("avg_frame_rate"),
        "v_codec": v.get("codec_name"),
        "pix_fmt": v.get("pix_fmt"),
        "color_space": v.get("color_space"),
        "nb_frames": int(num(v.get("nb_frames")) or 0),
        "v_duration": v_dur,
        "a_codec": a.get("codec_name"),
        "sample_rate": int(num(a.get("sample_rate")) or 0),
        "channels": a.get("channels"),
        "a_duration": a_dur,
        "av_diff_ms": av_diff_ms,
    }


def checks(r: dict) -> list[tuple[str, str, str]]:
    """返回 (检查项, 实测值, 判定)。判定 ∈ PASS / FAIL / WARN / N/A。"""
    out = []
    w, h = r.get("width") or 0, r.get("height") or 0
    long_side = max(w, h)
    out.append(("分辨率", f"{w}x{h}",
                "PASS" if long_side >= 720 else "FAIL"))

    rfr = r.get("r_frame_rate") or ""
    try:
        num_, den_ = (int(x) for x in rfr.split("/"))
        fps = num_ / den_
    except Exception:
        fps = None
    if fps is None:
        out.append(("帧率", rfr, "FAIL"))
    elif abs(fps - 25) < 0.1 or abs(fps - 30) < 0.1:
        out.append(("帧率", f"{fps:g}", "PASS"))
    else:
        out.append(("帧率", f"{fps:g}", "WARN"))

    out.append(("视频编码", r.get("v_codec") or "-",
                "PASS" if r.get("v_codec") == "h264" else "FAIL"))

    dur = r.get("v_duration") or 0
    if 10 <= dur <= 60:
        out.append(("时长", f"{dur:g}s", "PASS"))
    elif dur > 0:
        out.append(("时长", f"{dur:g}s", "WARN"))
    else:
        out.append(("时长", "-", "FAIL"))

    if r.get("a_codec"):
        out.append(("音频编码", r["a_codec"], "PASS" if r["a_codec"] == "aac" else "WARN"))
        sr = r.get("sample_rate") or 0
        out.append(("采样率", f"{sr}",
                    "PASS" if sr == 48000 else ("WARN" if sr else "FAIL")))
        out.append(("声道", str(r.get("channels")), "PASS"))
    else:
        out.append(("音频轨", "无", "FAIL"))

    diff = r.get("av_diff_ms")
    if diff is None:
        out.append(("音画时长差", "-", "N/A"))
    else:
        out.append(("音画时长差", f"{diff} ms",
                    "PASS" if abs(diff) <= 40 else "WARN"))
    return out


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--json", help="写出 JSON 结果的路径")
    ap.add_argument("--md", help="写出 Markdown 自检表的路径")
    args = ap.parse_args()

    results = [probe(Path(f)) for f in args.files]

    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(
            json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    header = "| 文件 | 检查项 | 实测 | 判定 |\n|---|---|---|---|\n"
    body = ""
    for r in results:
        if "error" in r:
            body += f"| {Path(r['file']).name} | - | {r['error']} | FAIL |\n"
            continue
        for item, val, verdict in checks(r):
            body += f"| {Path(r['file']).name} | {item} | {val} | {verdict} |\n"
    md = header + body
    print(md)

    if args.md:
        Path(args.md).parent.mkdir(parents=True, exist_ok=True)
        Path(args.md).write_text(
            "# 素材入库自检表\n\n> 由 `scripts/probe_media.py` 生成\n\n" + md,
            encoding="utf-8")


if __name__ == "__main__":
    main()
