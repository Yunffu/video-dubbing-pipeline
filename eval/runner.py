#!/usr/bin/env python3
"""跑分器：跑 case 集 → 出结果表 + badcase 清单 + 与上一轮的回归 diff。

这是「第 5 层：约束与验收」的落地物。三个纪律：
  1. **同一指标只有一个口径** —— 阈值写在 spec 里，runner 只负责判定和留痕；
  2. **每行结果留痕** —— 耗时/成败/降级原因/指标值都落盘，可回溯；
  3. **回归只许变好** —— 改方案后重跑，本来 PASS 的 case 必须还 PASS。

用法:
    python eval/runner.py --case-set all
    python eval/runner.py --case-set all --baseline results/2026-11-25_120000
    python eval/runner.py --stage subtitle_erase --case-set all
"""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import cv2  # noqa: E402

from eval import metrics as M  # noqa: E402
from eval import thresholds as T  # noqa: E402
from pipeline import subtitle_erase as se  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

RESULTS = ROOT / "results"


def load_cases() -> list[dict]:
    data = json.loads((ROOT / "assets" / "cases.json").read_text(encoding="utf-8"))
    return data["cases"]


def run_subtitle_erase(case: dict, work: Path, frames: int) -> dict:
    """字幕擦除：用「合成 GT 法」跑方案 A，并给出 baseline 对照。

    两种字幕走两套定位，但共用同一套擦除/指标/判定链路：
      hard_sub_bottom → 画面底部高密度横带（se.detect_subtitle_band）
      hard_sub_face   → 压在脸上的字幕（se.detect_face_subtitle，矩形）
    """
    t0 = time.time()
    derived = ROOT / case["derived_file"]
    master = ROOT / case["master"]
    if not derived.exists() or not master.exists():
        return {"status": "skip", "reason": "素材缺失", "pass": False, "verdict": "SKIP"}

    is_face = case.get("difficulty_type") == "hard_sub_face"
    band = se.detect_face_subtitle(derived) if is_face else se.detect_subtitle_band(derived)
    if not band.get("found"):
        # 降级路径：检不到字幕 → 不擦、记 degraded（不静默通过）
        return {"status": "degraded", "pass": False, "verdict": "DEGRADED",
                "reason": band.get("reason") or band.get("error", "未检出字幕"),
                "elapsed_ms": int((time.time() - t0) * 1000)}

    # 掩膜统一成矩形：底部字幕是全宽横带，人脸字幕是检测出的矩形
    x0, y0, x1, y1 = se.region_of(band, master_wh(derived)[0])
    region = (x0, y0, x1, y1)

    # 评测口径：**不落盘**（无损中间件一条 25s 片子 580MB，会把盘撑爆；
    # 落盘还会把编码损失混进修补质量里）。落盘成片走 scripts/erase_subtitles.py。
    t_start = time.time()
    mm = M.subtitle_erase_in_memory(derived, master, region,
                                    scheme="opencv_telea", n_frames=frames)
    if "error" in mm:
        return {"status": "fail", "pass": False, "verdict": "FAIL", "reason": mm["error"]}

    mvals = {
        "psnr_in_mask": mm["after"]["psnr_in_mask"]["median"],
        "ssim_in_mask": mm["after"]["ssim_in_mask"]["median"],
        "psnr_out_mask_delta": mm["delta"]["psnr_out_mask"],
        "untouched_out_mask": mm["untouched_out_mask"],
    }
    jd = T.judge(mvals, scheme="opencv_telea")

    W, H = master_wh(derived)
    return {
        "status": "ok",
        # WARN 不算失败：硬约束过了，只是没达到本档调优指标
        "pass": jd["verdict"] != "FAIL",
        "verdict": jd["verdict"],
        "metrics": {
            **mvals,
            "psnr_in_mask_p25": mm["after"]["psnr_in_mask"]["p25"],
            "psnr_in_mask_p75": mm["after"]["psnr_in_mask"]["p75"],
            "psnr_in_mask_baseline": mm["baseline"]["psnr_in_mask"]["median"],
            "psnr_in_mask_delta": mm["delta"]["psnr_in_mask"],
            "psnr_out_mask": mm["after"]["psnr_out_mask"]["median"],
            "frames_compared": mm["frames_compared"],
            "frames_black_skipped": mm["frames_black_skipped"],
            "mask_area_ratio": round(((x1 - x0) * (y1 - y0)) / max(W * H, 1), 4),
            "tier_flags": T.tier_flags(mvals),
            # 口径指纹：跨 run 比较前必须一致，否则 diff 没意义
            # v1 = 采样帧均值（含近黑帧 → 恒 99dB 拉高）; v2 = 中位数 + 近黑帧剔除
            "metric_profile": "median_v2_blackskipped",
            # 判定指纹：v1 = 单一 30dB 门槛; v2 = 双档阈值 + 硬约束/调优项分开
            "judge_profile": "tier_v2",
        },
        "threshold": jd["tier_threshold"],
        "tier": jd["tier"],
        "failed_checks": jd["failed"],
        "warned_checks": jd["warned"],
        "band": ({"rect": band["rect"], "face": band.get("face")}
                 if is_face else
                 {"y0": band["y0"], "y1": band["y1"], "bands": band["bands"],
                  "hit_ratio": band["hit_ratio"]}),
        "elapsed_ms": int((time.time() - t_start) * 1000),
        "note": "合成 GT 法（母版即 GT）；PSNR 为编码前口径（不落盘）",
    }


