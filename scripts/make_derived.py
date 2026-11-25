#!/usr/bin/env python3
"""从一条干净母版派生难例素材（合成 GT 法）。

母版 = GT。派生件 = 被污染的输入，二者成对即可算 PSNR/SSIM。

用法:
    python scripts/make_derived.py <母版.mp4> --outdir assets/derived --name m1_01

派生件:
    hard_sub_bottom   底部大字硬字幕（字幕擦除 / Inpainting 的常规难例）
    hard_sub_face     字幕压在人脸区域（"擦除会把脸也擦掉"的边界）
    low_bitrate       低码率重编码（超分 SR 的输入）
    noisy             强背景噪声（ASR 识别率下降）
"""
import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Microsoft YaHei,{size},&H00FFFFFF,&H00000000,&H00000000,1,0,0,0,100,100,0,0,1,{outline},0,{align},{ml},{mr},{mv},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,{start},{end},Default,,0,0,0,,{text}
"""

TEXT_LINES = ["这是一条用于测试的中文字幕", "第二行字幕用来压住画面下三分之一"]


def ffmpeg_bin() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        sys.exit("找不到 ffmpeg：把 FFmpeg 的 bin 目录加进 PATH")
    return exe


def run(args: list[str]):
    r = subprocess.run([ffmpeg_bin(), "-y", "-v", "error"] + args,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise SystemExit("ffmpeg 失败:\n" + (r.stderr or ""))


def probe_wh(path: Path) -> tuple[int, int]:
    exe = shutil.which("ffprobe")
    out = subprocess.run(
        [exe, "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height", "-of", "csv=p=0:s=x", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    w, h = out.stdout.strip().split("x")[:2]
    return int(w), int(h)


def write_ass(path: Path, w: int, h: int, size: int, align: int,
              ml: int, mr: int, mv: int, duration: float) -> None:
    body = ASS_HEADER.format(w=w, h=h, size=size, outline=max(2, size // 12),
                              align=align, ml=ml, mr=mr, mv=mv,
                              start="0:00:00.00", end=f"0:00:{duration:05.2f}",
                              text=r"\N".join(TEXT_LINES))
    path.write_text(body, encoding="utf-8")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("source", help="干净母版（同时就是 GT）")
    ap.add_argument("--outdir", default="assets/derived")
    ap.add_argument("--name", required=True, help="素材名前缀，如 m1_01")
    ap.add_argument("--workdir", default="_work/ass")
    args = ap.parse_args()

    src = Path(args.source)
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    Path(args.workdir).mkdir(parents=True, exist_ok=True)

    w, h = probe_wh(src)
    dur = float(subprocess.run(
        [shutil.which("ffprobe"), "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(src)],
        capture_output=True, text=True, encoding="utf-8", errors="replace"
    ).stdout.strip())

    # 1) 底部大字硬字幕
    ass = Path(args.workdir) / f"{args.name}_bottom.ass"
    write_ass(ass, w, h, size=max(48, h // 12), align=2,
              ml=60, mr=60, mv=int(h * 0.08), duration=dur)
    run(["-i", str(src), "-vf", f"ass={ass.as_posix()}",
         "-c:v", "libx264", "-crf", "20", "-c:a", "aac",
         str(outdir / f"{args.name}_hard_sub_bottom.mp4")])

    # 2) 字幕压在人脸区域（画面上部）
    ass2 = Path(args.workdir) / f"{args.name}_face.ass"
    write_ass(ass2, w, h, size=max(48, h // 12), align=8,
              ml=60, mr=60, mv=int(h * 0.30), duration=dur)
    run(["-i", str(src), "-vf", f"ass={ass2.as_posix()}",
         "-c:v", "libx264", "-crf", "20", "-c:a", "aac",
         str(outdir / f"{args.name}_hard_sub_face.mp4")])

    # 3) 低码率（超分 SR 的输入）
    run(["-i", str(src), "-b:v", "400k", "-c:a", "aac",
         str(outdir / f"{args.name}_low_bitrate.mp4")])

    # 4) 强背景噪声（ASR 降级）
    run(["-i", str(src),
         "-f", "lavfi", "-i", "anoisesrc=color=white:amplitude=0.08:d=3600",
         "-filter_complex", "[0:a][1:a]amix=inputs=2:duration=first:weights=1 1[a]",
         "-map", "0:v", "-map", "[a]",
         "-c:v", "copy", "-c:a", "aac",
         str(outdir / f"{args.name}_noisy.mp4")])

    # 5) 嘴部遮挡（半透明矩形压住画面中下部中央）
    box_w, box_h = w // 5, h // 6
    box_x = (w - box_w) // 2
    box_y = int(h * 0.52)
    draw = (f"drawbox=x={box_x}:y={box_y}:w={box_w}:h={box_h}:"
            f"color=black@0.75:t=fill")
    run(["-i", str(src), "-vf", draw,
         "-c:v", "libx264", "-crf", "20", "-c:a", "aac",
         str(outdir / f"{args.name}_mouth_occluded.mp4")])

    derived = [
        {"type": "hard_sub_bottom", "file": f"{args.name}_hard_sub_bottom.mp4",
         "purpose": "字幕擦除常规难例", "metric": "字幕区域 mask 内 PSNR/SSIM（对母版）"},
        {"type": "hard_sub_face", "file": f"{args.name}_hard_sub_face.mp4",
         "purpose": "字幕压脸边界难例", "metric": "同上看擦除是否伤及人脸"},
        {"type": "low_bitrate", "file": f"{args.name}_low_bitrate.mp4",
         "purpose": "超分 SR 的输入", "metric": "SR 前后 PSNR/SSIM（对母版）"},
        {"type": "noisy", "file": f"{args.name}_noisy.mp4",
         "purpose": "ASR 识别率下降难例", "metric": "ASR 字符错误率变化"},
        {"type": "mouth_occluded", "file": f"{args.name}_mouth_occluded.mp4",
         "purpose": "嘴部遮挡难例（LipSync 崩坏边界）", "metric": "口型同步指标 / 人工 MOS"},
    ]
    manifest = {"master": str(src), "master_size": f"{w}x{h}",
                "duration": dur, "derived": derived}
    (outdir / f"{args.name}_derived.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"母版（GT）: {src}  {w}x{h}  {dur:.2f}s")
    for d in derived:
        print(f"  派生: {d['file']}  ({d['purpose']})")


if __name__ == "__main__":
    main()
