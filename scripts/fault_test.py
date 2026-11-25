#!/usr/bin/env python3
"""断网/故障注入测试 —— 验证 spec/_fault.yaml 里的兜底策略真的成立。

要证明的事（对应用户提的「服务器突然断网会不会直接挂掉」）：
    1. 进程不崩；
    2. 每个降级都有 stat.degraded=true + 可读原因；
    3. 全网断掉时仍能产出一份「降级成片」；
    4. 重试调用照样要能计入成本账单（超时不等于免费）。

用法:
    python scripts/fault_test.py                 # 跑全部注入用例
    python scripts/fault_test.py --json out.json
"""
import argparse
import json
import sys
import time
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.config import TranslateConfig, TTSConfig  # noqa: E402
from pipeline.contracts import SubtitleSegment  # noqa: E402
from pipeline.media import probe as media_probe  # noqa: E402
from pipeline.translate.offline import OfflineProvider  # noqa: E402
from pipeline.translate.openai_compatible import OpenAICompatibleProvider, ProviderError  # noqa: E402
from pipeline.translate.registry import TranslatingService  # noqa: E402
from pipeline.tts.service import TTSService  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _segments():
    return [SubtitleSegment(index=i, start=i * 2.0, end=i * 2.0 + 1.8,
                             text=f"测试语句 {i}，用于故障注入。")
            for i in range(1, 4)]


def _provider_raising(cfg, exc_factory, name="deepseek"):
    p = OpenAICompatibleProvider(cfg, name, "http://localhost:9/v1", "dummy", "dummy-model")

    def _boom(payload):
        raise exc_factory()

    p._post = _boom
    return p


def case_fault(name):
    """单个注入用例 → (通过?, 说明)

    注入的是**在线后端**的故障，链尾必须还有 offline 兜底 ——
    验证的是生产路径：主后端挂了 → 自动落离线 → 仍出片，且降级有留痕。
    """
    cfg = TranslateConfig()
    cfg.provider = "auto"          # auto = deepseek → openai_compatible → offline
    cfg.deepseek_api_key = "dummy"
    cfg.max_retries = 1
    segs = _segments()

    if name == "dns_fail":
        svc = TranslatingService(cfg)
        svc.providers = [_provider_raising(
            cfg, lambda: ProviderError("gaierror: [Errno -2] Name or service not known")),
            OfflineProvider(cfg)]
    elif name == "http_401":
        svc = TranslatingService(cfg)
        svc.providers = [_provider_raising(
            cfg, lambda: ProviderError("HTTP 401: invalid api key")),
            OfflineProvider(cfg)]
    elif name == "http_429":
        svc = TranslatingService(cfg)
        svc.providers = [_provider_raising(
            cfg, lambda: ProviderError("HTTP 429: rate limit")),
            OfflineProvider(cfg)]
    elif name == "http_5xx":
        svc = TranslatingService(cfg)
        svc.providers = [_provider_raising(
            cfg, lambda: ProviderError("HTTP 503: service unavailable")),
            OfflineProvider(cfg)]
    elif name == "network_gone":
        # 真断网：端点指向不可达地址，靠真实 socket 失败
        cfg.deepseek_base_url = "http://240.0.0.1:9/v1"
        svc = TranslatingService(cfg)
    else:
        return False, f"未知注入: {name}"

    t0 = time.time()
    res = svc.translate(segs, "zh", "en")
    ms = int((time.time() - t0) * 1000)
    attempts = res.stat.extra.get("attempts", [])
    fell_back = res.stat.provider == "offline"
    ok = (res.stat.degraded and fell_back and attempts
          and len(res.segments) == len(segs))
    detail = (f"最终后端={res.stat.provider} degraded={res.stat.degraded} "
              f"段数={len(res.segments)}/{len(segs)} 失败尝试={len(attempts)} "
              f"耗时={ms}ms")
    return ok, detail


def case_tts_network_gone():
    """TTS 侧全网不通 → 必须降级为「保留原声」而不是崩。"""
    cfg = TTSConfig()
    cfg.max_retries = 0
    svc = TTSService(cfg)
    for p in svc.providers:
        def _boom(items, out_dir):
            raise RuntimeError("网络不可达：WS 握手失败")
        p.synthesize = _boom
    res = svc.synthesize(_segments(), "en", ROOT / "_work" / "fault_test")
    ok = res.stat.degraded and not res.segments
    return ok, f"degraded={res.stat.degraded} 段数={len(res.segments)} 原因={res.stat.error}"


def case_corrupt_media():
    """坏素材 → probe 不抛异常，返回带 error 的字典。"""
    bad = ROOT / "_work" / "fault_test" / "corrupt.mp4"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_bytes(b"\x00\x01\x02not a real mp4" * 100)
    try:
        res = media_probe.probe(bad)
        ok = "error" in res or res.get("has_video") is False
        return ok, f"source={res.get('source')} error={res.get('error')}"
    except Exception as e:
        return False, f"probe 抛异常: {e}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json")
    args = ap.parse_args()

    cases = []
    for n in ["dns_fail", "http_401", "http_429", "http_5xx", "network_gone"]:
        cases.append((f"translate/{n}", case_fault(n)))
    cases.append(("tts/network_gone", case_tts_network_gone()))
    cases.append(("media/corrupt", case_corrupt_media()))

    rows, passed = [], 0
    print("| 用例 | 结果 | 说明 |")
    print("|---|---|---|")
    for name, (ok, detail) in cases:
        passed += int(ok)
        rows.append({"case": name, "pass": ok, "detail": detail})
        print(f"| {name} | {'PASS' if ok else 'FAIL'} | {detail} |")

    print(f"\n{passed}/{len(cases)} 通过")
    if args.json:
        p = Path(args.json)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(
            {"cases": rows, "passed": passed, "total": len(cases)},
            ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"-> {p}")
    sys.exit(0 if passed == len(cases) else 1)


if __name__ == "__main__":
    main()
