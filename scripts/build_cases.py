#!/usr/bin/env python3
"""生成 case 集并写出 assets/cases.json。

对每个母版跑 make_derived.py，收集派生难例 → 汇总成一份 case 清单，
每条 case 记录：母版(GT)、难例类型、造法、用于验证什么指标。

用法:
    python scripts/build_cases.py
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "assets" / "raw"
DERIVED = ROOT / "assets" / "derived"


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    DERIVED.mkdir(parents=True, exist_ok=True)
    cases = []
    for meta_path in sorted(RAW.glob("*.meta.json")):
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        mid = meta["id"]
        master = ROOT / meta["master"]
        r = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "make_derived.py"),
             str(master), "--outdir", str(DERIVED), "--name", mid],
            capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode != 0:
            print(f"[失败] {mid}\n{r.stderr}")
            continue
        dman = json.loads((DERIVED / f"{mid}_derived.json").read_text(encoding="utf-8"))
        for d in dman["derived"]:
            cases.append({
                "case_id": f"{mid}__{d['type']}",
                "master": meta["master"],
                "gt_master_sha256": meta["master_sha256"],
                "license": meta["license"]["name"],
                "role": meta["role"],
                "derived_file": f"assets/derived/{d['file']}",
                "difficulty_type": d["type"],
                "purpose": d["purpose"],
                "metric": d["metric"],
                "verify_note": meta.get("verify", ""),
            })
        print(f"[完成] {mid} → {len(dman['derived'])} 个 case")

    out = ROOT / "assets" / "cases.json"
    out.write_text(json.dumps({"cases": cases}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"case 集: {len(cases)} 条 → {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
