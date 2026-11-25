"""翻译后端装配与自动降级。

配置 TRANSLATE_PROVIDER：
- `deepseek`           只用 DeepSeek
- `openai_compatible`  只用自定义 OpenAI 兼容端点
- `offline`            只用离线降级
- `auto`（默认）       DeepSeek → 自定义端点 → 离线，**任一失败自动降级下一级并留痕**
"""
import time

from ..config import TranslateConfig
from ..contracts import StageStat, TranslationResult
from .offline import OfflineProvider
from .openai_compatible import OpenAICompatibleProvider


class TranslatingService:
    """按配置选后端，失败自动降级，所有降级都会记录在结果里。"""

    def __init__(self, cfg: TranslateConfig):
        self.cfg = cfg
        self.providers = []
        if cfg.provider in ("auto", "deepseek") and cfg.deepseek_api_key:
            self.providers.append(OpenAICompatibleProvider(
                cfg, "deepseek", cfg.deepseek_base_url, cfg.deepseek_api_key,
                cfg.deepseek_model))
        if cfg.provider in ("auto", "openai_compatible") and cfg.openai_api_key:
            self.providers.append(OpenAICompatibleProvider(
                cfg, "openai_compatible", cfg.openai_base_url, cfg.openai_api_key,
                cfg.openai_model))
        if cfg.provider in ("auto", "offline"):
            self.providers.append(OfflineProvider(cfg))
        if not self.providers:
            self.providers.append(OfflineProvider(cfg))

    def describe(self) -> dict:
        return {"config": self.cfg.describe(),
                "providers": [p.name for p in self.providers]}

    def translate(self, segments: list, source_language: str,
                  target_language: str) -> TranslationResult:
        attempts = []
        for p in self.providers:
            if not p.available():
                attempts.append({"provider": p.name, "skipped": "不可用（配置缺失）"})
                continue
            try:
                result = p.translate(segments, source_language, target_language)
                result.stat.extra = dict(result.stat.extra or {})
                result.stat.extra["attempts"] = attempts
                if attempts:
                    result.warnings.insert(0, f"前序后端失败已降级：{attempts}")
                return result
            except Exception as e:
                attempts.append({"provider": p.name, "error": str(e)[:200]})
                continue

        stat = StageStat(name="translate", provider="none", degraded=True,
                         error="所有翻译后端均失败")
        stat.extra = {"attempts": attempts}
        return TranslationResult(segments=list(segments), source_language=source_language,
                                 target_language=target_language, stat=stat,
                                 warnings=[f"所有翻译后端均失败：{attempts}"])
