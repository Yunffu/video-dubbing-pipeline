#!/usr/bin/env python3
"""人评工作台：为「效果判定权在人」这件事搭一条可执行的链路。

对应用户第 2 点：「效果由我评判，并且我评判后我们来计算刚刚使用的成本，
在效果可控的时候降低 API 账单」。

这条链路由三段组成，缺一段就闭环不起来：

    ① 出题   —— 把「哪个成片、哪个指标、这次花了多少钱」摆到人面前（build）
    ② 打分   —— 人填 review.json（不填也行，填了才有阈值）
    ③ 反推   —— 用人的打分反推**阈值**，并算出「效果达标的那部分花了多少钱」（summarize）

关键设计：**自动指标只作为参考列，不作为判定权**。
`summarize` 会把「人判不可接受」的样本的指标分布拉出来，
给出「阈值候选」——这才是 `spec/*.yaml` 里阈值的来源，
而不是我按行业常识填的初值。
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# 人评维度：每一项都由用户给 1-5 分（5=最好），可留空
DIMENSIONS = [
    {"key": "erase", "label": "字幕擦除痕迹是否明显", "hint": "5=完全看不出擦过；1=一眼假"},
    {"key": "break", "label": "断句是否自然", "hint": "5=像人说话；1=断得很碎/连在一起"},
    {"key": "lip", "label": "口型是否假", "hint": "5=看不出；1=明显不同步（本项目可能不做口型）"},
    {"key": "style", "label": "翻译腔是否可接受", "hint": "5=地道；1=生硬直译"},
    {"key": "sub", "label": "字幕是否遮挡画面", "hint": "5=不挡；1=挡住关键信息"},
]
OVERALL_KEY = "overall"

# 这些**不是输出质量指标**，不能拿来定阈值：
#   psnr_baseline —— 是输入素材自带的编码损失，与成片好坏无关（偶然相关也会误导）
#   psnr_delta   —— 相对 baseline 的净收益，同理
#   untouched_*  —— 是硬约束（不误伤），达标即可，不是可调档位
NON_QUALITY_METRICS = {"psnr_baseline", "psnr_delta", "untouched_out_mask"}


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def build(args):
    """出题：把成片 + 指标 + 成本摆成一张待评清单。"""
    items = []
    src = Path(args.items)
    files = sorted(src.glob("*.json")) if src.is_dir() else [src]
    for p in files:
        d = _load(p)
        if not d:
            continue
        # 支持三种形态：单个条目 dict / 条目列表 / {"items": [...]}
        if isinstance(d, list):
            items.extend(x for x in d if isinstance(x, dict))
        elif isinstance(d, dict) and isinstance(d.get("items"), list):
            items.extend(x for x in d["items"] if isinstance(x, dict))
        elif isinstance(d, dict):
            items.append(d)

    packet = {
        "note": "把每个条目看成片，然后填 review.json 里的同名字段。"
                "打分标准见 dimensions[].hint；留空表示这条没评。",
        "dimensions": DIMENSIONS,
        "items": items,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(packet, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"→ {out}（{len(items)} 个待评条目）")
    for it in items:
        print(f"  {it.get('id', '?')}  {it.get('title', '')}")


def summarize(args):
    """反推阈值：用人打分把「不可接受」的样本圈出来，看它们的指标长什么样。"""
    packet = _load(Path(args.packet))
    reviews = _load(Path(args.reviews))
    if not packet or not reviews:
        sys.exit("packet 或 reviews 缺失")

    reviewed, unreviewed = [], []
    for it in packet.get("items", []):
        iid = it.get("id")
        r = reviews.get(iid)
        if not r or not r.get(OVERALL_KEY):
            unreviewed.append(iid)
            continue
        reviewed.append((it, r))

    print(f"已评 {len(reviewed)} 条，未评 {len(unreviewed)} 条"
          f"{('（未评：' + ', '.join(unreviewed) + '）') if unreviewed else ''}")

    if not reviewed:
        sys.exit("还没有任何打分，无法反推阈值")

    # 关键：以「整体不可接受」为负样本，看它们的指标分布
    bad = [(it, r) for it, r in reviewed if float(r.get(OVERALL_KEY, 5)) <= 2]
    good = [(it, r) for it, r in reviewed if float(r.get(OVERALL_KEY, 5)) >= 4]

    metrics = sorted({k for it, _ in reviewed for k in (it.get("metrics") or {})
                      if isinstance((it.get("metrics") or {})[k], (int, float))
                      and k not in NON_QUALITY_METRICS})
    report = {"reviewed": len(reviewed), "unreviewed": unreviewed,
              "bad_samples": [it.get("id") for it, _ in bad],
              "good_samples": [it.get("id") for it, _ in good],
              "metric_thresholds": {}, "cost": {}}

    print(f"\n负样本（整体 ≤2 分）{len(bad)} 条；正样本（≥4 分）{len(good)} 条")
    print(f"\n{'指标':28s} {'负样本最差':>12s} {'正样本最好':>12s} {'阈值候选':>12s}")
    print("-" * 70)
    for m in metrics:
        bvals = [(it.get("metrics") or {}).get(m) for it, _ in bad
                 if isinstance((it.get("metrics") or {}).get(m), (int, float))]
        gvals = [(it.get("metrics") or {}).get(m) for it, _ in good
                 if isinstance((it.get("metrics") or {}).get(m), (int, float))]
        if not bvals:
            continue
        worst_bad = max(bvals) if m.startswith(("ssim", "untouched")) else min(bvals)
        best_good = min(gvals) if m.startswith(("ssim", "untouched")) else max(gvals)
        cand = None
        if bvals and gvals:
            if m.startswith(("ssim", "untouched")):
                cand = max(bvals)            # 该指标越大越好 → 阈值取负样本的最大值
            else:
                cand = min(gvals)            # 该指标越小越好 → 阈值取正样本的最小值
        report["metric_thresholds"][m] = {
            "bad_max": worst_bad, "good_min": best_good,
            "candidate": cand,
            "separable": bool(bvals and gvals and
                              (max(bvals) < min(gvals) if m.startswith(("ssim", "untouched"))
                               else min(bvals) > max(gvals)))}
        print(f"{m:28s} {worst_bad:>12} {best_good:>12} "
              f"{str(cand):>12}{'  ←可分' if report['metric_thresholds'][m]['separable'] else ''}")

    # 成本：达标样本花了多少钱，不达标样本花了多少钱
    def total_cost(pairs):
        c = 0.0
        for it, _ in pairs:
            c += float((it.get("cost") or {}).get("usd", 0) or 0)
        return round(c, 4)

    report["cost"] = {"good_usd": total_cost(good), "bad_usd": total_cost(bad),
                       "all_usd": total_cost(reviewed)}
    print(f"\n成本：达标样本 ${report['cost']['good_usd']} / "
          f"不达标样本 ${report['cost']['bad_usd']} / "
          f"合计 ${report['cost']['all_usd']}")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"→ {out}")
    if not report["metric_thresholds"]:
        print("\n提示：条目里没有数值型 metrics，无法反推阈值 —— "
              "把 psnr_in_mask / ssim_in_mask 等放进 items[].metrics 再跑一次。")


def simulate(args):
    """**演示用**：按指标自动填一份假打分，验证「打分 → 反推阈值」这条链路是通的。

    ⚠️ 这不是人的判断。产物里所有文件都带 `_simulated` 标记，
    绝不能当成真的人工评分解读；真人打分请填 `reviews.json`。
    """
    packet = _load(Path(args.packet))
    items = packet.get("items") or []
    reviews = {}
    for it in items:
        m = it.get("metrics") or {}
        ssim = float(m.get("ssim_in_mask") or 0)
        psnr = float(m.get("psnr_in_mask") or 0)
        # 假打分规则：SSIM 越高给分越高（只是为了让演示有区分度）
        score = max(1, min(5, round(ssim * 5 + psnr / 60)))
        reviews[it["id"]] = {
            "erase": score, "break": score, "lip": None,
            "style": score, "sub": score, "overall": score,
            "note": "SIMULATED（演示用，非人工判断）",
            "_simulated": True,
        }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(reviews, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"→ {out}（{len(reviews)} 条**模拟**打分，仅用于验证链路）")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="生成待评清单")
    b.add_argument("--items", required=True, help="条目 JSON 文件或目录")
    b.add_argument("--out", default="results/human_review/packet.json")

    s = sub.add_parser("summarize", help="用打分反推阈值 + 算达标成本")
    s.add_argument("--packet", default="results/human_review/packet.json")
    s.add_argument("--reviews", default="results/human_review/reviews.json")
    s.add_argument("--out", default="results/human_review/thresholds.json")

    m = sub.add_parser("simulate", help="生成假打分（仅演示链路，不是人工判断）")
    m.add_argument("--packet", default="results/human_review/packet.json")
    m.add_argument("--out", default="results/human_review/reviews.simulated.json")

    args = ap.parse_args()
    if args.cmd == "build":
        build(args)
    elif args.cmd == "simulate":
        simulate(args)
    else:
        summarize(args)


if __name__ == "__main__":
    main()
