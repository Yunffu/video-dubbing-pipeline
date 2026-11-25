"""验收阈值口径：硬约束 vs 调优项分开，调优项按方案分两档。

口径由用户于 2026-11-25 拍板，理由如下。

一、硬约束与调优项必须分开
    「区外不得误伤」是**硬约束**：mask 之外的像素必须原封不动。
    这条没有商量余地 —— 擦了字幕却把画面改了，比没擦更糟。
    「字幕区 PSNR / SSIM」是**调优项**：擦得多干净取决于算法能力，
    不该拿来当一票否决。

二、为什么调优项要分两档
    方案 A（OpenCV inpaint，纯 CPU 零依赖）的算法上限是硬事实。
    实测 scripts/erase_sweep.py 用 **oracle 完美遮罩**跑出来的最好成绩：
        M1_04  PSNR 29.96dB / SSIM 0.50
    也就是说，换任何实现都到不了 30dB。若把 30dB 当唯一阈值，
    等于用指标逼着换方案 —— 把「选型」和「验收」混成一件事，
    跑分器就退化成了「按方案 A 判它必挂」。

    分档之后口径变成：
        A 档（方案 A 能达到的）→ PSNR 25 / SSIM 0.75
        B 档（方案 B 的目标）   → PSNR 30 / SSIM 0.90
    「这个难例该用哪个方案」变成跑分器给出的**结论**，而不是事前拍死的门槛。

三、判定规则
    硬约束任一未过                        → FAIL（一票否决）
    硬约束过了、调优项未达本档             → WARN（**不判 FAIL**，记为待调优）
    全部达标                              → PASS

    注意「调优项未达本档 = WARN」是用户拍板的硬规则，不设例外：
    调优项只用来回答「离这一档还差多少」，不用来一票否决 ——
    否则等于「拿指标逼着换方案」，把选型和验收混成一件事。

对外只暴露一个数字：**本档判定 PASS/WARN/FAIL**，两档都写进结果表
（A档 ✅❌ / B档 ✅❌ 两列），这样「方案 A 的真实水平」和
「距离方案 B 还差多少」同时可见。
"""
from __future__ import annotations

# 调优项分档：key=档位名，value=该档的门槛
TIERS: dict[str, dict[str, float]] = {
    "A": {"psnr_in_mask": 25.0, "ssim_in_mask": 0.75},
    "B": {"psnr_in_mask": 30.0, "ssim_in_mask": 0.90},
}

TIER_LABEL = {
    "A": "方案A（OpenCV inpaint，纯CPU）",
    "B": "方案B（视频修复模型 LaMa/ProPainter）",
}

# 硬约束：不达标一律 FAIL，不能降级成 WARN
HARD_CONSTRAINTS: dict[str, float] = {
    # 区外 PSNR 相对基线的变化不得为负（不得比擦之前更差）
    "psnr_out_mask_delta": -0.5,
    # mask 之外的像素必须原封不动（理论上 99dB，60dB 已经很宽松）
    "untouched_out_mask": 60.0,
}


def tier_of(scheme: str) -> str:
    """方案名 → 档位名。"""
    return "A" if "telea" in scheme or "ns" in scheme else "B"


def judge(metrics: dict, scheme: str = "opencv_telea",
          tier: str | None = None) -> dict:
    """按「硬约束 + 分档调优项」判定一个 case。

    metrics 需要含：psnr_in_mask / ssim_in_mask / psnr_out_mask_delta /
                    untouched_out_mask（None 表示该指标没算出来，不参与判定）
    返回 {verdict, failed, warned, tier, tier_threshold}
    """
    tier = tier or tier_of(scheme)
    cur = TIERS[tier]
    lower = TIERS["A"] if tier == "B" else None

    failed: dict[str, dict] = {}
    warned: dict[str, dict] = {}

    # 1) 硬约束：不达标一律 FAIL
    for k, thr in HARD_CONSTRAINTS.items():
        v = metrics.get(k)
        if v is None:
            continue
        if v < thr:
            failed[k] = {"value": v, "threshold": thr, "kind": "hard"}

    # 2) 调优项：未达本档 → WARN（不判 FAIL）
    for k in ("psnr_in_mask", "ssim_in_mask"):
        v = metrics.get(k)
        if v is None:
            continue
        if v >= cur[k]:
            continue
        w = {"value": v, "threshold": cur[k], "kind": "tuning"}
        if lower is not None:
            # B 档：连 A 档都不到 → 额外标注（仍记 WARN，FAIL 只留给硬约束）
            w["lower_tier_threshold"] = lower[k]
            w["below_lower_tier"] = v < lower[k]
        warned[k] = w

    verdict = "FAIL" if failed else ("WARN" if warned else "PASS")
    return {"verdict": verdict, "failed": failed, "warned": warned,
            "tier": tier, "tier_threshold": dict(cur),
            "tier_label": TIER_LABEL.get(tier, tier)}


def tier_flags(metrics: dict) -> dict[str, str]:
    """两档各自的达标情况（用于结果表同时显示 A 档 / B 档）。"""
    out = {}
    for name, thr in TIERS.items():
        marks = []
        for k in ("psnr_in_mask", "ssim_in_mask"):
            v = metrics.get(k)
            marks.append("✅" if v is not None and v >= thr[k] else "❌")
        out[name] = "".join(marks)
    return out
