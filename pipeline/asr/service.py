"""ASR 后端装配与自动降级。

降级链：faster_whisper → openai_whisper（torch CPU 版，本机已有 torch）→ 空转写（标 degraded）。

为什么要有降级：真实素材链路里 ASR 是第一道闸，它一挂整条 Pipeline 就没法跑。
降级不是「假装成功」—— 结果里 stat.degraded=True、error 有原因，下游能识别。
"""
import time

from ..config import ASRConfig
from ..contracts import ASRResult, StageStat
from .faster_whisper_asr import FasterWhisperProvider


class OpenAIWhisperProvider:
    """openai-whisper 兜底（torch CPU 版已装，但本项目默认不装这个包）。"""

    name = "openai_whisper"

    def __init__(self, cfg: ASRConfig):
        self.cfg = cfg
        self._model = None

    def available(self) -> bool:
        try:
            import whisper  # noqa: F401
            return True
        except Exception:
            return False

    def _load(self):
        if self._model is None:
            import whisper
            self._model = whisper.load_model(self.cfg.model)
        return self._model

    def transcribe(self, media: str, language=None) -> ASRResult:
        from ..contracts import SubtitleSegment
        t0 = time.time()
        model = self._load()
        lang = language or self.cfg.language
        raw = model.transcribe(media, language=None if lang in ("auto", "", None) else lang,
                               fp16=False)
        segs = []
        for s in raw.get("segments", []):
            text = (s.get("text") or "").strip()
            if not text:
                continue
            segs.append(SubtitleSegment(index=len(segs) + 1,
                                        start=round(float(s["start"]), 3),
                                        end=round(float(s["end"]), 3),
                                        text=text))
        stat = StageStat(name="asr", provider=self.name, model=self.cfg.model,
                         elapsed_ms=int((time.time() - t0) * 1000))
        stat.extra = {"segments": len(segs)}
        return ASRResult(language=raw.get("language", "unknown"), segments=segs, stat=stat)


class ASRService:
    """选后端 + 失败自动降级 + 全程留痕。"""

    def __init__(self, cfg: ASRConfig):
        self.cfg = cfg
        self.providers = [FasterWhisperProvider(cfg), OpenAIWhisperProvider(cfg)]

    def describe(self) -> dict:
        return {"config": self.cfg.describe(),
                "providers": [{"name": p.name, "available": p.available()}
                              for p in self.providers]}

    def transcribe(self, media: str, language=None) -> ASRResult:
        attempts = []
        for p in self.providers:
            if not p.available():
                attempts.append({"provider": p.name, "skipped": "依赖未安装"})
                continue
            try:
                res = p.transcribe(media, language)
                res.stat.extra = dict(res.stat.extra or {})
                res.stat.extra["attempts"] = attempts
                if attempts:
                    res.stat.extra["degraded_from"] = [a.get("provider") for a in attempts]
                    res.stat.degraded = True
                return res
            except Exception as e:
                attempts.append({"provider": p.name, "error": str(e)[:200]})
                continue

        stat = StageStat(name="asr", provider="none", degraded=True,
                         error="所有 ASR 后端均失败")
        stat.extra = {"attempts": attempts}
        return ASRResult(language="unknown", segments=[], stat=stat)
