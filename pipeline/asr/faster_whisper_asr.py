"""faster-whisper ASR 后端（CTranslate2，CPU 可跑）。

选它的理由（核实见 docs/notes/技术选型检索台账.md）：
  - 官方 CPU 基准：13 分钟音频 int8 单批 1m42s → 25s 素材约 3–4s，可接受；
  - Windows 有 cp311 wheel（ctranslate2 4.9.1 win_amd64），且 Windows 版只有 CPU，正好匹配本机；
  - 自带 VAD（Silero），能把音乐/静音段切掉 —— 真实 VLOG 素材没这一步噪声会被当成语音。

已知短板（要写进 badcase，不要粉饰）：
  - 音乐/混响/多人抢话场景下错字明显（实测 BLG VLOG 上 small 档把「开腔」听成「开空腔」）；
  - 档位越大越准但越慢；large-v3 在 CPU 上不现实。
"""
import time

from ..config import ASRConfig
from ..contracts import ASRResult, StageStat, SubtitleSegment


class FasterWhisperProvider:
    name = "faster_whisper"

    def __init__(self, cfg: ASRConfig):
        self.cfg = cfg
        self._model = None

    def available(self) -> bool:
        try:
            import faster_whisper  # noqa: F401
            return True
        except Exception:
            return False

    def _load(self):
        if self._model is None:
            from faster_whisper import WhisperModel
            self._model = WhisperModel(self.cfg.model, device=self.cfg.device,
                                       compute_type=self.cfg.compute_type)
        return self._model

    def transcribe(self, media: str, language=None) -> ASRResult:
        t0 = time.time()
        model = self._load()
        load_ms = int((time.time() - t0) * 1000)

        lang = language or self.cfg.language
        if lang in ("auto", "", None):
            lang = None

        t1 = time.time()
        segments, info = model.transcribe(
            media,
            language=lang,
            beam_size=self.cfg.beam_size,
            vad_filter=self.cfg.vad,
            initial_prompt=self.cfg.initial_prompt or None,
        )
        segs = []
        for i, s in enumerate(segments):
            text = (s.text or "").strip()
            if not text:
                continue
            segs.append(SubtitleSegment(index=len(segs) + 1,
                                        start=round(float(s.start), 3),
                                        end=round(float(s.end), 3),
                                        text=text))
        elapsed = int((time.time() - t1) * 1000)

        stat = StageStat(name="asr", provider=self.name, model=self.cfg.model,
                         elapsed_ms=elapsed)
        stat.extra = {
            "model_load_ms": load_ms,
            "detect_language": getattr(info, "language", lang),
            "language_probability": round(float(getattr(info, "language_probability", 0) or 0), 3),
            "audio_duration": round(float(getattr(info, "duration", 0) or 0), 2),
            "segments": len(segs),
            "beam_size": self.cfg.beam_size,
            "vad": self.cfg.vad,
            "realtime_factor": round(elapsed / 1000 / max(float(getattr(info, "duration", 0) or 1), 1e-6), 3),
        }
        return ASRResult(language=getattr(info, "language", lang or "unknown"),
                         segments=segs, stat=stat)
