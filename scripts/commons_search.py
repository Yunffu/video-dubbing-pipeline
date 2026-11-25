#!/usr/bin/env python3
"""在 Wikimedia Commons 上检索**真实片源**（场景素材）。

为什么需要它：
  - 业务场景要的是真实内容（真中文硬字幕、真外语片），不是合成的理想素材；
  - Commons 的许可元数据可程序化核验（CC / PD），符合仓库红线；
  - Commons API 有 429 限流 → 统一退避重试，不要散落在各处。

用法:
    python scripts/commons_search.py --query "filetype:video chinese subtitles" --limit 8
    python scripts/commons_search.py --query "..." --json out.json      # 连元数据一起落盘
"""
import argparse
import json
import sys
import time
import urllib.parse
import urllib.request

API = "https://commons.wikimedia.org/w/api.php"
UA = "video-dubbing-pipeline/0.1 (educational research; contact: local)"


def api(**kw) -> dict:
    """调 Commons API，遇 429 退避重试（最多 4 次）。"""
    kw.setdefault("format", "json")
    kw.setdefault("action", "query")
    url = API + "?" + urllib.parse.urlencode(kw)
    delay = 3
    last = None
    for attempt in range(4):
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            last = e
            code = getattr(e, "code", None)
            if code not in (429, 503):
                raise
            time.sleep(delay)
            delay *= 2
    raise RuntimeError(f"Commons API 重试耗尽: {last}")


def search(query: str, limit: int = 8) -> list[str]:
    d = api(list="search", srsearch=query, srnamespace=6, srlimit=limit)
    return [it["title"] for it in d.get("query", {}).get("search", [])]


def metadata(titles: list[str]) -> list[dict]:
    """一次最多查 20 个标题的 imageinfo（视频流/尺寸/许可/署名）。"""
    out = []
    for i in range(0, len(titles), 10):
        chunk = titles[i:i + 10]
        d = api(prop="imageinfo", titles="|".join(chunk),
                iiprop="url|size|mime|mediatype|extmetadata|dimensions")
        for p in d.get("query", {}).get("pages", {}).values():
            if "imageinfo" not in p:
                continue
            info = p["imageinfo"][0]
            em = info.get("extmetadata", {})
            g = lambda k: em.get(k, {}).get("value", "").replace("\n", " ").strip()
            out.append({
                "title": p["title"],
                "url": info["url"].split("?")[0],
                "bytes": info["size"],
                "width": info.get("width"),
                "height": info.get("height"),
                "mime": info.get("mime"),
                "license": g("LicenseShortName"),
                "license_url": g("LicenseUrl"),
                "artist": g("Artist")[:120],
                "credit": g("Credit")[:120],
                "description": g("ImageDescription")[:300],
            })
        time.sleep(1.5)  # 主动限速，别再吃 429
    return out


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--query", action="append", required=True)
    ap.add_argument("--limit", type=int, default=8)
    ap.add_argument("--meta", action="store_true", help="附带许可/尺寸/时长元数据")
    ap.add_argument("--json", help="结果落盘")
    args = ap.parse_args()

    rows = []
    for q in args.query:
        titles = search(q, args.limit)
        print(f"\n=== {q} ===")
        for t in titles:
            print("  ", t)
        rows.append({"query": q, "titles": titles})
        time.sleep(1.5)

    if args.meta:
        flat = [t for r in rows for t in r["titles"]]
        metas = metadata(flat)
        by_title = {m["title"]: m for m in metas}
        print("\n\n=== 元数据 ===")
        for r in rows:
            print(f"\n--- {r['query']}")
            for t in r["titles"]:
                m = by_title.get(t)
                if not m:
                    print(f"  [缺] {t}")
                    continue
                print(f"  {m['bytes']/1e6:8.1f}MB  {m['width']}x{m['height']}  "
                      f"{m['license']:<22} {t}")
                print(f"           artist={m['artist'][:80]}")
        rows = {"queries": rows, "metadata": metas}

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, indent=2)
        print(f"\n-> {args.json}")


if __name__ == "__main__":
    main()
