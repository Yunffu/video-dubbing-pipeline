"""请求审计日志。

为什么单独一个模块：
  「接内网」这件事里，**可审计**和**不泄密**是两条硬要求 ——
  公司网关后面挂着外部 API 的凭据，日志一旦把 Authorization 头或 key 落盘，
  就是在制造事故。所以这里的规矩是：
    1. **只记元数据**：谁、何时、哪个 case、哪个模型、多少 token、多久、什么结果；
    2. **不记内容**：请求体/响应体默认不落盘（可开关，但默认关）；
    3. **不记凭据**：任何 key 在写盘前必须过 `redact()`，且 key 只以「指纹」形式出现。

落盘格式：JSONL，一行一条，便于 grep / 追加 / 被 ELK 收走。
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path
from typing import Any

# 任何看起来像凭据的键，落盘前一律换成指纹
_SECRET_KEYS = {"authorization", "api_key", "apikey", "key", "token",
                "secret", "password", "cookie", "set-cookie"}


def fingerprint(secret: str | None) -> str:
    """把凭据变成**不可逆指纹**：能对账「是不是同一把 key」，但看不出 key 本身。

    sha256 前 8 位；空值返回 '-'。
    """
    if not secret:
        return "-"
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()[:8]


def redact(obj: Any) -> Any:
    """递归脱敏：命中敏感键名的值替换成指纹。"""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if str(k).lower() in _SECRET_KEYS:
                out[k] = fingerprint(str(v)) if isinstance(v, str) else "***"
            else:
                out[k] = redact(v)
        return out
    if isinstance(obj, list):
        return [redact(v) for v in obj]
    return obj


class AuditLog:
    """线程安全的 JSONL 审计写入器。

    写失败**不能影响主流程**（审计是观测手段，不是业务依赖），
    所以所有异常都在内部吞掉，只在 `errors` 计数里体现。
    """

    def __init__(self, path: Path | str, actor: str = "local",
                 record_body: bool = False):
        self.path = Path(path)
        self.actor = actor
        self.record_body = record_body
        self.errors = 0
        self._lock = threading.Lock()

    def write(self, **fields: Any) -> dict:
        rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
               "actor": self.actor}
        rec.update(redact(fields))
        line = json.dumps(rec, ensure_ascii=False)
        with self._lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except Exception:
                self.errors += 1
        return rec

    def call(self, *, stage: str, model: str, status: str,
             latency_ms: int | None = None, tokens: dict | None = None,
             error: str | None = None, case_id: str | None = None,
             extra: dict | None = None) -> dict:
        """一次模型调用的标准审计记录。"""
        return self.write(
            kind="model_call", stage=stage, case_id=case_id, model=model,
            status=status, latency_ms=latency_ms, tokens=tokens or {},
            error=(error or "")[:300] or None, extra=redact(extra or {}))
