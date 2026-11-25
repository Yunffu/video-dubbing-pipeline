#!/usr/bin/env python3
"""翻译层命令行：验证 provider 接入与降级路径。

用法:
    python scripts/translate_cli.py --text "Hello, welcome to the finals." --src en --tgt zh
    python scripts/translate_cli.py --segments segs.json --src en --tgt zh --json out.json
    python scripts/translate_cli.py --describe          # 只看当前会用到哪些后端
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline.config import TranslateConfig          # noqa: E402
from pipeline.contracts import SubtitleSegment        # noqa: E402
from pipeline.translate import TranslatingService      # noqa: E402


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", action="append", help="一行一段，可重复")
    ap.add_argument("--segments", help="JSON 文件：[{index,start,end,text}]")
    ap.add_argument("--src", default="en")
    ap.add_argument("--tgt", default="zh")
    ap.add_argument("--json", help="结果写出的 JSON 路径")
    ap.add_argument("--describe", action="store_true", help="只打印后端配置")
    args = ap.parse_args()

    cfg = TranslateConfig()
    service = TranslatingService(cfg)

    if args.describe:
        print(json.dumps(service.describe(), ensure_ascii=False, indent=2))
        return

    if args.segments:
        raw = json.loads(Path(args.segments).read_text(encoding="utf-8"))
        segs = [SubtitleSegment(index=int(d.get("index", i)), start=float(d.get("start", 0)),
                                 end=float(d.get("end", 0)), text=d["text"],
                                 speaker=d.get("speaker", "")) for i, d in enumerate(raw)]
    elif args.text:
        segs = [SubtitleSegment(index=i, start=float(i * 2), end=float(i * 2 + 2), text=t)
                for i, t in enumerate(args.text)]
    else:
        ap.error("需要 --text 或 --segments")

    result = service.translate(segs, args.src, args.tgt)

    print(f"后端: {result.stat.provider} {result.stat.model}")
    print(f"耗时: {result.stat.elapsed_ms}ms   降级: {result.stat.degraded}")
    if result.warnings:
        for w in result.warnings:
            print(f"  ⚠ {w}")
    for s in result.segments:
        print(f"  [{s.index}] {s.text}")
    if args.json:
        Path(args.json).write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2),
                                   encoding="utf-8")


if __name__ == "__main__":
    main()
