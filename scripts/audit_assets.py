#!/usr/bin/env python3
"""素材审计：对全部母版跑「规格自检 + 人脸校验」，汇总成一张审计表。

用法:
    python scripts/audit_assets.py [--md docs/notes/02-素材审计表.md]
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "assets" / "raw"


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--md", default=str(ROOT / "docs" / "notes" / "02-素材审计表.md"))
    args = ap.parse_args()

    rows = []
    for meta_path in sorted(RAW.glob("*.meta.json")):
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        mid = meta["id"]
        mp4 = RAW / f"{mid}.mp4"
        if not mp4.exists():
            continue

        p = run([sys.executable, str(ROOT / "scripts" / "probe_media.py"), str(mp4)])
        spec = {}
        for line in p.stdout.splitlines():
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) == 4 and cells[0] != "文件":
                spec.setdefault(cells[1], (cells[2], cells[3]))

        f = run([sys.executable, str(ROOT / "scripts" / "check_face.py"), str(mp4), "--frames", "16"])
        face = {}
        for line in f.stdout.splitlines():
            m = re.search(r"单人正脸帧占比\([^)]*\):\s*([\d.]+)%.*最大脸高:\s*(\d+)px", line)
            if m:
                face["frontal_pct"] = f"{float(m.group(1)):.0f}%"
                face["face_h"] = m.group(2) + "px"
            if "判定:" in line:
                face["verdict"] = line.split("判定:")[-1].strip()

        rows.append({"meta": meta, "spec": spec, "face": face})

    md = ["| ID | 定位 | 许可 | 分辨率 | 帧率 | 时长 | 单人正脸 | 最大脸高 | 判定 |",
          "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        m = r["meta"]
        g = lambda k: r["spec"].get(k, ("-", "-"))[0]
        md.append(f"| {m['id']} | {m.get('case_role', '-')} | {m['license']['name']} | "
                  f"{g('分辨率')} | {g('帧率')} | {g('时长')} | "
                  f"{r['face'].get('frontal_pct', '-')} | "
                  f"{r['face'].get('face_h', '-')} | {r['face'].get('verdict', '-')} |")

    out = "\n".join(md) + (
        "\n\n> 判定规则：单人正脸帧占比 ≥80% 且最大脸高 ≥80px → PASS（可做 LipSync GT；阈值依据：LipSync 裁脸后统一 resize 到 96×96，源脸高 ≥80px 即够）。\n"
        "> 规格自检来自 `probe_media.py`；人脸校验来自 `check_face.py`（OpenCV YuNet，"
        "由鼻尖与眼中点相对位置估算偏航角）。\n")
    Path(args.md).write_text("# 素材审计表\n\n> 由 `scripts/audit_assets.py` 生成\n\n" + out, encoding="utf-8")
    print(out)
    print(f"→ {args.md}")


if __name__ == "__main__":
    main()