def master_wh(path: Path) -> tuple[int, int]:
    cap = cv2.VideoCapture(str(path))
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()
    return w, h


def master_h(path: Path) -> int:
    cap = cv2.VideoCapture(str(path))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()
    return h or 720


STAGES = {
    "subtitle_erase": {
        "fn": run_subtitle_erase,
        "applies_to": ["hard_sub_bottom", "hard_sub_face"],
        "spec": "spec/subtitle_erase.yaml",
    },
    # 下面这些是「已知要接但还没接」的环节 —— 显式写出来，避免假装全跑通了
    "asr": {"fn": None, "applies_to": ["noisy"],
            "why": "缺参考转写（ASR 无 GT）→ 只能人评"},
    "translate": {"fn": None, "applies_to": [],
                  "why": "缺参考译文 → Δt 需要槽位真值"},
    "lipsync": {"fn": None, "applies_to": ["mouth_occluded"],
                "why": "SyncNet 未安装；且译制片音画不同源 → 偏移不可作质量判据"},
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--case-set", default="all")
    ap.add_argument("--stage", default="subtitle_erase")
    ap.add_argument("--frames", type=int, default=8)
    ap.add_argument("--baseline", help="上一轮 run_id 目录（做回归 diff）")
    ap.add_argument("--run-id")
    args = ap.parse_args()

    cases = load_cases()
    if args.case_set != "all":
        want = set(args.case_set.split(","))
        cases = [c for c in cases if c["case_id"] in want
                 or c["difficulty_type"] in want]
    stage = STAGES.get(args.stage)
    if stage is None:
        sys.exit(f"未知 stage: {args.stage}")

    run_id = args.run_id or time.strftime("%Y-%m-%d_%H%M%S")
    outdir = RESULTS / run_id
    work = outdir / "work"
    work.mkdir(parents=True, exist_ok=True)

    rows = []
    if stage["fn"] is None:
        print(f"[{args.stage}] 尚未接入：{stage.get('why', '')}")
    else:
        for c in cases:
            if c["difficulty_type"] not in stage["applies_to"]:
                continue
            res = stage["fn"](c, work, args.frames)
            res.update({"case_id": c["case_id"],
                        "difficulty_type": c["difficulty_type"],
                        "stage": args.stage, "run_id": run_id,
                        "spec": stage["spec"]})
            rows.append(res)
            flag = (res.get("verdict") or ("PASS" if res.get("pass") else "FAIL"))
            if res.get("status") in ("skip", "degraded"):
                flag = res["status"].upper()
            m = res.get("metrics", {})
            print(f"[{flag:8s}] {c['case_id']:28s} "
                  f"in={m.get('psnr_in_mask')} ssim={m.get('ssim_in_mask')} "
                  f"{res.get('reason', '')}")

    # metrics.jsonl：每行留痕
    with (outdir / "metrics.jsonl").open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    # table.md：给人看的表
    n_pass = sum(1 for r in rows if r.get("pass"))
    n_warn = sum(1 for r in rows if r.get("verdict") == "WARN")
    lines = [f"# 跑分结果 {run_id}", "",
             f"stage: `{args.stage}` · spec: `{stage['spec']}` · "
             f"通过 **{n_pass}/{len(rows)}**（其中 WARN {n_warn} 条：硬约束过了，"
             f"只是没达到本档调优指标）", "",
             "判定口径：硬约束（区外不误伤 / untouched≥60dB）不过 → **FAIL**；"
             "字幕区 PSNR/SSIM 按方案分档：A档 25dB/0.75、B档 30dB/0.90，"
             "未达本档 → **WARN**（不判 FAIL）。", "",
             "| case | 难度 | 结果 | 档位 | 字幕区 PSNR | 基线 | Δ | SSIM | 区外Δ | A档 | B档 | 备注 |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        m = r.get("metrics", {})
        if r.get("status") == "degraded":
            verdict = "DEGRADED"
        elif r.get("status") == "skip":
            verdict = "SKIP"
        else:
            verdict = r.get("verdict") or ("PASS" if r.get("pass") else "FAIL")
        tf = m.get("tier_flags") or {}
        lines.append(
            f"| {r['case_id']} | {r['difficulty_type']} | {verdict} | "
            f"{r.get('tier', '-')} | "
            f"{m.get('psnr_in_mask', '-')} | {m.get('psnr_in_mask_baseline', '-')} | "
            f"{m.get('psnr_in_mask_delta', '-')} | {m.get('ssim_in_mask', '-')} | "
            f"{m.get('psnr_out_mask_delta', '-')} | "
            f"{tf.get('A', '-')} | {tf.get('B', '-')} | "
            f"{r.get('reason', '') or ';'.join(r.get('failed_checks', {}).keys())} |")
    (outdir / "table.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    # badcases.md：失败的 case + 归因；WARN 单列（不是失败，是待调优）
    bad = [r for r in rows if not r.get("pass")]
    warns = [r for r in rows if r.get("verdict") == "WARN"]
    bl = [f"# badcase 清单 {run_id}", "",
          f"FAIL {len(bad)} 个（占 {len(bad)}/{len(rows)}）；"
          f"WARN {len(warns)} 个（硬约束已过，仅未达本档调优指标）。", ""]
    for r in bad + warns:
        tag = "FAIL" if not r.get("pass") else "WARN"
        bl += [f"## {r['case_id']}（{r['difficulty_type']}）[{tag}]", "",
               f"- 状态：{r.get('status')} · 档位：{r.get('tier', '-')}"
               f"（{T.TIER_LABEL.get(r.get('tier'), '-')}）"]
        if r.get("reason"):
            bl.append(f"- 原因：{r['reason']}")
        for k, v in (r.get("failed_checks") or {}).items():
            bl.append(f"- 未达标 `{k}`：{v['value']} < 阈值 {v['threshold']}")
        for k, v in (r.get("warned_checks") or {}).items():
            bl.append(f"- 未达调优项 `{k}`：{v['value']} < 阈值 {v['threshold']}")
        if r.get("band"):
            if "rect" in r["band"]:
                bl.append(f"- mask（人脸矩形）：{r['band']['rect']}")
            else:
                bl.append(f"- mask：rows {r['band']['y0']}–{r['band']['y1']} "
                          f"（bands={r['band']['bands']}，命中率 {r['band']['hit_ratio']}）")
        bl.append("")
    (outdir / "badcases.md").write_text("\n".join(bl) + "\n", encoding="utf-8")

    # 回归 diff：只让本来就过的 case 保持过
    if args.baseline:
        base = RESULTS / args.baseline / "metrics.jsonl"
        if base.exists():
            prev, prev_profile = {}, None
            for line in base.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    o = json.loads(line)
                    prev[(o["case_id"], o["stage"])] = o.get("pass")
                    if prev_profile is None:
                        om = o.get("metrics") or {}
                        prev_profile = (om.get("metric_profile"), om.get("judge_profile"))
            cur_profile = None
            for r in rows:
                if r.get("metrics"):
                    rm = r["metrics"]
                    cur_profile = (rm.get("metric_profile"), rm.get("judge_profile"))
                    break
            reg, fixed = [], []
            for r in rows:
                k = (r["case_id"], r["stage"])
                if k in prev and prev[k] and not r.get("pass"):
                    reg.append(r["case_id"])
                elif k in prev and not prev[k] and r.get("pass"):
                    fixed.append(r["case_id"])
            same_profile = (cur_profile is None or prev_profile is None
                            or cur_profile == prev_profile)
            if not same_profile:
                # 口径不同的两轮不能直接比 pass/fail —— 那不是回归，是换了尺子
                msg = (f"⚠ 基线口径 指标`{prev_profile[0]}`/判定`{prev_profile[1]}` "
                       f"≠ 本轮口径 指标`{cur_profile[0]}`/判定`{cur_profile[1]}`："
                       f"pass/fail 不可直接比较（差值来自口径而非方案变化），"
                       f"以下 diff 仅作参考")
            else:
                msg = ""
            print(f"\n回归 diff vs {args.baseline}: {msg}"
                  f"新增失败 {len(reg)} 条 {reg} · 新修复 {len(fixed)} 条 {fixed}")
            (outdir / "regression.md").write_text(
                f"# 回归 diff（vs {args.baseline}）\n\n"
                f"{msg + chr(10) if msg else ''}"
                f"- 新增失败：{reg or '无'}\n- 新修复：{fixed or '无'}\n",
                encoding="utf-8")
        else:
            print(f"找不到基线：{base}")

    print(f"\n→ {outdir}")


if __name__ == "__main__":
    main()
