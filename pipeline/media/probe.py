"""定位 ffmpeg / ffprobe，并读取媒体信息。

为什么自己写而不是直接 `shutil.which`：
  1. Windows 上 ffmpeg 常常不在 PATH（本机就踩过：`scripts/normalize.py` 直接 SystemExit 退出，
     而 `~/.agent-reach/tools/ffmpeg/bin` 里只有 ffmpeg、没有 ffprobe）；
  2. 坏元数据时 `ffprobe -print_format json` 可能直接失败 —— 这正是「真实素材会让 AI 代码崩」
     的典型场景，所以这里必须有**兜底解析**：改用 `ffmpeg -i` 读 stderr。

统一入口：probe(path) → dict，永远不抛异常，失败也返回带 error 的字典。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# 常见安装位置（按优先级）
_CANDIDATE_DIRS = [
    r"D:\Dev\ffmpeg\bin",
    str(Path.home() / ".agent-reach" / "tools" / "ffmpeg" / "bin"),
    str(Path.home() / "AppData" / "Roaming" / "bilibili" / "ffmpeg"),
]

# `ffmpeg -i` 输出里的关键字段
_RE_DURATION = re.compile(r"Duration:\s*(\d+):(\d\d):(\d\d\.\d+)")
_RE_VIDEO = re.compile(r"Stream #\d+:\d+.*?: Video:\s*([A-Za-z0-9_]+).*?(\d{2,5})x(\d{2,5})")
_RE_FPS = re.compile(r"(\d+(?:\.\d+)?)\s*fps")
_RE_AUDIO = re.compile(r"Stream #\d+:\d+.*?: Audio:\s*([A-Za-z0-9_]+).*?(\d+)\s*Hz")
_RE_NV = re.compile(r"Video:\s*([A-Za-z0-9_]+).*?(\d{2,5})x(\d{2,5}).*?,\s*([\d.]+)\s*fps")


def _which(name: str) -> str | None:
    hit = shutil.which(name)
    if hit:
        return hit
    for d in _CANDIDATE_DIRS:
        exe = Path(d) / (name + ".exe")
        if exe.exists():
            return str(exe)
    return None


def ffmpeg_bin() -> str | None:
    return os.environ.get("FFMPEG_BIN") or _which("ffmpeg")


def ffprobe_bin() -> str | None:
    return os.environ.get("FFPROBE_BIN") or _which("ffprobe")


def run(cmd: list[str], timeout: int = 600) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


def _parse_ffmpeg_stderr(text: str) -> dict:
    """从 `ffmpeg -i` 的 stderr 里抠出能用的信息（ffprobe 挂了时的兜底）。"""
    out: dict = {"source": "ffmpeg_stderr", "raw_len": len(text)}
    m = _RE_DURATION.search(text)
    if m:
        h, mi, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
        out["duration"] = h * 3600 + mi * 60 + s
    mv = _RE_VIDEO.search(text)
    if mv:
        out["video_codec"] = mv.group(1)
        out["width"] = int(mv.group(2))
        out["height"] = int(mv.group(3))
    mf = _RE_FPS.search(text)
    if mf:
        out["fps"] = float(mf.group(1))
    ma = _RE_AUDIO.search(text)
    if ma:
        out["audio_codec"] = ma.group(1)
        out["sample_rate"] = int(ma.group(2))
    out["has_video"] = "Video:" in text
    out["has_audio"] = "Audio:" in text
    if "does not contain any stream" in text or "Invalid data found" in text:
        out["error"] = "容器损坏或无有效流"
    return out


def probe(path) -> dict:
    """读取媒体信息。ffprobe 优先，失败则用 ffmpeg stderr 兜底。

    返回统一形状：duration / width / height / fps / has_video / has_audio /
    video_codec / audio_codec / sample_rate / streams / error / source
    """
    p = Path(path)
    if not p.exists():
        return {"file": str(p), "error": "文件不存在", "source": "none"}

    fp = ffprobe_bin()
    if fp:
        cmd = [fp, "-v", "error", "-print_format", "json",
               "-show_format", "-show_streams", str(p)]
        try:
            r = run(cmd, timeout=120)
            data = json.loads(r.stdout or "{}")
        except Exception as e:
            data = {}
            err = f"ffprobe 解析失败: {e}"
        else:
            err = None
        if data:
            fmt = data.get("format", {})
            streams = data.get("streams", [])
            v = next((s for s in streams if s.get("codec_type") == "video"), None)
            a = next((s for s in streams if s.get("codec_type") == "audio"), None)
            out = {
                "file": str(p),
                "source": "ffprobe",
                "duration": float(fmt.get("duration") or 0) or None,
                "size_bytes": int(fmt.get("size") or 0),
                "format_name": fmt.get("format_name"),
                "has_video": v is not None,
                "has_audio": a is not None,
                "streams": len(streams),
                "audio_streams": sum(1 for s in streams if s.get("codec_type") == "audio"),
            }
            if v:
                out["width"] = v.get("width")
                out["height"] = v.get("height")
                out["video_codec"] = v.get("codec_name")
                out["fps"] = _parse_rate(v.get("avg_frame_rate")) or _parse_rate(v.get("r_frame_rate"))
                out["pix_fmt"] = v.get("pix_fmt")
            if a:
                out["audio_codec"] = a.get("codec_name")
                out["sample_rate"] = int(a.get("sample_rate") or 0) or None
                out["channels"] = a.get("channels")
            return out
    else:
        err = "找不到 ffprobe"

    fb = ffmpeg_bin()
    if not fb:
        return {"file": str(p), "error": err or "找不到 ffmpeg/ffprobe", "source": "none"}
    r = run([fb, "-hide_banner", "-i", str(p)], timeout=120)
    out = _parse_ffmpeg_stderr(r.stderr or "")
    out["file"] = str(p)
    out["error"] = out.get("error") or err
    return out


def _parse_rate(rate: str | None) -> float | None:
    """'30000/1001' → 29.97"""
    if not rate:
        return None
    try:
        if "/" in rate:
            num, den = rate.split("/")
            den = float(den)
            if den == 0:
                return None
            return round(float(num) / den, 3)
        return round(float(rate), 3)
    except Exception:
        return None


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="读取媒体信息（ffprobe 优先，ffmpeg 兜底）")
    ap.add_argument("media")
    ap.add_argument("--json")
    a = ap.parse_args()
    res = probe(a.media)
    if a.json:
        Path(a.json).write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(res, ensure_ascii=False, indent=2))
