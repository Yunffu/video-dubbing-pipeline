"""模型单价表与成本估算（成本口径的**单一来源**）。

为什么要有这个文件：
  用户要求「评判完效果就算出刚刚用了多少钱，效果可控时把 API 账单压下去」。
  这句话拆成工程动作是三步：
    ① 每次调用记 token            → pipeline/translate/openai_compatible.py
    ② token 折算成美元            → **本文件**
    ③ 按效果反推阈值、把超预算的挡掉 → spec/*.yaml + eval/cost.py
  第 ② 步如果散落在各个脚本里，「这一轮花了多少钱」就会有好几个答案。
  所以单价表只写这一处，改价只改这一处。

口径（必须与 spec/translate.yaml 的 threshold_source 一致）：
  - 单位：美元 / 百万 tokens
  - 输入分两档：缓存命中 / 未命中（DeepSeek 自动前缀缓存，命中价约为未命中的 1/50）
  - 高峰时段单价 ×2：UTC 周一至周五 01:00–04:00 与 06:00–10:00（不含中国法定节假日）
  - 本地时区换算（UTC+8）：高峰 = 工作日 09:00–12:00 与 14:00–18:00

数据来源（2026-11-25 抓取官方定价页核实，非记忆）：
  https://api-docs.deepseek.com/quick_start/pricing
  官方当前只列两个模型：deepseek-flash(V4.1-Flash-1017) / deepseek-v4-pro(V4-Pro-0813)。
  旧名 deepseek-chat / deepseek-v4-flash 官方说明「由 V4.1-Flash 承载并按 Flash 价计费」，
  所以本表把旧名**按 flash 价**估算（实测旧名仍可调用，2026-11-25 本机跑通）。
"""
from __future__ import annotations

import datetime as _dt
import os

# 高峰时段（UTC）：周一至周五 01:00-04:00 与 06:00-10:00
_PEAK_UTC_HOURS = ((1, 4), (6, 10))

# 官方价目（美元 / 百万 tokens）—— off-peak 价，peak 为其 2 倍
MODEL_PRICING: dict[str, dict[str, float]] = {
    "deepseek-flash": {"cache_hit_in": 0.003, "cache_miss_in": 0.15, "out": 0.60},
    "deepseek-v4-pro": {"cache_hit_in": 0.022, "cache_miss_in": 0.66, "out": 1.98},
}

# 旧名 → 现行模型（按官方说明折算）
MODEL_ALIASES: dict[str, str] = {
    "deepseek-chat": "deepseek-flash",
    "deepseek-v4-flash": "deepseek-flash",
    "deepseek-v4-flash-vision-exp": "deepseek-flash",
    "deepseek-reasoner": "deepseek-v4-pro",
}

# 对照用：本项目实际用的本地免费栈 vs DeepSeek 官方托管价（list price）
REFERENCE_PRICES = {
    "tts_usd_per_10k_chars": 0.45,      # DeepSeek 官方 TTS list price
    "tts_usd_per_10k_chars_promo": 0.225,
    "asr_usd_per_hour": 0.116,          # DeepSeek 官方 STT list price
    "asr_usd_per_hour_promo": 0.058,
}


def canonical_model(model: str) -> str:
    """把旧模型名折算到现行模型名；未知模型原样返回。"""
    m = (model or "").strip()
    return MODEL_ALIASES.get(m, m)


def is_peak(at: _dt.datetime | None = None) -> bool:
    """是否落在高峰时段（UTC 周一至周五 01-04 / 06-10）。

    中国法定节假日我们无法自动判断 → 提供环境变量 `DEEPSEEK_PEAK_OVERRIDE`
    （设 0 强制按非高峰算，设 1 强制按高峰算），不设则按日历判断。
    这是**已知简化**，报告里如实写出来。
    """
    ov = os.environ.get("DEEPSEEK_PEAK_OVERRIDE", "").strip()
    if ov == "0":
        return False
    if ov == "1":
        return True
    now = at or _dt.datetime.now(_dt.timezone.utc)
    if now.weekday() >= 5:          # 周末全天非高峰
        return False
    hour = now.hour
    return any(lo <= hour < hi for lo, hi in _PEAK_UTC_HOURS)


def price_of(model: str, peak: bool | None = None) -> dict[str, float] | None:
    """取某个模型的单价（美元/百万 tokens）。未知模型返回 None。"""
    name = canonical_model(model)
    base = MODEL_PRICING.get(name)
    if base is None:
        return None
    if peak is None:
        peak = is_peak()
    mult = 2.0 if peak else 1.0
    return {k: v * mult for k, v in base.items()}


def estimate_usd(model: str, prompt_tokens: int = 0, completion_tokens: int = 0,
                 cache_hit_tokens: int = 0, at: _dt.datetime | None = None,
                 ) -> dict:
    """把 token 数折算成美元。

    缓存命中 token 从「未命中输入」里扣除，按命中价计费（DeepSeek 自动前缀缓存，
    usage 里会分 prompt_cache_hit_tokens / prompt_cache_miss_tokens）。
    """
    prompt_tokens = int(prompt_tokens or 0)
    completion_tokens = int(completion_tokens or 0)
    cache_hit_tokens = int(cache_hit_tokens or 0)
    if cache_hit_tokens > prompt_tokens:      # 防御：有些后端 usage 口径不一致
        cache_hit_tokens = prompt_tokens
    miss_tokens = prompt_tokens - cache_hit_tokens

    peak = is_peak(at)
    price = price_of(model, peak=peak)
    if price is None:
        return {"model": model, "canonical": canonical_model(model),
                "usd": None, "peak": peak,
                "note": "未知模型单价，未折算；请在 MODEL_PRICING 里补一行"}

    usd = (miss_tokens / 1e6 * price["cache_miss_in"]
           + cache_hit_tokens / 1e6 * price["cache_hit_in"]
           + completion_tokens / 1e6 * price["out"])
    return {
        "model": model,
        "canonical": canonical_model(model),
        "usd": round(usd, 8),
        "peak": peak,
        "price_per_1m": price,
        "tokens": {"prompt": prompt_tokens, "cache_hit": cache_hit_tokens,
                   "cache_miss": miss_tokens, "completion": completion_tokens},
        "basis": "api-docs.deepseek.com/quick_start/pricing（2026-11-25 核实）",
    }


def describe() -> str:
    """人读的价目表（供报告与 CLI 输出）。"""
    lines = ["模型单价（美元/百万 tokens，off-peak；peak 为 2 倍）", ""]
    for name, p in MODEL_PRICING.items():
        lines.append(f"  {name}: 缓存命中输入 {p['cache_hit_in']} · "
                     f"未命中输入 {p['cache_miss_in']} · 输出 {p['out']}")
    lines.append("")
    lines.append("高峰时段：UTC 周一至周五 01:00-04:00 与 06:00-10:00"
                 "（本地 UTC+8 即工作日 09:00-12:00 与 14:00-18:00）")
    return "\n".join(lines)


if __name__ == "__main__":
    import json
    print(describe())
    print()
    demo = estimate_usd("deepseek-chat", prompt_tokens=250, completion_tokens=180)
    print(json.dumps(demo, ensure_ascii=False, indent=2))
