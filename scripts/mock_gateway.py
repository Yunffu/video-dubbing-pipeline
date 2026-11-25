#!/usr/bin/env python3
"""本地模拟「公司网关」，用来演示/验证接入层的失败处理。

**不联外网、不需要真凭据**，就能把公司网关最常见的四种行为复现出来：

  1. **鉴权失败**：请求头没有正确 Bearer → 401
  2. **限流**     ：短时间请求数超配额 → 429 + Retry-After
  3. **模型白名单**：请求了没被批准的模型 → 403
  4. **上游转发** ：允许的请求 → 转发给「上游」（本脚本内置一个假翻译服务）

用法:
    # 起网关（前台）
    python scripts/mock_gateway.py --port 8790 --rpm 5 --allowed-models deepseek-chat,gpt-4o-mini

    # 另一个终端：跑接入层（凭据与地址都走环境变量）
    export GATEWAY_BASE_URL=http://127.0.0.1:8790/v1
    export GATEWAY_API_KEY=mock-token
    export GATEWAY_ALLOWED_MODELS=deepseek-chat,gpt-4o-mini
    python scripts/gateway_demo.py --model deepseek-chat

    # 想看失败路径：
    python scripts/gateway_demo.py --model not-approved-model   # 白名单拒绝
    GATEWAY_API_KEY=wrong python scripts/gateway_demo.py        # 401
"""
import argparse
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

STATE = {
    "rpm": 5,
    "allowed": {"deepseek-chat", "gpt-4o-mini"},
    "token": "mock-token",
    "calls": [],          # 时间戳列表，用于限流
    "upstream_calls": 0,
    "lock": threading.Lock(),
}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):        # 静音默认日志
        pass

    def _send(self, code: int, obj: dict, headers: dict | None = None):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/health":
            self._send(200, {"ok": True, "upstream_calls": STATE["upstream_calls"]})
            return
        if path.endswith("/models"):
            self._send(200, {"data": [{"id": m} for m in sorted(STATE["allowed"])]})
            return
        self._send(404, {"error": "not found"})

    def do_POST(self):
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"

        # 1) 鉴权
        auth = self.headers.get("Authorization") or ""
        if auth != f"Bearer {STATE['token']}":
            self._send(401, {"error": {"message": "invalid company token",
                                       "type": "authentication_error"}})
            return

        # 2) 限流
        now = time.time()
        with STATE["lock"]:
            STATE["calls"] = [t for t in STATE["calls"] if now - t < 60]
            if len(STATE["calls"]) >= STATE["rpm"]:
                retry_after = max(1, int(60 - (now - STATE["calls"][0])))
                self._send(429, {"error": {"message": "rate limit exceeded",
                                           "type": "rate_limit_error"}},
                           headers={"Retry-After": str(retry_after)})
                return
            STATE["calls"].append(now)

        try:
            body = json.loads(raw.decode("utf-8"))
        except Exception:
            self._send(400, {"error": {"message": "bad json"}})
            return

        # 3) 模型白名单
        model = body.get("model", "")
        if model not in STATE["allowed"]:
            self._send(403, {"error": {"message":
                                       f"model {model!r} not approved on this gateway",
                                       "type": "permission_error"}})
            return

        # 4) 上游「翻译」：按 prompt 回声，够演示链路与审计
        with STATE["lock"]:
            STATE["upstream_calls"] += 1
        msgs = body.get("messages") or []
        last = ""
        for m in reversed(msgs):
            if m.get("role") == "user":
                last = str(m.get("content", ""))
                break
        self._send(200, {
            "id": f"mock-{int(now*1000)}",
            "model": model,
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant",
                                     "content": f"[mock:{model}] {last[:120]}"}}],
            "usage": {"prompt_tokens": max(1, len(last) // 4),
                      "completion_tokens": max(1, len(last) // 4),
                      "total_tokens": max(2, len(last) // 2)},
        })


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8790)
    ap.add_argument("--rpm", type=int, default=5)
    ap.add_argument("--allowed-models", default="deepseek-chat,gpt-4o-mini")
    ap.add_argument("--token", default="mock-token")
    args = ap.parse_args()

    STATE["rpm"] = args.rpm
    STATE["allowed"] = {m.strip() for m in args.allowed_models.split(",") if m.strip()}
    STATE["token"] = args.token

    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"mock gateway on http://127.0.0.1:{args.port}/v1  "
          f"rpm={args.rpm} allowed={sorted(STATE['allowed'])}")
    print(f"token={args.token}（本地假凭据，仅用于演示）")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
