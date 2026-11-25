"""离线降级后端。

三级降级（逐级尝试，全部失败才判定不可用）：
1. **Argos-Translate**（离线 NLLB 模型，若已安装且模型可用）
2. **术语表替换**（按 glossary 做词条替换，其余原样保留）
3. **原句透传**（offline_strict=False 时；标记 degraded=True，让下游知道译文不可用）

降级结果一定会在 stat.degraded / warnings 里留痕——对应 JD 职责 3 的稳定性评估。
"""
import time

from ..config import TranslateConfig
from ..contracts import StageStat, SubtitleSegment, TranslationResult


class OfflineProvider:
    """不依赖网络的后端。"""

    name = "offline"

    def __init__(self, cfg: TranslateConfig):
        self.cfg = cfg
        self._argos = None
        self._argos_checked = False

    def available(self) -> bool:
        return True

    def _try_argos(self, text: str, source: str, target: str):
        if not self._argos_checked:
            self._argos_checked = True
            try:
                import argostranslate.translate as at
                self._argos = at
            except Exception:
                self._argos = None
        if self._argos is None:
            return None
        try:
            src = self._argos.get_installed_language_from_code(source)
            tgt = self._argos.get_installed_language_from_code(target)
            if src is None or tgt is None:
                return None
            return self._argos.translate(text, src, tgt)
        except Exception:
            return None

    def translate(self, segments: list, source_language: str,
                  target_language: str) -> TranslationResult:
        started = time.time()
        stat = StageStat(name="translate:offline", provider="offline", model="argos/glossary")
        gloss = self.cfg.glossary()
        out = []
        warnings = []
        used_argos = 0
        strict_miss = 0

        for s in segments:
            text = None
            if self._argos is not None or not self._argos_checked:
                text = self._try_argos(s.text, source_language, target_language)
                if text:
                    used_argos += 1
            if not text:
                original = s.text
                for k, v in gloss.items():
                    if k in original:
                        original = original.replace(k, v)
                text = original
                if self.cfg.offline_strict and text == s.text:
                    # 严格模式：没命中术语表的句子不产出伪译文
                    text = ""
                    strict_miss += 1
            out.append(SubtitleSegment(index=s.index, start=s.start, end=s.end,
                                       text=text, speaker=s.speaker))

        stat.elapsed_ms = int((time.time() - started) * 1000)
        stat.degraded = True
        stat.extra = {"argos_segments": used_argos, "glossary_terms": len(gloss),
                      "strict_miss": strict_miss,
                      "mode": "argos" if used_argos else ("glossary" if gloss else "passthrough")}
        if not used_argos:
            warnings.append("离线降级：未命中 Argos 模型，译文为术语替换/原文透传，不可用于对外交付")
        if strict_miss:
            warnings.append(f"离线严格模式：{strict_miss} 段未命中术语表，译文置空（不产出伪译文）")
        return TranslationResult(segments=out, source_language=source_language,
                                 target_language=target_language, stat=stat,
                                 warnings=warnings)
