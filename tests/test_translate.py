"""翻译层自测：不联网、不需要密钥。

覆盖三条关键路径：
1. LLM 返回带编号的多行输出 → 正确解析回 segments（保序保数）；
2. LLM 少返回几行 → 缺失段回退原文并产生 warning（不静默丢段）；
3. LLM 整个失败 → registry 自动降级到离线后端，并在 stat/warnings 里留痕。
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from pipeline.config import TranslateConfig          # noqa: E402
from pipeline.contracts import SubtitleSegment       # noqa: E402
from pipeline.translate.openai_compatible import (   # noqa: E402
    OpenAICompatibleProvider, ProviderError)
from pipeline.translate.registry import TranslatingService  # noqa: E402

FAILURES = []


def check(name: str, cond: bool, detail: str = ""):
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        FAILURES.append(name)


def make_segments():
    return [
        SubtitleSegment(index=0, start=0.0, end=2.0, text="Hello, welcome to the finals."),
        SubtitleSegment(index=1, start=2.0, end=4.0, text="The game starts now."),
    ]


def fake_provider(cfg, name, content):
    p = OpenAICompatibleProvider(cfg, name, "http://localhost:9/v1", "dummy", "dummy-model")
    p._post = lambda payload: {
        "choices": [{"message": {"content": content}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
    }
    return p


def test_parsing():
    print("[1] LLM 编号输出解析")
    cfg = TranslateConfig(provider="offline")
    content = "0\t你好，欢迎来到总决赛。\n1\t比赛现在开始。"
    p = fake_provider(cfg, "deepseek", content)
    res = p.translate(make_segments(), "en", "zh")
    check("段数一致", len(res.segments) == 2, str(len(res.segments)))
    check("首段译文正确", res.segments[0].text == "你好，欢迎来到总决赛。", res.segments[0].text)
    check("次段译文正确", res.segments[1].text == "比赛现在开始。", res.segments[1].text)
    check("顺序保持", [s.index for s in res.segments] == [0, 1])
    check("token 用量记账", res.stat.extra.get("total_tokens") == 30, str(res.stat.extra))
    check("未降级标记", res.stat.degraded is False)


def test_missing_line():
    print("[2] LLM 漏行 → 回退原文并告警")
    cfg = TranslateConfig(provider="offline")
    p = fake_provider(cfg, "deepseek", "0\t只有第一行有译文")
    res = p.translate(make_segments(), "en", "zh")
    check("段数仍一致", len(res.segments) == 2, str(len(res.segments)))
    check("缺失段回退原文", res.segments[1].text == "The game starts now.", res.segments[1].text)
    check("产生 warning", any("未解析到译文" in w for w in res.warnings), str(res.warnings))


def test_fallback_chain():
    print("[3] LLM 失败 → 自动降级离线并留痕")
    cfg = TranslateConfig(provider="auto")
    svc = TranslatingService(cfg)

    class Boom(OpenAICompatibleProvider):
        def available(self):
            return True

        def translate(self, segments, source_language, target_language):
            raise ProviderError("模拟 401 鉴权失败")

    svc.providers = [Boom(cfg, "deepseek", "http://x/v1", "k", "m"),
                     svc.providers[-1]]
    res = svc.translate(make_segments(), "en", "zh")
    check("降级到离线", res.stat.provider == "offline", res.stat.provider)
    check("标记 degraded", res.stat.degraded is True)
    check("记录失败原因", "模拟 401" in json.dumps(res.stat.extra, ensure_ascii=False),
          str(res.stat.extra))
    check("warnings 含降级提示", any("降级" in w for w in res.warnings), str(res.warnings))


def test_glossary_and_strict():
    print("[4] 离线术语表 / 严格模式")
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                     encoding="utf-8") as f:
        json.dump({"finals": "总决赛"}, f, ensure_ascii=False)
        path = f.name

    cfg = TranslateConfig(provider="offline", glossary_path=path)
    svc = TranslatingService(cfg)
    res = svc.translate(make_segments(), "en", "zh")
    check("术语替换生效", res.segments[0].text == "Hello, welcome to the 总决赛.",
          res.segments[0].text)

    cfg2 = TranslateConfig(provider="offline", glossary_path=path, offline_strict=True)
    svc2 = TranslatingService(cfg2)
    res2 = svc2.translate(make_segments(), "en", "zh")
    check("严格模式：未命中段置空", res2.segments[1].text == "", repr(res2.segments[1].text))
    check("严格模式：命中段保留", res2.segments[0].text == "Hello, welcome to the 总决赛.",
          res2.segments[0].text)


if __name__ == "__main__":
    for fn in (test_parsing, test_missing_line, test_fallback_chain,
               test_glossary_and_strict):
        fn()
    print()
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} 项 -> {FAILURES}")
        sys.exit(1)
    print("ALL PASS")
