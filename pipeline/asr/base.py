"""ASR provider 抽象：Pipeline 只认协议，换模型只改配置。"""
from typing import Optional, Protocol

from ..contracts import ASRResult


class ASRProvider(Protocol):
    """语音识别后端协议。"""

    name: str

    def available(self) -> bool:
        """依赖/权重是否就绪（不触发重活）。"""

    def transcribe(self, media: str, language: Optional[str] = None) -> ASRResult:
        """把媒体转成带时间轴的字幕段。失败应抛异常，由 Service 负责降级。"""
