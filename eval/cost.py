#!/usr/bin/env python3
"""成本账单：把各次运行的 token / 墙钟 / 降级次数聚合成一张可核对的表。

对应用户的第 2 点要求：「效果由我评判 → 评判后我们计算刚刚使用的成本 →
在效果可控的时候降低 API 账单」。所以这里要回答三个问题：
  1. 这一轮花了多少钱？（token → 美元，单价是可配置口径，不是权威数字）
  2. **失败的钱算不算？**（重试/降级掉的调用照样计费 → 必须单列）
  3. 降本手段能省多少？（缓存命中、合并请求、模型分级路由 → 给区间估计）

用法:
    python eval/cost.py --scan _work --out results/cost.json
    python eval/cost.py --scan _work --simulate cache 0.3
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.pricing import REFERENCE_PRICES, estimate_usd, price_of  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _stage_tokens(tr: dict) -> dict:
    """从 translate stage 里挖出 token 口径。

    两种来源：
      ① stat.extra —— 新版 provider 写 prompt/completion/cache_hit 三个数（可精确折算）
      ② stage["cost"] —— 旧版只留了 total_tokens（分不清输入输出）
         → 只能给**区间**：全算输入价是下界，全算输出价是上界
    """
    extra = tr.get("extra") or {}
    prompt = extra.get("prompt_tokens")
    completion = extra.get("completion_tokens")
    cache_hit = extra.get("prompt_cache_hit_tokens") or 0
    if prompt is not None and completion is not None:
        return {"prompt": int(prompt), "completion": int(completion),
                "cache_hit": int(cache_hit), "total": int(prompt) + int(completion),
                "exact": True}
    total = extra.get("total_tokens") or tr.get("cost") or 0
    try:
        total = int(total)
    except (TypeError, ValueError):
        total = 0
    return {"prompt": total, "completion": 0, "cache_hit": 0, "total": total,
            "exact": False}


def _usd_of(tokens: dict, model: str, recorded) -> tuple[float, str]:
    """折算美元，返回 (值, 口径说明)。recorded 优先——那是 provider 当时算的。"""
    if recorded:
        return float(recorded), "provider 实录"
    if not tokens.get("total"):
        return 0.0, "无 token 记录（未调用在线后端）"
    if tokens.get("exact"):
        est = estimate_usd(model, tokens["prompt"], tokens["completion"],
                           tokens["cache_hit"])
        if est.get("usd") is not None:
            return est["usd"], (f"按 {est['canonical']} 单价折算"
                                f"（{'高峰' if est['peak'] else '非高峰'}）")
        return 0.0, "未知模型单价"
    # 只有 total_tokens → 给区间，诚实标注
    p = price_of(model)
    if not p:
        return 0.0, "未知模型单价"
    total = tokens["total"]
    lo = total / 1e6 * p["cache_miss_in"]
    hi = total / 1e6 * p["out"]
    return round(lo, 8), f"仅 total_tokens，区间 ${lo:.8f}~${hi:.8f}（计账取下界）"


def collect(scan_dir: Path) -> list[dict]:
    """递归找 run.json（流水线每次运行都落一份）。

    兼容两种落盘结构：
      ① 正式 run.json：{"meta": {"cost": {...}, "segments": n, "source_info": {...}}}
      ② 场景 demo run.json：{"stages": [...], "segments": [{index,start,end,src,tgt}]}
    """
    rows = []
    for p in sorted(scan_dir.rglob("run.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        meta = d.get("meta", {}) or {}
        cost = meta.get("cost", {}) or {}
        stages = d.get("stages", []) or []
        tr = next((s for s in stages if str(s.get("name", "")).startswith("translate")), {}) or {}
        extra = tr.get("extra", {}) or {}
        attempts = extra.get("attempts") or []
        # 降级与重试：调用照样发生了 → 钱照样花了
        retried = max(len(attempts) - 1, 0) if attempts else 0

        tokens = _stage_tokens(tr)
        model = tr.get("model") or meta.get("model") or "deepseek-flash"
        recorded = extra.get("estimated_usd")
        if recorded is None:
            recorded = cost.get("translate_cost_usd")
        usd, basis = _usd_of(tokens, model, recorded)

        # 段数与时长：优先正式结构，否则从场景的 segments 列表推
        seg_list = d.get("segments") if isinstance(d.get("segments"), list) else []
        segments = meta.get("segments") or tr.get("segments") or len(seg_list) or 0
        duration = (meta.get("source_info", {}) or {}).get("duration") or 0
        if not duration and seg_list:
            ends = [s.get("end", 0) for s in seg_list if isinstance(s, dict)]
            duration = max(ends) if ends else 0
        # TTS 字数：正式结构里记了 tts_chars；场景结构里用译文长度估
        tts_chars = cost.get("tts_chars", 0) or 0
        if not tts_chars and seg_list:
            tts_chars = sum(len(str(s.get("tgt") or s.get("text") or ""))
                             for s in seg_list if isinstance(s, dict))

        rows.append({
            "run": str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(p),
            "segments": segments,
            "duration_s": round(float(duration or 0), 2),
            "prompt_tokens": tokens["prompt"] if tokens["exact"] else 0,
            "completion_tokens": tokens["completion"] if tokens["exact"] else 0,
            "total_tokens": tokens["total"],
            "tokens_exact": tokens["exact"],
            "model": model,
            "translate_cost_usd": usd,
            "cost_basis": basis,
            "cost_per_min_audio_usd": (round(usd / (duration / 60), 8)
                                       if usd and duration else 0),
            "cost_per_1k_segments_usd": (round(usd / segments * 1000, 8)
                                         if usd and segments else 0),
            "wall_ms": cost.get("wall_ms", 0) or 0,
            "provider": tr.get("provider", ""),
            "degraded": bool(tr.get("degraded")),
            "attempts": len(attempts),
            "retried_calls": retried,
            "tts_chars": tts_chars,
            "mix": meta.get("mix", {}) or {},
        })
    return rows


def summarize(rows: list[dict]) -> dict:
    def s(k):
        return sum(r.get(k, 0) or 0 for r in rows)
    dur_min = sum((r.get("duration_s") or 0) for r in rows) / 60
    segs = s("segments")
    tts_chars = s("tts_chars")
    # 反事实：如果 ASR / TTS 也走 DeepSeek 官方托管 API（而不是本地 faster-whisper / edge-tts）
    # 官方价（2026-11-25 核实，见 pipeline/pricing.py）：ASR $0.116/小时，TTS $0.45/万字符
    cf_asr = round(dur_min / 60 * REFERENCE_PRICES["asr_usd_per_hour"], 6)
    cf_tts = round(tts_chars / 10000 * REFERENCE_PRICES["tts_usd_per_10k_chars"], 6)
    return {
        "runs": len(rows),
        "segments": segs,
        "audio_minutes": round(dur_min, 2),
        "tts_chars": tts_chars,
        "prompt_tokens": s("prompt_tokens"),
        "completion_tokens": s("completion_tokens"),
        "total_tokens": s("total_tokens"),
        "tokens_exact_rows": sum(1 for r in rows if r.get("tokens_exact")),
        "translate_cost_usd": round(s("translate_cost_usd"), 8),
        "cost_per_min_audio_usd": round(s("translate_cost_usd") / dur_min, 8) if dur_min else None,
        "cost_per_1k_segments_usd": round(s("translate_cost_usd") / segs * 1000, 8) if segs else None,
        "wall_ms": s("wall_ms"),
        "degraded_runs": sum(1 for r in rows if r.get("degraded")),
        "retried_calls": s("retried_calls"),
        "degraded_cost_usd": round(
            sum(r["translate_cost_usd"] for r in rows if r.get("degraded")), 8),
        "overshoot_segments": sum((r.get("mix", {}) or {}).get("overshoot", 0) or 0
                                  for r in rows),
        "counterfactual_hosted_usd": {
            "asr": cf_asr,
            "tts": cf_tts,
            "total": round(cf_asr + cf_tts, 6),
            "note": "反事实口径：ASR/TTS 若走 DeepSeek 官方托管 API 的 list price；"
                    "本地 faster-whisper / edge-tts 这两段实际为 $0",
        },
    }


def simulate(rows: list[dict], mode: str, value: float) -> dict:
    """降本测算（区间估计，不许诺）。

    cache   : 命中率 value 的请求直接复用上次结果 → 只省 token 里的可缓存部分
    batch   : 把 N 段合成 1 次请求 → 省掉每段的固定开销（prompt 里的指令部分）
    routing : 便宜模型先跑，value 比例的段不用升级到贵模型
    """
    tot = summarize(rows)
    base = tot["translate_cost_usd"]
    if base <= 0:
        return {"error": "没有可测算的成本数据（先跑一次带翻译的 run.json）"}
    if mode == "cache":
        # 缓存命中省掉整次请求（in/out 都省）→ 上限是命中率
        saved = base * value
        return {"mode": "cache", "hit_rate": value, "saved_usd": round(saved, 6),
                "after_usd": round(base - saved, 6),
                "note": "命中缓存的请求完全不发出去；实际还要扣缓存写入与失效"}
    if mode == "batch":
        # 合并请求省的是每段的指令性 prompt 开头，保守按 prompt 的 15% 估
        p = tot["prompt_tokens"]
        fixed = p * 0.15
        saved = fixed * (base / max(p + tot["completion_tokens"], 1))
        return {"mode": "batch", "saved_usd": round(saved, 6),
                "after_usd": round(base - saved, 6),
                "note": "合并请求省每段的固定指令开销；保守按 prompt 15% 估"}
    if mode == "routing":
        saved = base * value * 0.5
        return {"mode": "routing", "cheap_share": value, "saved_usd": round(saved, 6),
                "after_usd": round(base - saved, 6),
                "note": "便宜档与贵档单价差按 50% 估；实际取决于是否需要升级"}
    return {"error": f"未知模式 {mode}"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scan", default="_work", help="扫描目录（递归找 run.json）")
    ap.add_argument("--simulate", choices=["cache", "batch", "routing"])
    ap.add_argument("--value", type=float, default=0.3)
    ap.add_argument("--out")
    args = ap.parse_args()

    rows = collect(Path(args.scan) if Path(args.scan).is_absolute() else ROOT / args.scan)
    if not rows:
        print(f"在 {args.scan} 下没找到 run.json")
        return

    tot = summarize(rows)
    print("| run | 段数 | 时长(s) | token(总) | 翻译成本$ | $/分钟 | $/千段 | 墙钟(ms) | 后端 | 降级 | 重试 |")
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        print(f"| {r['run']} | {r['segments']} | {r['duration_s']} | "
              f"{r['total_tokens']}{'' if r['tokens_exact'] else '*'} | "
              f"{r['translate_cost_usd']:.8f} | {r['cost_per_min_audio_usd']} | "
              f"{r['cost_per_1k_segments_usd']} | "
              f"{r['wall_ms']} | {r['provider'] or '-'} | "
              f"{'是' if r['degraded'] else '否'} | {r['retried_calls']} |")

    print(f"\n合计: {tot['runs']} 次运行 · {tot['segments']} 段 · "
          f"{tot['audio_minutes']} 分钟音频")
    print(f"翻译成本 ${tot['translate_cost_usd']:.8f}"
          f"（${tot['cost_per_min_audio_usd']}/分钟音频 · "
          f"${tot['cost_per_1k_segments_usd']}/千段）")
    print(f"token 合计 {tot['total_tokens']}（其中 {tot['tokens_exact_rows']} 行有输入/输出拆分；"
          f"带 * 的行只有总数 → 成本按下界计）")
    print(f"降级运行 {tot['degraded_runs']} 次 · 重试调用 {tot['retried_calls']} 次 · "
          f"降级部分成本 ${tot['degraded_cost_usd']:.6f} ← 这部分钱也要算")
    print(f"overshoot 段数 {tot['overshoot_segments']}（译文长度塞不进原时间槽）")
    cf = tot["counterfactual_hosted_usd"]
    print(f"\n反事实（若 ASR/TTS 走 DeepSeek 官方托管 API，list price）："
          f"ASR ${cf['asr']} + TTS ${cf['tts']} = ${cf['total']}"
          f"　← 本地 faster-whisper / edge-tts 这两段实际 $0，"
          f"是这条链路成本的大头")

    payload = {"rows": rows, "summary": tot}
    if args.simulate:
        sim = simulate(rows, args.simulate, args.value)
        payload["simulation"] = sim
        print("\n降本测算: " + json.dumps(sim, ensure_ascii=False))

    if args.out:
        p = Path(args.out) if Path(args.out).is_absolute() else ROOT / args.out
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"-> {p}")


if __name__ == "__main__":
    main()
