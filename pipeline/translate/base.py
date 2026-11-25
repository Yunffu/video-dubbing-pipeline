"""翻译 provider 抽象。

任何接入方式都实现同一个协议，Pipeline 只认协议不认厂商 → 换模型只改配置。
"""
from typing import Protocol

from ..contracts import StageStat, SubtitleSegment, TranslationResult


class TranslationProvider(Protocol):
    """翻译后端协议。"""

    name: str

    def available(self) -> bool:
        """是否可用（配置齐全 / 依赖就绪）。"""

    def translate(self, segments: list, source_language: str,
                  target_language: str) -> TranslationResult:
        """把带时间轴的字幕段翻译到目标语种，返回一一对应的译文段。"""
