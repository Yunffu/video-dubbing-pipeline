#!/usr/bin/env python3
"""成本口径的离线断言（不联网）。

为什么要有这个测试：
  成本是「用户判定 → 算钱 → 压账单」这条链的终点。如果单价表被改坏、
  或者高峰时段判断写反，会直接算错钱，而且**不会报错**——只是账单慢慢不对。
  所以这里把几条关键口径钉死。

跑法：python tests/test_pricing.py
"""
import datetime as dt
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.pricing import (  # noqa: E402
    MODEL_PRICING, canonical_model, estimate_usd, is_peak, price_of)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

FAILED = []


def check(cond, name, detail=""):
    if cond:
        print(f"  PASS {name}")
    else:
        print(f"  FAIL {name} {detail}")
        FAILED.append(name)


def utc(y, m, d, hh):
    return dt.datetime(y, m, d, hh, tzinfo=dt.timezone.utc)


print("[1] 旧模型名折算到现行模型")
check(canonical_model("deepseek-chat") == "deepseek-flash", "deepseek-chat → flash")
check(canonical_model("deepseek-v4-flash") == "deepseek-flash", "deepseek-v4-flash → flash")
check(canonical_model("deepseek-v4-pro") == "deepseek-v4-pro", "v4-pro 保持不变")

print("[2] 高峰时段判定（UTC 周一至周五 01-04 / 06-10）")
check(is_peak(utc(2026, 11, 23, 2)) is True, "周一 02:00 UTC = 高峰")
check(is_peak(utc(2026, 11, 23, 7)) is True, "周一 07:00 UTC = 高峰")
check(is_peak(utc(2026, 11, 23, 5)) is False, "周一 05:00 UTC = 非高峰")
check(is_peak(utc(2026, 11, 23, 20)) is False, "周一 20:00 UTC = 非高峰")
check(is_peak(utc(2026, 11, 21, 2)) is False, "周六 02:00 UTC = 非高峰（周末全天非高峰）")

print("[3] 高峰单价 = 非高峰单价 ×2")
lo = price_of("deepseek-flash", peak=False)
hi = price_of("deepseek-flash", peak=True)
check(abs(hi["out"] - lo["out"] * 2) < 1e-9, "输出价高峰翻倍")
check(abs(hi["cache_miss_in"] - lo["cache_miss_in"] * 2) < 1e-9, "输入价高峰翻倍")

print("[4] token → 美元 折算正确")
# 手工算：1000 未命中输入 + 1000 输出 @ flash 非高峰
#   = 1000/1e6*0.15 + 1000/1e6*0.60 = 1.5e-4 + 6.0e-4 = 7.5e-4
r = estimate_usd("deepseek-flash", prompt_tokens=1000, completion_tokens=1000,
                 at=utc(2026, 11, 23, 20))
check(abs(r["usd"] - 7.5e-4) < 1e-9, "1000 in + 1000 out = $0.00075", r)
check(r["peak"] is False, "非高峰标记正确")

r_peak = estimate_usd("deepseek-flash", prompt_tokens=1000, completion_tokens=1000,
                      at=utc(2026, 11, 23, 2))
check(abs(r_peak["usd"] - 1.5e-3) < 1e-9, "同一 token 数在高峰 = 2 倍", r_peak)

print("[5] 缓存命中比未命中便宜")
a = estimate_usd("deepseek-flash", prompt_tokens=1000, completion_tokens=0, at=utc(2026, 11, 23, 20))
b = estimate_usd("deepseek-flash", prompt_tokens=1000, completion_tokens=0,
                 cache_hit_tokens=1000, at=utc(2026, 11, 23, 20))
check(b["usd"] < a["usd"], "全缓存命中 < 全未命中", (a["usd"], b["usd"]))
check(abs(b["usd"] - 1000 / 1e6 * MODEL_PRICING["deepseek-flash"]["cache_hit_in"]) < 1e-12,
      "缓存命中价 = 表里的 cache_hit_in")

print("[6] 防御：异常输入不炸")
check(estimate_usd("unknown-model", 100, 100)["usd"] is None, "未知模型返回 None 而不是报错")
check(estimate_usd("deepseek-flash", 0, 0)["usd"] == 0.0, "0 token = $0")
r = estimate_usd("deepseek-flash", prompt_tokens=10, completion_tokens=0,
                 cache_hit_tokens=999, at=utc(2026, 11, 23, 20))
check(r["tokens"]["cache_hit"] == 10, "cache_hit 不会超过 prompt 总数", r["tokens"])

print()
if FAILED:
    print(f"{len(FAILED)} 条断言失败: {FAILED}")
    sys.exit(1)
print("ALL PASS")
