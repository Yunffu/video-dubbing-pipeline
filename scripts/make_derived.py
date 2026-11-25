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
              ml: int, mr: int, mv: int, duration: float,
              pos: tuple[int, int] | None = None) -> None:
    text = r"\N".join(TEXT_LINES)
    if pos is not None:
        # align=5（正中）+ \pos 覆盖标签：把文字块钉在指定坐标，
        # 否则 ASS 只能相对整屏定位，没法把字幕放到人脸嘴上。
        text = r"{\pos(%d,%d)}%s" % (pos[0], pos[1], text)
    body = ASS_HEADER.format(w=w, h=h, size=size, outline=max(2, size // 12),
                              align=align, ml=ml, mr=mr, mv=mv,
                              start="0:00:00.00", end=f"0:00:{duration:05.2f}",
                              text=text)
    path.write_text(body, encoding="utf-8")


def detect_face_anchor(video: Path) -> dict:
    """在母版上找主脸，返回字幕该压在哪。

    用 YuNet 取最大人脸框 + 嘴部关键点（landmark 3/4 的中点）。
    为什么不用整屏坐标：人脸字幕的意义就是「字挡住脸」，
    之前这批素材把字幕烧在画面顶部（align=8 + mv=0.3h），
    名叫 hard_sub_face 其实没压到脸，等于没测到难点。

    取多帧中位数：人脸会动，取中位框比取首帧稳。
    """
    import cv2
    import numpy as np
    from check_face import model_path, sample_frames, landmarks_of

    frames, _, _ = sample_frames(video, 16)
    if not frames:
        return {"found": False, "error": "读不到帧"}
    boxes, mouths = [], []
    for _, fr in frames:
        h, w = fr.shape[:2]
        det = cv2.FaceDetectorYN_create(model_path(), "", (w, h), 0.6, 0.3, 5000)
        _, faces = det.detect(fr)
        if faces is None or not len(faces):
            continue
        b = max(faces, key=lambda x: x[2] * x[3])
        lm = landmarks_of(b)
        boxes.append([int(v) for v in b[:4]])
        mouths.append([(lm[3][0] + lm[4][0]) / 2, (lm[3][1] + lm[4][1]) / 2])
    if not boxes:
        return {"found": False, "error": "检不到人脸"}
    bx, by, bw, bh = np.median(np.array(boxes), axis=0).astype(int)
    mx, my = np.median(np.array(mouths), axis=0)

    # 字号随脸高缩放：字要盖得住脸，但也不至于大到离谱
    size = int(max(16, min(bh * 0.30, max(bw * 0.5, bh * 0.30))))
    size = max(16, min(size, 480))
    block_h = size * 2 * 1.25          # 两行文字块高度
    # 文字块中心压到嘴部略下方 → 覆盖嘴和下巴
    cy = int(my + block_h * 0.12)
    cx = int(mx)
    return {"found": True, "box": [int(bx), int(by), int(bw), int(bh)],
            "mouth": [int(mx), int(my)], "size": size,
            "pos": (cx, cy), "block_h": block_h,
            "note": "位置 = 最大人脸的嘴部关键点（多帧中位）"}


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("source", help="干净母版（同时就是 GT）")
    ap.add_argument("--outdir", default="assets/derived")
    ap.add_argument("--name", required=True, help="素材名前缀，如 m1_01")
    ap.add_argument("--workdir", default="_work/ass")
    ap.add_argument("--only", help="只生成指定派生件，逗号分隔："
                                   "hard_sub_bottom,hard_sub_face,low_bitrate,noisy,mouth_occluded")
    args = ap.parse_args()

    only = {s.strip() for s in args.only.split(",")} if args.only else None

    def want(kind: str) -> bool:
        return only is None or kind in only

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
    if want("hard_sub_bottom"):
        ass = Path(args.workdir) / f"{args.name}_bottom.ass"
        write_ass(ass, w, h, size=max(48, h // 12), align=2,
                  ml=60, mr=60, mv=int(h * 0.08), duration=dur)
        run(["-i", str(src), "-vf", f"ass={ass.as_posix()}",
             "-c:v", "libx264", "-crf", "20", "-c:a", "aac",
             str(outdir / f"{args.name}_hard_sub_bottom.mp4")])

    # 2) 字幕压在人脸区域（真的压在嘴/下巴上）
    anchor: dict = {}
    if want("hard_sub_face"):
        anchor = detect_face_anchor(src)
        ass2 = Path(args.workdir) / f"{args.name}_face.ass"
        if anchor.get("found"):
            write_ass(ass2, w, h, size=anchor["size"], align=5,
                      ml=60, mr=60, mv=0, duration=dur, pos=anchor["pos"])
        else:
            # 检不到脸 → 退回顶部居中，但要在产物里留痕，不能静默降级
            print(f"[warn] {args.name}: 检不到人脸（{anchor.get('error')}），"
                  f"face 字幕退回顶部居中 —— 该 case 不代表人脸难例")
            write_ass(ass2, w, h, size=max(48, h // 12), align=8,
                      ml=60, mr=60, mv=int(h * 0.30), duration=dur)
        run(["-i", str(src), "-vf", f"ass={ass2.as_posix()}",
             "-c:v", "libx264", "-crf", "20", "-c:a", "aac",
             str(outdir / f"{args.name}_hard_sub_face.mp4")])

    # 3) 低码率（超分 SR 的输入）
    if want("low_bitrate"):
        run(["-i", str(src), "-b:v", "400k", "-c:a", "aac",
             str(outdir / f"{args.name}_low_bitrate.mp4")])

    # 4) 强背景噪声（ASR 降级）
    if want("noisy"):
        run(["-i", str(src),
             "-f", "lavfi", "-i", "anoisesrc=color=white:amplitude=0.08:d=3600",
             "-filter_complex", "[0:a][1:a]amix=inputs=2:duration=first:weights=1 1[a]",
             "-map", "0:v", "-map", "[a]",
             "-c:v", "copy", "-c:a", "aac",
             str(outdir / f"{args.name}_noisy.mp4")])

    # 5) 嘴部遮挡（半透明矩形压住画面中下部中央）
    if want("mouth_occluded"):
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
         "purpose": "字幕压脸边界难例", "metric": "同上看擦除是否伤及人脸",
         "subtitle_anchor": anchor if want("hard_sub_face") and anchor.get("found") else None},
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
