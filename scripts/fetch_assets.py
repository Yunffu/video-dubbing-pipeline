#!/usr/bin/env python3
"""按 assets/manifest.json 确定性重建母版（下载 → 校验 SHA256 → 规格统一）。

母版 = 该 case 的 GT。派生难例由 scripts/build_cases.py 从母版生成。

用法:
    python scripts/fetch_assets.py [--id M1_01 ...] [--cache ../../_work/cache] [--offline]
"""
import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "assets" / "manifest.json"
UA = "PersonalAgentProject/1.0 (educational; non-commercial)"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"  下载 {url}")
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=120) as r, dest.open("wb") as f:
        shutil.copyfileobj(r, f)


def normalize(src: Path, out: Path, start: float, dur: float, fps: int = 25) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    vf = f"scale=trunc(iw/2)*2:trunc(ih/2)*2,fps={fps},format=yuv420p"
    cmd = [shutil.which("ffmpeg"), "-y", "-v", "error",
           "-ss", f"{start}", "-t", f"{dur}", "-i", str(src),
           "-vf", vf,
           "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-profile:v", "high",
           "-c:a", "aac", "-ar", "48000", "-ac", "2", "-b:a", "128k",
           "-movflags", "+faststart", str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise SystemExit("ffmpeg 失败:\n" + r.stderr)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", action="append", help="只处理指定 id，可重复")
    ap.add_argument("--cache", default=str(ROOT.parent / "_work" / "cache"))
    ap.add_argument("--offline", action="store_true", help="只用缓存，不联网")
    args = ap.parse_args()

    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    cache = Path(args.cache)
    cache.mkdir(parents=True, exist_ok=True)
    raw_dir = ROOT / "assets" / "raw"

    for a in data["assets"]:
        if args.id and a["id"] not in args.id:
            continue
        src = cache / (a["id"] + a["source"]["ext"])
        if not src.exists():
            if args.offline:
                print(f"[跳过] {a['id']}：缓存缺失且 --offline")
                continue
            download(a["source"]["url"], src)

        digest = sha256(src)
        if digest != a["source"]["sha256"]:
            print(f"[失败] {a['id']}：SHA256 不符\n  期望 {a['source']['sha256']}\n  实得 {digest}")
            continue
        size = src.stat().st_size
        if size != a["source"]["bytes"]:
            print(f"[警告] {a['id']}：体积 {size} ≠ 清单 {a['source']['bytes']}（源站可能更新，校验和一致则忽略）")

        out = raw_dir / f"{a['id']}.mp4"
        normalize(src, out, a["clip"]["start"], a["clip"]["dur"])
        meta = {
            "id": a["id"], "role": a.get("role", a.get("case_role", "")), "case_role": a.get("case_role", ""), "desc": a["desc"],
            "license": a["license"], "source_url": a["source"]["url"],
            "source_sha256": digest, "clip": a["clip"],
            "master": str(out.relative_to(ROOT)).replace("\\", "/"),
            "master_sha256": sha256(out),
            "verify": a.get("verify", ""),
        }
        (raw_dir / f"{a['id']}.meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[完成] {a['id']} → {out.name}  {out.stat().st_size/1e6:.1f}MB  许可={a['license']['name']}")


if __name__ == "__main__":
    main()
