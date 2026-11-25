"""TTS 后端装配与降级。

降级链：edge_tts → （失败）保留原声。

「保留原声」是最诚实的降级：口型/字幕还是中文，但成片可交付、
且 stat.degraded=True 明确告诉下游「这段没配音」。静默失败才是不合格的。
"""
import time
from pathlib import Path

from ..config import TTSConfig
from ..contracts import StageStat, TTSResult
from .edge_tts_provider import EdgeTTSProvider


class TTSService:
    def __init__(self, cfg: TTSConfig):
        self.cfg = cfg
        self.providers = [EdgeTTSProvider(cfg)]

    def describe(self) -> dict:
        return {"config": self.cfg.describe(),
                "providers": [{"name": p.name, "available": p.available()}
                              for p in self.providers]}

    def _voice_for_text(self, text: str, language: str) -> tuple[str, bool]:
        """按**文本实际语种**选音色，而不是只看目标语种。

        实测坑：edge-tts 用英文音色读中文会直接抛 NoAudioReceived（整段音频拿不到）。
        翻译降级时译文仍是中文，若按目标语种选英文音色 → 整批合成全挂。
        所以这里以文本为准，并记录切换次数（= 翻译是否真的换了语种的信号）。
        """
        has_cjk = any("一" <= ch <= "鿿" for ch in text)
        want = "zh" if has_cjk else language
        voice = self.cfg.voice_for(want)
        switched = want != language
        return voice, switched

    def synthesize(self, segments: list, language: str, work_dir: Path) -> TTSResult:
        """segments: 带 index/start/end/text 的字幕段。返回每段音频路径与时长。"""
        t0 = time.time()
        voice = self.cfg.voice_for(language)
        texts = [s.text for s in segments]
        attempts = []

        items = []
        switched = 0
        for s in segments:
            v, sw = self._voice_for_text(s.text, language)
            switched += int(sw)
            items.append({"text": s.text, "voice": v})

        for p in self.providers:
            if not p.available():
                attempts.append({"provider": p.name, "skipped": "依赖未安装"})
                continue
            try:
                raw = p.synthesize(items, work_dir / "tts")
                out = []
                for seg, item in zip(segments, raw):
                    out.append({"index": seg.index, "text": seg.text,
                                "path": item.get("path", ""),
                                "duration": item.get("duration", 0.0),
                                "slot_start": seg.start, "slot_end": seg.end})
                stat = StageStat(name="tts", provider=p.name, model=voice,
                                 elapsed_ms=int((time.time() - t0) * 1000))
                stat.extra = {"segments": len(out), "voice": voice,
                              "attempts": attempts,
                              "voice_switched_segments": switched,
                              "total_audio_s": round(sum(o["duration"] for o in out), 2)}
                return TTSResult(audio_path=str(work_dir / "tts"),
                                 language=language, voice=voice,
                                 segments=out, stat=stat)
            except Exception as e:
                attempts.append({"provider": p.name, "error": str(e)[:200]})
                continue

        stat = StageStat(name="tts", provider="none", degraded=True,
                         error="所有 TTS 后端均失败，保留原声")
        stat.extra = {"attempts": attempts, "segments": 0}
        return TTSResult(audio_path="", language=language, voice=voice,
                         segments=[], stat=stat)
