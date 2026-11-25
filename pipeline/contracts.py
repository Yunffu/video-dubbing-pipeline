"""Pipeline 各环节的数据契约。

设计原则（对齐 JD 职责 3「评估效果、耗时、成本及稳定性」）：
每个环节的产物都自带 **耗时 / 成本 / 是否降级 / 失败原因**，
这样对比表可以直接从运行日志里生成，而不是手工填。
"""
from dataclasses import dataclass, field, asdict
from typing import Any, Optional


@dataclass
class StageStat:
    """一个环节的运行统计。"""
    name: str
    elapsed_ms: int = 0
    provider: str = ""
    model: str = ""
    degraded: bool = False
    error: str = ""
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SubtitleSegment:
    """一条带时间轴的字幕（ASR 与翻译共用此结构）。"""
    index: int
    start: float
    end: float
    text: str
    speaker: str = ""

    @property
    def duration(self) -> float:
        return max(self.end - self.start, 0.0)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ASRResult:
    language: str
    segments: list
    stat: StageStat

    def to_dict(self) -> dict:
        return {"language": self.language,
                "segments": [s.to_dict() for s in self.segments],
                "stat": self.stat.to_dict()}


@dataclass
class TranslationResult:
    """译文结果。segments 与输入一一对应（保序、保数）。"""
    segments: list
    source_language: str
    target_language: str
    stat: StageStat
    warnings: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"source_language": self.source_language,
                "target_language": self.target_language,
                "segments": [s.to_dict() for s in self.segments],
                "stat": self.stat.to_dict(),
                "warnings": self.warnings}


@dataclass
class TTSResult:
    audio_path: str
    language: str
    voice: str
    segments: list          # 每段译文 → 实际音频时长，供时长对齐使用
    stat: StageStat

    def to_dict(self) -> dict:
        return {"audio_path": self.audio_path, "language": self.language,
                "voice": self.voice, "segments": self.segments,
                "stat": self.stat.to_dict()}


@dataclass
class LipSyncResult:
    video_path: str
    stat: StageStat
    detail: Optional[dict] = None

    def to_dict(self) -> dict:
        return {"video_path": self.video_path, "stat": self.stat.to_dict(),
                "detail": self.detail or {}}


@dataclass
class PipelineResult:
    """整条流水线的产物 + 全环节统计。"""
    output_video: str = ""
    stages: list = field(default_factory=list)
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"output_video": self.output_video,
                "stages": [s.to_dict() for s in self.stages],
                "meta": self.meta}

    def to_table(self) -> str:
        """生成对比表用的行：环节 / 耗时 / 是否降级 / 备注。"""
        head = "| 环节 | 耗时(ms) | 方案 | 降级 | 备注 |\n|---|---|---|---|---|"
        rows = []
        for s in self.stages:
            rows.append(f"| {s.name} | {s.elapsed_ms} | {s.provider or '-'} "
                        f"{('(' + s.model + ')') if s.model else ''} | "
                        f"{'是' if s.degraded else '否'} | {s.error or '-'} |")
        return head + "\n" + "\n".join(rows)
