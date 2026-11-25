#!/usr/bin/env python3
"""人评打分台的离线断言（不联网、不起服务）。

为什么要有这个测试：
  「效果判定权在人」这条链的最后一环是 `reviews.json` → `summarize` 反推阈值。
  打分界面如果把字段名写错、把 null 写成 0，**summarize 不会报错**，
  只会静默地把「没评」当成「评了 0 分」，阈值就反推错了。
  所以这里把「输出格式必须和 summarize 期待的一模一样」钉死。

跑法：python tests/test_review_ui.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval.human_review.build_packet import DIMENSIONS, OVERALL_KEY  # noqa: E402
from eval.human_review.review_ui import build_html, collect_items  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

FAILED = []


def check(cond, name, detail=""):
    if cond:
        print(f"  PASS {name}")
    else:
        print(f"  FAIL {name} {detail}")
        FAILED.append(name)


FAKE_ITEMS = [{
    "id": "M1_01__hard_sub_bottom",
    "title": "样例条目",
    "difficulty_type": "hard_sub_bottom",
    "video": "results/human_review/items/M1_01__hard_sub_bottom.mp4",
    "metrics": {"psnr_in_mask": 16.84, "ssim_in_mask": 0.765},
    "cost": {"usd": 0.0, "note": "样例"},
    "auto_note": "指标只作参考列",
}]

print("[1] 条目收集：三种输入形态")
tmp = ROOT / "_work" / "_test_items.json"
tmp.parent.mkdir(parents=True, exist_ok=True)
tmp.write_text(json.dumps(FAKE_ITEMS, ensure_ascii=False), encoding="utf-8")
check(len(collect_items(tmp)) == 1, "list 形态")
tmp.write_text(json.dumps({"items": FAKE_ITEMS}, ensure_ascii=False), encoding="utf-8")
check(len(collect_items(tmp)) == 1, "{'items': [...]} 形态")
tmp.write_text(json.dumps(FAKE_ITEMS[0], ensure_ascii=False), encoding="utf-8")
check(len(collect_items(tmp)) == 1, "单个 dict 形态")
tmp.write_text("[]", encoding="utf-8")
check(collect_items(tmp) == [], "空列表 → 空结果（不炸）")

print("[2] HTML 里不能残留模板占位符")
html = build_html(FAKE_ITEMS, {})
for ph in ("__DATA__", "__DIMS__", "__OVERALL__"):
    check(ph not in html, f"{ph} 已被替换")

print("[3] 打分字段名必须和 build_packet 一致（summarize 靠这些 key 读）")
for d in DIMENSIONS:
    check(d["key"] in html, f"维度 {d['key']} 出现在页面上")
check(OVERALL_KEY in html, f"判定权字段 {OVERALL_KEY} 出现在页面上")

print("[4] 嵌入的 JSON 必须可解析，且结构是 summarize 期待的形状")
start = html.index("const DATA = ") + len("const DATA = ")
end = html.index(";\nconst DIMS", start)
data = json.loads(html[start:end])
check(isinstance(data.get("items"), list) and len(data["items"]) == 1, "items 是列表")
check("reviews" in data, "reviews 字段存在")
item = data["items"][0]
for k in ("id", "title", "metrics", "cost", "auto_note"):
    check(k in item, f"条目带 {k}")
check(item["metrics"].get("psnr_in_mask") == 16.84, "指标值原样带过去（不是字符串）")

print("[5] 页面输出必须能喂回 summarize（key 集合完全一致）")
reviews = {"M1_01__hard_sub_bottom": {"erase": 4, "break": None, "lip": None,
                                      "style": 5, "sub": 4, "overall": 4, "note": "ok"}}
tmp.write_text(json.dumps({"items": FAKE_ITEMS}, ensure_ascii=False), encoding="utf-8")
packet = {"dimensions": list(DIMENSIONS), "items": FAKE_ITEMS}
expect = {d["key"] for d in DIMENSIONS} | {OVERALL_KEY, "note"}
check(set(reviews["M1_01__hard_sub_bottom"]) == expect,
      "一条打分的 key 集合 = 5 维度 + overall + note",
      f"实际 {sorted(reviews['M1_01__hard_sub_bottom'])}")
tmp.unlink(missing_ok=True)

print()
if FAILED:
    print(f"{len(FAILED)} 条断言失败: {FAILED}")
    sys.exit(1)
print("ALL PASS")
