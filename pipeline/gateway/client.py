"""公司网关接入层：把外部 OpenAI 兼容 API 挂到公司网关上。

场景（对应用户提的「拿着公司的账号，去串联内部的接口，或者把外部 API 接入公司的网关」）：
  - 公司有统一网关（OpenAI 兼容路径 /v1/chat/completions），所有出网流量走它；
  - 凭据是**公司账号**签发的，不能用我们自己的个人 key 直连；
  - 网关可能做**模型白名单**（只放行被采购/批准的模型）、**限流**、**审计**；
  - 网关也可能把请求转到内部接口（内部大模型 / 内部 ASR / 内部 TTS）。

这一层的职责（不多不少）：
  1. **凭据只从环境变量读**，永远不进代码、不进日志（见 audit.redact）；
  2. **模型白名单**：不在白名单的模型 → 本地直接拒（不等网关报错），省一次往返；
  3. **限流**：客户端侧令牌桶 + 最小间隔，避免把公司网关打挂；
  4. **重试与退避**：尊重 Retry-After；401/403 不重试（凭据/权限问题重试无意义）；
  5. **审计**：每次调用落一行 JSONL（谁/何时/case/模型/token/耗时/结果）。

**熔断**：连续 N 次失败后打开熔断器，窗口期内直接失败（快速失败优于拖死整条流水线），
到点自动半开重试一次。
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from .audit import AuditLog, fingerprint


class GatewayError(RuntimeError):
    """网关调用失败（已归类，便于上层决定降级）。"""

    def __init__(self, message: str, kind: str = "unknown", status: int | None = None,
                 retry_after: float | None = None):
        super().__init__(message)
        self.kind = kind          # auth / rate_limit / server / network / blocked / bad_request
        self.status = status
        self.retry_after = retry_after


@dataclass
class GatewayConfig:
    """网关配置（全部来自环境变量，**不含任何密钥明文**）。"""
    base_url: str = field(default_factory=lambda: os.environ.get(
        "GATEWAY_BASE_URL", "").strip().rstrip("/"))
    api_key: str = field(default_factory=lambda: os.environ.get(
        "GATEWAY_API_KEY", "").strip())
    # 网关转发的上游地址（留空 = 网关自己处理，不转发）
    upstream_base_url: str = field(default_factory=lambda: os.environ.get(
        "GATEWAY_UPSTREAM_BASE_URL", "").strip().rstrip("/"))
    allowed_models: list[str] = field(default_factory=lambda: [
        m.strip() for m in os.environ.get("GATEWAY_ALLOWED_MODELS", "").split(",")
        if m.strip()])
    requests_per_minute: int = field(default_factory=lambda: int(
        os.environ.get("GATEWAY_RPM", "0") or 0))
    timeout: int = field(default_factory=lambda: int(
        os.environ.get("GATEWAY_TIMEOUT", "60") or 60))
    max_retries: int = field(default_factory=lambda: int(
        os.environ.get("GATEWAY_MAX_RETRIES", "3") or 3))
    # 熔断：连续失败多少次打开；打开后多少秒半开
    breaker_threshold: int = field(default_factory=lambda: int(
        os.environ.get("GATEWAY_BREAKER_THRESHOLD", "5") or 5))
    breaker_cooldown_s: int = field(default_factory=lambda: int(
        os.environ.get("GATEWAY_BREAKER_COOLDOWN", "60") or 60))
    audit_path: str = field(default_factory=lambda: os.environ.get(
        "GATEWAY_AUDIT_PATH", "results/gateway/audit.jsonl"))
    actor: str = field(default_factory=lambda: os.environ.get(
        "GATEWAY_ACTOR", "local").strip() or "local")

    def model_allowed(self, model: str) -> bool:
        if not self.allowed_models:
            return True          # 没配白名单 = 网关自己管
        return model in self.allowed_models

    def describe(self) -> dict:
        """脱敏视图：可以进报告，绝不含凭据。"""
        return {"base_url": self.base_url, "configured": bool(self.api_key),
                "key_fingerprint": fingerprint(self.api_key),
                "upstream_base_url": self.upstream_base_url,
                "allowed_models": self.allowed_models,
                "requests_per_minute": self.requests_per_minute,
                "timeout": self.timeout, "max_retries": self.max_retries,
                "breaker_threshold": self.breaker_threshold,
                "breaker_cooldown_s": self.breaker_cooldown_s,
                "audit_path": self.audit_path}


class _RateLimiter:
    """最小间隔 + 每分钟配额（线程安全，够用即可，不追求精确令牌桶）。"""

    def __init__(self, rpm: int):
        self.rpm = rpm
        self._lock = threading.Lock()
        self._calls: list[float] = []
        self._last = 0.0

    def wait(self) -> float:
        if self.rpm <= 0:
            return 0.0
        with self._lock:
            now = time.time()
            self._calls = [t for t in self._calls if now - t < 60]
            if len(self._calls) >= self.rpm:
                sleep = 60 - (now - self._calls[0])
            else:
                gap = 60.0 / self.rpm
                sleep = max(0.0, self._last + gap - now)
            if sleep > 0:
                time.sleep(sleep)
            self._last = time.time()
            self._calls.append(self._last)
            return sleep


class _Breaker:
    """熔断器：连续失败 → 打开；冷却后进入半开（放一次探测请求）。"""

    def __init__(self, threshold: int, cooldown_s: int):
        self.threshold = threshold
        self.cooldown_s = cooldown_s
        self.failures = 0
        self.opened_at = 0.0
        self._lock = threading.Lock()

    @property
    def open(self) -> bool:
        with self._lock:
            if self.failures < self.threshold:
                return False
            if time.time() - self.opened_at >= self.cooldown_s:
                self.failures = self.threshold - 1   # 半开：放一次
                return False
            return True

    def record(self, ok: bool, count: bool = True) -> None:
        """记录一次调用结果。

        count=False 用于**限流**：429 是「对方主动限速」，属于预期背压，
        不是网关故障，不该把熔断器打开（否则一次突发流量就让整条链路瘫掉）。
        """
        with self._lock:
            if ok:
                self.failures = 0
                self.opened_at = 0.0
            elif count:
                self.failures += 1
                if self.failures >= self.threshold:
                    self.opened_at = time.time()


class GatewayClient:
    """公司网关的 OpenAI 兼容客户端。"""

    def __init__(self, cfg: GatewayConfig | None = None,
                 audit: AuditLog | None = None):
        self.cfg = cfg or GatewayConfig()
        self.audit = audit or AuditLog(self.cfg.audit_path, actor=self.cfg.actor)
        self.limiter = _RateLimiter(self.cfg.requests_per_minute)
        self.breaker = _Breaker(self.cfg.breaker_threshold, self.cfg.breaker_cooldown_s)

    # ---------------------------------------------------------------- 请求

    def chat(self, messages: list[dict], model: str, *, stage: str = "gateway",
             case_id: str | None = None, temperature: float = 0.3,
             max_tokens: int | None = None, extra_body: dict | None = None) -> dict:
        """走网关发一次 chat 请求。失败抛 GatewayError（已归类）。"""
        # 1) 白名单在本地拦 —— 不给网关一次白跑的往返
        if not self.cfg.model_allowed(model):
            msg = (f"模型 {model!r} 不在网关白名单内"
                   f"（允许：{self.cfg.allowed_models or '未配置'}）")
            self.audit.call(stage=stage, case_id=case_id, model=model,
                            status="blocked", error=msg)
            raise GatewayError(msg, kind="blocked")

        if not self.cfg.base_url or not self.cfg.api_key:
            msg = "网关未配置（GATEWAY_BASE_URL / GATEWAY_API_KEY）"
            self.audit.call(stage=stage, case_id=case_id, model=model,
                            status="unconfigured", error=msg)
            raise GatewayError(msg, kind="unconfigured")

        # 2) 熔断打开 → 快速失败，不拖死整条流水线
        if self.breaker.open:
            msg = f"网关熔断中（连续失败 {self.breaker.failures} 次），快速失败"
            self.audit.call(stage=stage, case_id=case_id, model=model,
                            status="circuit_open", error=msg)
            raise GatewayError(msg, kind="circuit_open")

        body = {"model": model, "messages": messages, "temperature": temperature}
        if max_tokens:
            body["max_tokens"] = max_tokens
        if extra_body:
            body.update(extra_body)
        payload = json.dumps(body).encode("utf-8")

        last_err: GatewayError | None = None
        for attempt in range(self.cfg.max_retries + 1):
            self.limiter.wait()
            t0 = time.time()
            try:
                data = self._post(payload)
                latency = int((time.time() - t0) * 1000)
                self.breaker.record(True)
                tokens = data.get("usage") or {}
                self.audit.call(stage=stage, case_id=case_id, model=model,
                                status="ok", latency_ms=latency,
                                tokens={"prompt": tokens.get("prompt_tokens"),
                                        "completion": tokens.get("completion_tokens"),
                                        "total": tokens.get("total_tokens")})
                return data
            except GatewayError as e:
                last_err = e
                latency = int((time.time() - t0) * 1000)
                # 鉴权/权限类不重试：重试不会让错 key 变对 key
                if e.kind in ("auth", "blocked", "bad_request"):
                    self.breaker.record(False)
                    self.audit.call(stage=stage, case_id=case_id, model=model,
                                    status=e.kind, latency_ms=latency, error=str(e))
                    raise
                self.breaker.record(False, count=e.kind != "rate_limit")
                self.audit.call(stage=stage, case_id=case_id, model=model,
                                status=e.kind, latency_ms=latency, error=str(e),
                                extra={"attempt": attempt + 1})
                if attempt >= self.cfg.max_retries:
                    break
                delay = e.retry_after if e.retry_after else min(2 ** attempt, 8)
                time.sleep(min(delay, 30))
        assert last_err is not None
        raise last_err

    def _post(self, payload: bytes) -> dict:
        url = f"{self.cfg.base_url}/chat/completions"
        headers = {"Content-Type": "application/json",
                   "Authorization": f"Bearer {self.cfg.api_key}"}
        req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.cfg.timeout) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                return json.loads(body)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")[:300]
            if e.code in (401, 403):
                raise GatewayError(f"HTTP {e.code}: {body}", kind="auth", status=e.code)
            if e.code == 429:
                ra = e.headers.get("Retry-After")
                try:
                    ra_s = float(ra)
                except (TypeError, ValueError):
                    ra_s = None
                raise GatewayError(f"HTTP 429: {body}", kind="rate_limit",
                                   status=429, retry_after=ra_s)
            if e.code == 400:
                raise GatewayError(f"HTTP 400: {body}", kind="bad_request", status=400)
            if 500 <= e.code < 600:
                raise GatewayError(f"HTTP {e.code}: {body}", kind="server", status=e.code)
            raise GatewayError(f"HTTP {e.code}: {body}", kind="unknown", status=e.code)
        except urllib.error.URLError as e:
            raise GatewayError(f"网络不可达: {e.reason}", kind="network")
        except TimeoutError:
            raise GatewayError(f"请求超时（{self.cfg.timeout}s）", kind="timeout")
        except json.JSONDecodeError as e:
            raise GatewayError(f"响应不是 JSON: {e}", kind="bad_response")

    # ---------------------------------------------------------------- 其它

    def health(self) -> dict:
        """网关连通性自检（不花 token）。"""
        url = f"{self.cfg.base_url or 'http://localhost:0'}/models"
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {self.cfg.api_key}"}, method="GET")
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=min(self.cfg.timeout, 10)) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                ok = True
                err = None
        except urllib.error.HTTPError as e:
            ok = False
            err = f"HTTP {e.code}"
        except Exception as e:
            ok = False
            err = str(e)[:200]
        ms = int((time.time() - t0) * 1000)
        self.audit.call(stage="gateway_health", model="-",
                        status="ok" if ok else "fail", latency_ms=ms, error=err)
        return {"reachable": ok, "error": err, "latency_ms": ms,
                "config": self.cfg.describe()}
