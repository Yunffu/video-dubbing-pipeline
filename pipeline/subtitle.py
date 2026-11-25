"""字幕生成与烧录（ASS）。

为什么用 ASS 而不是 SRT：
  - ASS 能精确控制字号/描边/位置，**烧录后不会遮住画面主体**（译制片的硬要求）；
  - ASS 自带时间轴微调（\\t），后面做「对齐口型」时可以在字幕层面补偿。

烧录走 ffmpeg 的 libass（gyan 完整版带）。如果烧录失败，spec 里的降级路径是
「只输出外挂字幕（.ass/.srt），不烧录」—— 成片仍然可用，只是不能直接发。
"""
import subprocess
from pathlib import Path

from .media import probe as media_probe


def _fmt(t: float) -> str:
    """ASS 时间格式 h:mm:ss.cc"""
    if t < 0:
        t = 0
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def build_ass(segments: list, out_path: Path, width: int = 1280, height: int = 720,
              font: str = "Microsoft YaHei", font_size: int = 42) -> Path:
    """segments: 带 start/end/text 的字幕段（时间轴沿用源片）。"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    head = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
        "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
        "MarginR, MarginV, Encoding",
        # 主色白、副色黄(卡拉OK用不到)、描边黑、粗体、Alignment=2 底部居中
        f"Style: Main,{font},{font_size},&H00FFFFFF,&H000000FF,&H00000000,&H80000000,"
        "-1,0,0,0,100,100,0,0,1,2.4,1.2,2,40,40,32,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    lines = list(head)
    for i, s in enumerate(segments, 1):
        text = (getattr(s, "text", "") or "").replace("\n", "\\N")
        lines.append(
            f"Dialogue: 0,{_fmt(s.start)},{_fmt(s.end)},Main,,0,0,0,,{text}")
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_path


def build_srt(segments: list, out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)

    def ts(t: float) -> str:
        h = int(t // 3600); m = int((t % 3600) // 60); s = t % 60
        return f"{h:02d}:{m:02d}:{s:06.3f}".replace(".", ",")

    rows = []
    for i, s in enumerate(segments, 1):
        rows.append(f"{i}\n{ts(s.start)} --> {ts(s.end)}\n{s.text}\n")
    out_path.write_text("\n".join(rows), encoding="utf-8")
    return out_path


def burn_in(video: Path, ass_path: Path, out_path: Path,
            audio: Path | None = None) -> tuple[bool, str]:
    """把 ASS 烧录进画面。audio 为 None 时保留原音轨。

    返回 (成功?, 错误信息)。失败不抛异常 —— 由上层按 spec 降级。
    """
    fb = media_probe.ffmpeg_bin()
    if not fb:
        return False, "找不到 ffmpeg"
    # 字幕路径交给 ffmpeg 的 filter 参数时，`:` `\` `'` 都要转义，
    # 而且路径里带中文/空格时 ffmpeg 的 filter 解析器仍会挑眉。
    # 最稳的做法：**切到字幕所在目录，用纯文件名引用**，其余路径给绝对路径。
    ass_path = Path(ass_path)
    cwd = ass_path.resolve().parent
    cmd = [fb, "-y", "-v", "error", "-i", str(Path(video).resolve())]
    if audio:
        cmd += ["-i", str(Path(audio).resolve())]
    vf = "ass=filename=" + ass_path.name.replace(":", "\\:").replace("'", "\\'")
    cmd += ["-vf", vf]
    if audio:
        cmd += ["-map", "0:v", "-map", "1:a"]
    else:
        cmd += ["-map", "0:v", "-map", "0:a?"]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "aac", "-b:a", "128k", str(Path(out_path).resolve())]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=str(cwd))
    if r.returncode != 0:
        return False, (r.stderr or "burn-in failed")[:400]
    return True, ""
