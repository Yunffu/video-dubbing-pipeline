"""OpenAI 兼容协议的翻译后端。

DeepSeek、Kimi（Moonshot）、通义（DashScope compatible-mode）、智谱 GLM、
Kimi K2、本地 vLLM/Ollama 的 OpenAI 兼容端点都走这一个实现——差异只有
base_url + model + key。刻意只用标准库 urllib，避免为一次 HTTP 调用引入 SDK 依赖。
"""
import json
import re
import time
import urllib.error
import urllib.request

from ..config import TranslateConfig
from ..contracts import StageStat, SubtitleSegment, TranslationResult

SYSTEM_PROMPT = (
    "你是视频字幕本地化译者。把字幕逐条翻译成目标语言，要求：\n"
    "1. 一条对应一行，保留行首编号，条数必须与输入一致，不多不少；\n"
    "2. 口语化、简洁，符合说话人口吻，不要书面语、不要解释、不要引号；\n"
    "3. 保持每行长度尽量接近原句（字幕场景对时长敏感）；\n"
    "4. 专有名词按给定术语表翻译；\n"
    "5. 只输出译文，不要任何前后说明。"
)


class ProviderError(RuntimeError):
    """后端调用失败（超时、鉴权、返回异常等）。"""


class OpenAICompatibleProvider:
    """任意 OpenAI 兼容 /chat/completions 端点的翻译后端。"""

    def __init__(self, cfg: TranslateConfig, name: str, base_url: str,
                 api_key: str, model: str):
        self.cfg = cfg
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model

    def available(self) -> bool:
        return bool(self.api_key and self.base_url and self.model)

    def _build_prompt(self, segments: list, source_language: str,
                      target_language: str) -> str:
        langs = f"[{source_language} → {target_language}]"
        gloss = self.cfg.glossary()
        lines = [langs]
        if gloss:
            terms = "\n".join(f"- {k} → {v}" for k, v in list(gloss.items())[:100])
            lines.append(f"术语表：\n{terms}")
        lines.append("输入：")
        for s in segments:
            lines.append(f"{s.index}\t{s.text}")
        lines.append("输出：")
        return "\n".join(lines)

    def _post(self, payload: dict) -> dict:
        url = f"{self.base_url}/chat/completions"
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self.api_key}",
                     "User-Agent": "video-dubbing-pipeline/0.1"},
            method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.cfg.timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:300]
            raise ProviderError(f"HTTP {e.code}: {body}") from e
        except Exception as e:
            raise ProviderError(f"{type(e).__name__}: {e}") from e

    def translate(self, segments: list, source_language: str,
                  target_language: str) -> TranslationResult:
        started = time.time()
        stat = StageStat(name=f"translate:{self.name}", provider=self.name,
                         model=self.model)
        prompt = self._build_prompt(segments, source_language, target_language)
        payload = {
            "model": self.model,
            "temperature": 0.2,
            "max_tokens": max(512, int(len(prompt) * 1.5)),
            "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                         {"role": "user", "content": prompt}],
        }

        last_err = ""
        for attempt in range(self.cfg.max_retries + 1):
            try:
                data = self._post(payload)
                break
            except ProviderError as e:
                last_err = str(e)
                if attempt < self.cfg.max_retries:
                    time.sleep(1.5 * (attempt + 1))
        else:
            stat.elapsed_ms = int((time.time() - started) * 1000)
            stat.error = last_err
            stat.degraded = True
            raise ProviderError(last_err)

        usage = data.get("usage") or {}
        content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
        lines = [ln.strip() for ln in content.strip().splitlines() if ln.strip()]
        parsed = {}
        for ln in lines:
            m = re.match(r"^\s*\[?(\d+)\]?[\.\、:：\)\s]\s*(.+)$", ln)
            if m:
                parsed[int(m.group(1))] = m.group(2).strip()

        out = []
        warnings = []
        for s in segments:
            text = parsed.get(s.index)
            if not text:
                text = s.text
                warnings.append(f"segment {s.index} 未解析到译文，已回退原文")
            out.append(SubtitleSegment(index=s.index, start=s.start, end=s.end,
                                       text=text, speaker=s.speaker))

        stat.elapsed_ms = int((time.time() - started) * 1000)
        stat.extra = {
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "total_tokens": usage.get("total_tokens"),
            "segments_in": len(segments),
            "segments_out": len(out),
            "parsed_lines": len(parsed),
        }
        return TranslationResult(segments=out, source_language=source_language,
                                 target_language=target_language, stat=stat,
                                 warnings=warnings)
