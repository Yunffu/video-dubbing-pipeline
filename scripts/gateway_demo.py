#!/usr/bin/env python3
"""接入层演示：把外部 API 挂到「公司网关」上，并演示四种失败的处理。

不联外网 —— 默认连 `scripts/mock_gateway.py` 起的本地假网关。

用法:
    # 终端 1
    python scripts/mock_gateway.py --port 8790 --rpm 3
    # 终端 2
    python scripts/gateway_demo.py
    python scripts/gateway_demo.py --model not-approved     # 白名单拒绝
    python scripts/gateway_demo.py --wrong-token             # 401
    python scripts/gateway_demo.py --burst 6                 # 触发 429 + 退避
"""
import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.gateway import AuditLog, GatewayClient, GatewayConfig  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default=os.environ.get(
        "GATEWAY_BASE_URL", "http://127.0.0.1:8790/v1"))
    ap.add_argument("--api-key", default=os.environ.get("GATEWAY_API_KEY", "mock-token"))
    ap.add_argument("--model", default=os.environ.get("GATEWAY_MODEL", "deepseek-flash"))
    ap.add_argument("--allowed-models", default=os.environ.get(
        "GATEWAY_ALLOWED_MODELS", "deepseek-flash,gpt-4o-mini"))
    ap.add_argument("--burst", type=int, default=1)
    ap.add_argument("--wrong-token", action="store_true")
    ap.add_argument("--audit", default="results/gateway/audit.jsonl")
    args = ap.parse_args()

    cfg = GatewayConfig()
    cfg.base_url = args.base_url
    cfg.api_key = "wrong-token" if args.wrong_token else args.api_key
    cfg.allowed_models = [m.strip() for m in args.allowed_models.split(",") if m.strip()]
    cfg.audit_path = args.audit
    cfg.max_retries = 2
    cfg.breaker_threshold = 3
    cfg.breaker_cooldown_s = 5
    cfg.requests_per_minute = 0        # 限流交给网关演示

    client = GatewayClient(cfg, AuditLog(cfg.audit_path, actor="demo"))
    print("网关配置（脱敏）：")
    for k, v in cfg.describe().items():
        print(f"  {k}: {v}")

    msgs = [{"role": "system", "content": "你是字幕翻译助手"},
            {"role": "user", "content": "把这句话译成英文：欢迎来到我们的演播室。"}]

    ok = 0
    for i in range(args.burst):
        t0 = time.time()
        try:
            data = client.chat(msgs, model=args.model, stage="gateway_demo",
                               case_id=f"demo_{i}")
            text = data["choices"][0]["message"]["content"]
            usage = data.get("usage") or {}
            print(f"[{i+1}/{args.burst}] OK  {(time.time()-t0)*1000:.0f}ms  "
                  f"tokens={usage.get('total_tokens')}  → {text[:80]}")
            ok += 1
        except Exception as e:
            kind = getattr(e, "kind", type(e).__name__)
            print(f"[{i+1}/{args.burst}] FAIL({kind}) {(time.time()-t0)*1000:.0f}ms  {e}")

    print(f"\n成功 {ok}/{args.burst}；审计日志 → {cfg.audit_path}")
    if Path(cfg.audit_path).exists():
        for line in Path(cfg.audit_path).read_text(encoding="utf-8").splitlines()[-6:]:
            print("  " + line)


if __name__ == "__main__":
    main()
