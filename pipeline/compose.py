"""音轨合成：把逐段 TTS 音频按原时间轴回贴成一整条新音轨，再与视频合成。

参考实现：`bilibili/Index-Translate` 的 `video-dub/index_dub/timeline.py`（Apache-2.0），
它做的就是「配音 wav 变速回贴到原时间槽」；这里按我们的 spec 重写，并额外把
**变速幅度**记成指标（业务上就是「译制腔」的物理来源之一）。

时间轴对齐的两种情况：
  - TTS 比原槽短 → 拉慢（atempo < 1）
  - TTS 比原槽长 → 压快（atempo > 1）
  两端都受 `tempo_min/tempo_max` 限制；超出上限的段**不硬拉**（会明显失真），
  而是保留原速并记录 `overshoot` —— 这个数字就是「译文长度不匹配」的量化证据。
"""
import subprocess
from pathlib import Path

from .media import probe as media_probe


def _atempo_chain(tempo: float) -> str:
    """atempo 单次只支持 0.5~2.0，超出要串联。"""
    if abs(tempo - 1.0) < 0.02:
        return ""
    parts = []
    t = tempo
    while t < 0.5:
        parts.append(0.5); t /= 0.5
    while t > 2.0:
        parts.append(2.0); t /= 2.0
    parts.append(round(t, 4))
    return ",".join(f"atempo={p}" for p in parts)


def build_audio_track(tts_segments: list, out_path: Path,
                      total_duration: float | None = None,
                      tempo_min: float = 0.8, tempo_max: float = 1.25) -> dict:
    """按原时间轴把逐段 TTS 音频拼成一条整轨。

    返回 {ok, error, segments:[{index, slot, tts_dur, tempo, overshoot}], stats}
    """
    usable = [s for s in tts_segments if s.get("path") and float(s.get("duration") or 0) > 0]
    if not usable:
        return {"ok": False, "error": "没有可用的 TTS 音频段", "segments": [], "stats": {}}

    fb = media_probe.ffmpeg_bin()
    if not fb:
        return {"ok": False, "error": "找不到 ffmpeg", "segments": [], "stats": {}}

    cmd = [fb, "-y", "-v", "error"]
    for s in usable:
        cmd += ["-i", s["path"]]

    filters, infos = [], []
    for i, s in enumerate(usable):
        slot = max(float(s.get("slot_end", 0)) - float(s.get("slot_start", 0)), 0.05)
        dur = float(s.get("duration") or 0)
        tempo = dur / slot
        clamped = min(max(tempo, tempo_min), tempo_max)
        overshoot = (tempo > tempo_max) or (tempo < tempo_min)
        delay_ms = int(round(float(s.get("slot_start", 0)) * 1000))
        chain = _atempo_chain(clamped)
        parts = []
        if chain:
            parts.append(chain)
        parts.append(f"adelay={delay_ms}|{delay_ms}")
        # 滤镜链的逗号只分隔滤镜，第一个滤镜直接接在输入标签后面
        filters.append(f"[{i}:a]" + ",".join(parts) + f"[a{i}]")
        infos.append({"index": s.get("index"), "slot": round(slot, 3),
                      "tts_dur": round(dur, 3), "tempo": round(tempo, 3),
                      "applied_tempo": round(clamped, 3),
                      "overshoot": bool(overshoot)})

    mix_in = "".join(f"[a{i}]" for i in range(len(usable)))
    tail = "apad" + (f",atrim=0:{total_duration:.3f}" if total_duration else "")
    filters.append(f"{mix_in}amix=inputs={len(usable)}:duration=longest:normalize=0,"
                   f"{tail}[aout]")

    cmd += ["-filter_complex", ";".join(filters), "-map", "[aout]",
            "-ar", "48000", "-ac", "2", str(out_path)]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        return {"ok": False, "error": (r.stderr or "mix failed")[:400],
                "segments": infos, "stats": {}}

    stretched = [i for i in infos if abs(i["applied_tempo"] - 1.0) > 0.02]
    stats = {
        "segments": len(infos),
        "stretched": len(stretched),
        "overshoot": sum(1 for i in infos if i["overshoot"]),
        # required_* = 想要达到的变速比（可能超出 [tempo_min, tempo_max]）；
        # applied_*  = 实际施加的变速比（已被夹在区间内，永远合法）。
        # 两个都报：required 是「译文长度不匹配」的证据，applied 是「实际做了什么」。
        # （v1 只报 max_tempo，读起来像违反了 25% 上限，其实那是 required 值。）
        "max_required_tempo": max((i["tempo"] for i in infos), default=1.0),
        "min_required_tempo": min((i["tempo"] for i in infos), default=1.0),
        "max_applied_tempo": max((i["applied_tempo"] for i in infos), default=1.0),
        "min_applied_tempo": min((i["applied_tempo"] for i in infos), default=1.0),
        "tempo_bounds": [tempo_min, tempo_max],
        "mean_abs_stretch": round(
            sum(abs(i["applied_tempo"] - 1.0) for i in infos) / max(len(infos), 1), 4),
    }
    return {"ok": True, "error": "", "segments": infos, "stats": stats}


def mux(video: Path, audio: Path, out_path: Path, shortest: bool = True) -> tuple[bool, str]:
    """视频流 copy（不重编码）+ 新音轨 → 成片。

    视频不重编码是刻意的：译制流水线里画面通常不需要改，copy 能省掉一次有损重编码。
    """
    fb = media_probe.ffmpeg_bin()
    if not fb:
        return False, "找不到 ffmpeg"
    cmd = [fb, "-y", "-v", "error", "-i", str(video), "-i", str(audio),
           "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac",
           "-b:a", "160k", "-movflags", "+faststart"]
    if shortest:
        cmd.append("-shortest")
    cmd.append(str(out_path))
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        return False, (r.stderr or "mux failed")[:400]
    return True, ""
