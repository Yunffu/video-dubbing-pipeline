"""在 Internet Archive 搜索公共领域(PD)视频素材候选，按许可与描述过滤。

用法: python _work/search_archive.py [查询 ...]
"""
import json
import sys
import urllib.parse
import urllib.request

UA = "Mozilla/5.0 (PersonalAgentProject/1.0)"
API = "https://archive.org/advancedsearch.php"


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    queries = sys.argv[1:] or [
        'mediatype:movies AND licenseurl:(*publicdomain*) AND (subject:("speech") OR subject:("interview") OR subject:("lecture"))',
        'mediatype:movies AND licenseurl:(*publicdomain*) AND collection:(prelinger)',
        'mediatype:movies AND licenseurl:(*publicdomain*) AND title:("talking head" OR "speech by" OR "interview with")',
    ]
    seen = {}
    for q in queries:
        url = API + "?" + urllib.parse.urlencode({
            "q": q,
            "fl[]": ["identifier", "title", "licenseurl", "year", "runtime", "subject", "downloads"],
            "rows": 40, "output": "json",
        })
        try:
            d = get(url)
        except Exception as e:
            print(f"# 查询失败: {e}")
            continue
        for doc in d.get("response", {}).get("docs", []):
            ident = doc.get("identifier")
            if ident:
                seen[ident] = doc
        print(f"# {q[:60]} → 累计 {len(seen)}")

    print("\n=== 候选（按下载量排序）===")
    for ident, doc in sorted(seen.items(), key=lambda x: -(x[1].get("downloads") or 0))[:40]:
        lic = doc.get("licenseurl", "?")
        if isinstance(lic, list):
            lic = ",".join(lic)
        subs = doc.get("subject", [])
        if isinstance(subs, str):
            subs = [subs]
        print(f"{ident}")
        print(f"   标题: {str(doc.get('title'))[:70]}")
        print(f"   许可: {lic}  年份: {doc.get('year')}  时长: {doc.get('runtime')}  下载: {doc.get('downloads')}")
        print(f"   主题: {', '.join(map(str, subs[:6]))}")


if __name__ == "__main__":
    main()
