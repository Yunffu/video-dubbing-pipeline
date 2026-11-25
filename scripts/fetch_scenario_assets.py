#!/usr/bin/env python3
"""按 assets/scenario_manifest.json 重建**真实场景素材**（下载 → 校验 → 切片统一）。

与 fetch_assets.py（M1_* 母版）的分工：
  - fetch_assets.py      → 可控 GT 评测集（合成难例的母版）
  - fetch_scenario_assets.py → 真实业务片源（真硬字幕 / 真外语 / 真动画帧）

首次运行会把算出的 SHA256 回写进清单，之后每次重建都比对校验和 ——
片源被上游改动时能立刻发现，而不是悄悄换了素材。

用法:
    python scripts/fetch_scenario_assets.py --id S1_02 --id S2_01
    python scripts/fetch_scenario_assets.py --offline     # 只用缓存
"""
import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "assets" / "scenario_manifest.json"
UA = "PersonalAgentProject/1.0 (educational; non-commercial)"
MAX_WIDTH = 1280  # CPU 上跑 inpaint/LaMa，1080p 太慢，统一降到 720p 级


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(url: str, dest: Path, tries: int = 3) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    last = None
    for attempt in range(tries):
        try:
            print(f"  下载 {url[:110]}")
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=300) as r, dest.open("wb") as f:
                while True:
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
            print(f"  完成 {dest.stat().st_size/1e6:.1f}MB")
            return
        except Exception as e:
            last = e
            print(f"  重试 {attempt+1}/{tries}: {e}")
            time.sleep(8)
    raise RuntimeError(f"下载失败: {url}\n{last}")


def normalize(src: Path, out: Path, start: float, dur: float, fps: int = 25) -> None:
    """切片 + 限宽 + 统一帧率 + h264/aac。"""
    out.parent.mkdir(parents=True, exist_ok=True)
    vf = (f"scale='min({MAX_WIDTH},iw)':-2:flags=bicubic,"
          f"scale=trunc(iw/2)*2:trunc(ih/2)*2,fps={fps},format=yuv420p")
    cmd = [shutil.which("ffmpeg"), "-y", "-v", "error",
           "-ss", f"{start}", "-t", f"{dur}", "-i", str(src),
           "-vf", vf,
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
           "-profile:v", "high", "-c:a", "aac", "-ar", "48000", "-ac", "2",
           "-b:a", "128k", "-movflags", "+faststart", str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise SystemExit(f"ffmpeg 失败:\n{r.stderr}")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", action="append")
    ap.add_argument("--cache", default=str(ROOT.parent / "_work" / "cache"))
    ap.add_argument("--offline", action="store_true")
    args = ap.parse_args()

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    cache = Path(args.cache)
    cache.mkdir(parents=True, exist_ok=True)
    raw_dir = ROOT / "assets" / "raw"
    changed = False

    for a in manifest["assets"]:
        if args.id and a["id"] not in args.id:
            continue
        src_spec = a["source"]
        src = cache / (a["id"] + src_spec["ext"])
        if not src.exists():
            if args.offline:
                print(f"[跳过] {a['id']}：缓存缺失且 --offline")
                continue
            download(src_spec["url"], src)

        digest = sha256(src)
        expect = src_spec.get("sha256") or ""
        if expect and digest != expect:
            print(f"[失败] {a['id']}：SHA256 不符\n  期望 {expect}\n  实得 {digest}")
            continue
        if not expect:
            src_spec["sha256"] = digest
            changed = True
            print(f"[登记] {a['id']} SHA256={digest[:16]}… 已回写清单")

        out = raw_dir / f"{a['id']}.mp4"
        normalize(src, out, a["clip"]["start"], a["clip"]["dur"])
        meta = {
            "id": a["id"], "scenario": a["scenario"], "role": a["role"],
            "desc": a["desc"], "license": a["license"],
            "source_title": src_spec["title"], "source_url": src_spec["url"],
            "source_sha256": digest, "clip": a["clip"],
            "audio_lang": a["audio_lang"], "subtitle_lang": a["subtitle_lang"],
            "notes": a.get("notes", ""),
            "master": str(out.relative_to(ROOT)).replace("\\", "/"),
            "master_sha256": sha256(out),
        }
        (raw_dir / f"{a['id']}.meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[完成] {a['id']} → {out.name}  {out.stat().st_size/1e6:.1f}MB  "
              f"许可={a['license']['name']}")

    if changed:
        MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                            encoding="utf-8")
        print(f"→ 清单已更新: {MANIFEST}")


if __name__ == "__main__":
    main()
