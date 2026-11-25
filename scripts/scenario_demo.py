#!/usr/bin/env python3
"""四个业务场景 → 可交付成片。

场景（按用户给的业务口径拆）:
  s1 赛博出海   中文母片(真硬字幕) → 擦字幕 → ASR → 英译 → 英配 → 回贴 → 烧英文字幕 → 成片
  s2 合法搬运   外语母片(真硬字幕) → 擦字幕 → ASR → 中译 → ①原声+中文字幕 ②中文配音版
  s3 内部工具   方案A(OpenCV inpaint) vs 方案B(LaMa ONNX) 对比表 + 破损截图
  s4 选型探路   LaMa ONNX 在本机的耗时/成本/质量结论 + demo 成片

设计原则:
  - 每一步都记耗时/方案/降级/失败原因 → run.json 可复现可回归；
  - 失败降级不崩（缺 API key / Edge-TTS 挂了就出「只换字幕版」）；
  - 成片用 libx264 落盘（不用 mp4v —— mp4v 是 MPEG-4 Part 2，有损，会把编码损失
    误判成擦除失败，见 pipeline/subtitle_erase.py 的口径说明）。

用法:
    python scripts/scenario_demo.py s1 --id S1_02
    python scripts/scenario_demo.py s2 --id S2_01
    python scripts/scenario_demo.py s3 --ids S1_02,S2_01
    python scripts/scenario_demo.py s4
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from pipeline import subtitle_erase as se  # noqa: E402
from pipeline.asr.service import ASRService  # noqa: E402
from pipeline.compose import build_audio_track, mux  # noqa: E402
from pipeline.config import ASRConfig, TranslateConfig, TTSConfig  # noqa: E402
from pipeline.contracts import StageStat  # noqa: E402
from pipeline.media import probe as media_probe  # noqa: E402
from pipeline.subtitle import build_ass, build_srt, burn_in  # noqa: E402
from pipeline.translate.registry import TranslatingService  # noqa: E402
from pipeline.tts.service import TTSService  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

MANIFEST = ROOT / "assets" / "scenario_manifest.json"
RESULTS = ROOT / "results" / "scenarios"


# ---------------------------------------------------------------- 资产

def load_manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def asset_info(aid: str) -> dict:
    m = load_manifest()
    for a in m["assets"]:
        if a["id"] == aid:
            return a
    raise SystemExit(f"清单里没有 {aid}")


def raw_path(aid: str) -> Path:
    p = ROOT / "assets" / "raw" / f"{aid}.mp4"
    if not p.exists():
        raise SystemExit(f"缺母版 {p}，先跑 scripts/fetch_scenario_assets.py")
    return p


# ---------------------------------------------------------------- 引擎

def _lama_engine():
    from pipeline.inpaint.lama_onnx import LamaOnnx
    eng = LamaOnnx()
    if not eng.available():
        raise SystemExit("缺 LaMa ONNX 模型（_work/models/lama_fp32.onnx）")
    return eng


def make_engine(name: str):
    """返回 f(frame_bgr, mask_u8) -> frame_bgr。"""
    if name == "opencv_telea":
        return lambda fr, m: cv2.inpaint(fr, m, 4, cv2.INPAINT_TELEA)
    if name == "opencv_ns":
        return lambda fr, m: cv2.inpaint(fr, m, 4, cv2.INPAINT_NS)
    if name == "lama_onnx":
        eng = _lama_engine()
        return lambda fr, m: eng.inpaint(fr, m)
    raise SystemExit(f"未知引擎 {name}")


def erase_to_h264(video: Path, out: Path, rect: tuple[int, int, int, int],
                   engine: str = "opencv_telea") -> dict:
    """逐帧修补 → 原始 BGR 管道喂给 ffmpeg → libx264 成片。

    不走 cv2.VideoWriter：mp4v（MPEG-4 Part 2）有损，会把编码损失混进擦除质量。
    """
    t0 = time.time()
    cap = cv2.VideoCapture(str(video))
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    x0, y0, x1, y1 = rect
    mask = se.make_mask_rect(h, w, x0, y0, x1, y1)
    fn = make_engine(engine)

    fb = media_probe.ffmpeg_bin()
    out.parent.mkdir(parents=True, exist_ok=True)
    # 音轨从原片带过来（擦除只动画面）—— 不带音轨的话下游 ASR 会拿到 0 段，
    # 而且「擦完的片子听不到声」本身也不是可交付的中间产物。
    cmd = [fb, "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "bgr24",
           "-s", f"{w}x{h}", "-r", f"{fps}", "-i", "pipe:0",
           "-i", str(video),
           "-map", "0:v", "-map", "1:a?",
           "-c:v", "libx264", "-preset", "medium", "-crf", "18",
           "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-ar", "48000", "-ac", "2", "-b:a", "128k",
           "-movflags", "+faststart", str(out)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)

    frames = 0
    while True:
        ok, fr = cap.read()
        if not ok:
            break
        proc.stdin.write(fn(fr, mask).tobytes())
        frames += 1
    proc.stdin.close()
    proc.wait()
    cap.release()
    if proc.returncode != 0:
        raise SystemExit(f"ffmpeg 编码失败（{engine}）")
    return {"engine": engine, "frames": frames, "fps": fps,
            "elapsed_ms": int((time.time() - t0) * 1000),
            "rect": list(rect), "output": str(out)}


# ---------------------------------------------------------------- 字幕区检测

def detect_region(video: Path) -> tuple[tuple[int, int, int, int], dict]:
    """定字幕区。

    **顺序很重要**：先信底部横带，再考虑人脸字幕。
    反过来的代价是实测过的：Sojo 大学片（S2_01）的日文字幕明明在画面底部，
    人脸检测器却在一张脸/衣服的纹理上误报了一个 238×31 的「文字带」，
    于是我们擦的是错的地方 —— 日文字幕原封不动留在成片里
    （见 results/scenarios/S2_01 的教训）。
    底部横带是全局边缘密度统计（12 帧投票），比人脸区域内的纹理判据稳。
    """
    cap = cv2.VideoCapture(str(video))
    ok, fr = cap.read()
    cap.release()
    if not ok:
        raise SystemExit(f"读不到 {video}")
    h, w = fr.shape[:2]

    band = se.detect_subtitle_band(video)
    if band.get("found"):
        band = dict(band)
        band["coarse"] = [band["y0"], band["y1"]]
        # 收紧只减面积不涨质量（见 results/mask_tighten_eval.json）：
        # 文字行召回 0.928~0.994，mask 内 PSNR 平均 -1.6dB。故默认用粗带，
        # 需要减 inpaint 面积时再开 VD_TIGHTEN_BAND=1。
        if os.environ.get("VD_TIGHTEN_BAND") == "1":
            ny0, ny1 = se.tighten_band(video, band["y0"], band["y1"])
            band["y0"], band["y1"] = ny0, ny1
            band["tightened"] = True
        r = se.region_of(band, w)
        return r, {"kind": "bottom_band", "detail": band}

    face = se.detect_face_subtitle(video)
    if face.get("found"):
        r = se.region_of(face, w)
        return r, {"kind": "face", "detail": {k: v for k, v in face.items()
                                              if k in ("rect", "face")}}

    r = (0, int(h * 0.88), w, h)
    return r, {"kind": "fallback_bottom", "detail": {"found": False,
                                                     "reason": "检测器未命中，取底部 12% 保守带"}}


# ---------------------------------------------------------------- 接触印相

def imwrite_unicode(path: Path, img: np.ndarray) -> bool:
    """cv2.imwrite 在 Windows 上遇到非 ASCII 路径会**静默失败**（返回 False，不报错）。

    本项目路径里带中文 → 一律走 imencode + 写字节，失败要看得见。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    ok, buf = cv2.imencode(path.suffix or ".png", img)
    if not ok:
        print(f"[warn] 编码失败: {path}")
        return False
    path.write_bytes(buf.tobytes())
    return True


def contact_sheet(items: list[tuple[str, np.ndarray]], out: Path,
                  crop: tuple[int, int, int, int] | None = None,
                  labels: bool = True) -> Path:
    """横向拼图：(标签, 帧)。crop 为 (x0,y0,x1,y1) 时只取该区域。"""
    tiles = []
    for label, fr in items:
        t = fr
        if crop:
            x0, y0, x1, y1 = crop
            t = fr[max(y0, 0):y1, max(x0, 0):x1]
        t = cv2.resize(t, (480, int(480 * t.shape[0] / max(t.shape[1], 1))))
        if labels:
            bar = np.zeros((26, t.shape[1], 3), dtype=np.uint8)
            cv2.putText(bar, label[:38], (6, 18),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1,
                        cv2.LINE_AA)
            t = np.vstack([bar, t])
        tiles.append(t)
    hh = max(t.shape[0] for t in tiles)
    tiles = [np.vstack([t, np.zeros((hh - t.shape[0], t.shape[1], 3), np.uint8)])
             if t.shape[0] < hh else t for t in tiles]
    sheet = np.hstack(tiles)
    out.parent.mkdir(parents=True, exist_ok=True)
    imwrite_unicode(out, sheet)
    return out


def grab(video: Path, t: float) -> np.ndarray | None:
    cap = cv2.VideoCapture(str(video))
    cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
    ok, fr = cap.read()
    if not ok:
        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
        ok, fr = cap.read()
    cap.release()
    return fr if ok else None


# ---------------------------------------------------------------- 通用译制链路

def dub_chain(src_video: Path, work: Path, src_lang: str, tgt_lang: str,
              rect: tuple[int, int, int, int], engine: str,
              label: str) -> dict:
    """擦字幕 → ASR → 翻译 → TTS → 音轨回贴 → 烧字幕。返回 run 记录。"""
    work.mkdir(parents=True, exist_ok=True)
    rec: dict = {"label": label, "src_lang": src_lang, "tgt_lang": tgt_lang,
                 "engine": engine, "stages": []}

    # 1) 擦字幕（已有产物就复用 —— 擦一遍 40s，没必要反复重来）
    erased = work / "erased.mp4"
    if erased.exists():
        erase_info = {"engine": engine, "reused": True, "output": str(erased)}
        print(f"[erase] 复用 {erased}")
    else:
        t0 = time.time()
        erase_info = erase_to_h264(src_video, erased, rect, engine)
        print(f"[erase] {engine}  {erase_info['frames']}帧  {erase_info['elapsed_ms']}ms")
    rec["stages"].append({"name": "erase", **erase_info})

    # 2) ASR（在擦完的片子上听 —— 业务上就是「没有原声字幕也能听清」）
    asr_svc = ASRService(ASRConfig())
    asr = asr_svc.transcribe(str(erased), None if src_lang == "auto" else src_lang)
    rec["stages"].append({"name": "asr", "provider": asr.stat.provider,
                          "language": asr.language,
                          "segments": len(asr.segments),
                          "elapsed_ms": asr.stat.elapsed_ms,
                          "degraded": asr.stat.degraded, "extra": asr.stat.extra})
    print(f"[asr] {len(asr.segments)} 段 lang={asr.language} "
          f"degraded={asr.stat.degraded} {asr.stat.elapsed_ms}ms")
    if not asr.segments:
        rec["error"] = "ASR 无输出"
        return rec

    # 3) 翻译
    tr_svc = TranslatingService(TranslateConfig())
    tr = tr_svc.translate(asr.segments, src_lang, tgt_lang)
    rec["stages"].append({"name": "translate", "provider": tr.stat.provider,
                          "model": tr.stat.model, "segments": len(tr.segments),
                          "elapsed_ms": tr.stat.elapsed_ms,
                          "degraded": tr.stat.degraded,
                          "warnings": tr.warnings[:3],
                          "cost": tr.stat.extra.get("total_tokens")})
    print(f"[translate] {tr.stat.provider} 降级={tr.stat.degraded} "
          f"警告={tr.warnings[:1]}")
    if not tr.segments:
        rec["error"] = "翻译无输出"
        return rec

    # 4) 字幕文件（外挂 + 烧录用）
    info = media_probe.probe(src_video)
    W = info.get("width") or 1280
    H = info.get("height") or 720
    dur = info.get("duration") or 0.0
    ass_path = build_ass(tr.segments, work / "subtitles.ass", width=W, height=H)
    build_srt(tr.segments, work / "subtitles.srt")

    # 5) TTS → 音轨回贴 → 合成
    tts_svc = TTSService(TTSConfig())
    tts = tts_svc.synthesize(tr.segments, tgt_lang, work)
    rec["stages"].append({"name": "tts", "provider": tts.stat.provider,
                          "voice": tts.voice, "segments": len(tts.segments),
                          "elapsed_ms": tts.stat.elapsed_ms,
                          "degraded": tts.stat.degraded})
    print(f"[tts] {len(tts.segments)} 段 voice={tts.voice} "
          f"降级={tts.stat.degraded}")

    dub_mp4 = None
    if tts.segments:
        wav = work / "dub_track.wav"
        mix = build_audio_track(tts.segments, wav, total_duration=dur)
        rec["stages"].append({"name": "compose_audio", "ok": mix["ok"],
                              "error": mix.get("error", ""),
                              "stats": mix.get("stats", {})})
        print(f"[compose] {mix.get('stats')}")
        if mix["ok"]:
            dub_mp4 = work / "dub.mp4"
            ok, err = mux(erased, wav, dub_mp4)
            rec["stages"].append({"name": "mux", "ok": ok, "error": err})
            print(f"[mux] {'OK' if ok else '失败: ' + err}")

    # 6) 烧录目标语言字幕
    final = None
    base = dub_mp4 if dub_mp4 and dub_mp4.exists() else erased
    final = work / "final.mp4"
    ok, err = burn_in(base, ass_path, final)
    rec["stages"].append({"name": "burn_subtitles", "ok": ok, "error": err,
                          "source": str(base)})
    print(f"[subtitle] 烧录 {'OK' if ok else '失败: ' + err} → {final}")

    rec.update({"erased": str(erased), "ass": str(ass_path),
                "dub": str(dub_mp4) if dub_mp4 else None,
                "final": str(final) if ok else None,
                "segments": [{"index": s.index, "start": s.start, "end": s.end,
                              "src": a.text, "tgt": s.text}
                             for a, s in zip(asr.segments, tr.segments)]})
    return rec


# ---------------------------------------------------------------- 场景一

def scenario_s1(aid: str, out_dir: Path) -> dict:
    """赛博出海：中文硬字幕片 → 英文配音成片。"""
    info = asset_info(aid)
    src = raw_path(aid)
    work = out_dir / aid
    work.mkdir(parents=True, exist_ok=True)
    tgt = "en"

    rect, det = detect_region(src)
    print(f"[{aid}] 字幕区 {rect}  方式={det['kind']}")

    rec = dub_chain(src, work, info["audio_lang"], tgt, rect,
                    "opencv_telea", "赛博出海·中文→英文配音")
    rec["asset"] = {k: info[k] for k in ("id", "desc", "license", "credit")
                    if k in info}
    rec["asset"]["credit"] = info["license"]["credit"]
    rec["detection"] = det

    # 接触印相：原片 / 擦完 / 成片
    sheet = None
    if rec.get("final"):
        t = 12.0
        a = grab(src, t)
        b = grab(Path(rec["erased"]), t)
        c = grab(Path(rec["final"]), t)
        if a is not None and b is not None and c is not None:
            sheet = contact_sheet([("original (CN hard-sub)", a),
                                   ("erased", b),
                                   (f"final (EN dub+sub)", c)],
                                  work / "contact_sheet.png")
            rec["contact_sheet"] = str(sheet)
    (work / "run.json").write_text(json.dumps(rec, ensure_ascii=False, indent=2),
                                    encoding="utf-8")
    return rec


# ---------------------------------------------------------------- 场景二

def scenario_s2(aid: str, out_dir: Path) -> dict:
    """合法搬运：外语硬字幕片 → 换授权中文字幕（保留原声）+ 中文配音版。"""
    info = asset_info(aid)
    src = raw_path(aid)
    work = out_dir / aid
    work.mkdir(parents=True, exist_ok=True)
    tgt = "zh"

    rect, det = detect_region(src)
    print(f"[{aid}] 字幕区 {rect}  方式={det['kind']}")

    rec = dub_chain(src, work, info["audio_lang"], tgt, rect,
                    "opencv_telea", "合法搬运·外语→中文字幕+中文配音")
    rec["asset"] = {"id": aid, "desc": info["desc"],
                    "license": info["license"], "clip": info["clip"],
                    "subtitle_lang": info["subtitle_lang"]}
    rec["detection"] = det

    # 只换字幕不配音版（保留原声）—— 合法搬运的经典交付形态
    subs_only = None
    erased = Path(rec.get("erased") or "")
    if erased.exists() and rec.get("ass"):
        subs_only = work / "subs_only_zh.mp4"
        ok, err = burn_in(erased, Path(rec["ass"]), subs_only)
        rec["stages"].append({"name": "burn_subs_only", "ok": ok, "error": err})
        rec["subs_only"] = str(subs_only) if ok else None
        print(f"[subs-only] {'OK' if ok else '失败: ' + err}")

    sheet = None
    final = rec.get("final") or rec.get("erased")
    if final:
        t = 12.0
        a = grab(src, t)
        b = grab(Path(rec["erased"]), t)
        c = grab(Path(final), t)
        if a is not None and b is not None and c is not None:
            sheet = contact_sheet([(f"original ({info['subtitle_lang']} hard-sub)", a),
                                   ("erased", b),
                                   ("final (ZH subs+dub)", c)],
                                  work / "contact_sheet.png")
            rec["contact_sheet"] = str(sheet)
    (work / "run.json").write_text(json.dumps(rec, ensure_ascii=False, indent=2),
                                    encoding="utf-8")
    return rec


# ---------------------------------------------------------------- 场景三 / 四

def frame_metrics(a: np.ndarray, b: np.ndarray, mask: np.ndarray) -> dict:
    """mask 内 / 外的 PSNR 与 SSIM（自算，零额外依赖）。"""
    from eval import metrics as M

    inside = mask > 0
    outside = ~inside
    res = {}
    for tag, sel in (("in", inside), ("out", outside)):
        if not sel.any():
            continue
        res[f"psnr_{tag}"] = M.psnr_mask(a, b, mask * sel.astype(np.uint8))
        res[f"ssim_{tag}"] = round(float(M.ssim(a[sel], b[sel])), 4)
    return res


def compare_engines(aid: str, engines: list[str], n_frames: int = 8) -> dict:
    """同一 mask 下换引擎 → 量化「修补引擎」这一个变量。

    **真实素材没有 GT**（原片自带硬字幕，没有干净母版可比），
    所以这里算的不是「擦得好不好」，而是两件仍然成立的事：
      1. 修补区内的像素统计（修补结果 vs 原片带字幕帧 —— 只能看差异结构，
         不能当质量分）；
      2. **闪烁指标**：修补后相邻帧在 mask 内的变化量 / mask 外的变化量。
         逐帧独立修补在动画/运动镜头上会「字在闪」，这个比值能把它抓出来。
    质量分的严格版本在 compare_engines_gt（有合成 GT 的派生件上做）。
    """
    src = raw_path(aid)
    rect, det = detect_region(src)
    cap = cv2.VideoCapture(str(src))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    idxs = np.linspace(0, max(total - 1, 0), min(n_frames, max(total, 1))).astype(int)
    frames = []
    for i in idxs:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(i))
        ok, fr = cap.read()
        if ok:
            frames.append((int(i), fr))
    cap.release()
    if not frames:
        return {"error": "读不到帧"}

    h, w = frames[0][1].shape[:2]
    mask = se.make_mask_rect(h, w, *rect)
    rows = []
    for eng in engines:
        fn = make_engine(eng)
        agg = []
        t0 = time.time()
        prev = None
        flick_in, flick_out = [], []
        for _, fr in frames:
            out = fn(fr, mask)
            agg.append(frame_metrics(out, fr, mask))
            if prev is not None:
                d = np.abs(out.astype(np.int16) - prev.astype(np.int16)).mean(axis=2)
                inside = mask > 0
                outside = ~inside
                if inside.any():
                    flick_in.append(float(d[inside].mean()))
                if outside.any():
                    flick_out.append(float(d[outside].mean()))
            prev = out
        per = (time.time() - t0) / len(frames)
        med = {k: round(float(np.median([a[k] for a in agg])), 3)
               for k in agg[0]}
        fi = float(np.median(flick_in)) if flick_in else None
        fo = float(np.median(flick_out)) if flick_out else None
        rows.append({"engine": eng, "ms_per_frame": round(per * 1000, 1),
                     "median": med,
                     "flicker_in": round(fi, 3) if fi is not None else None,
                     "flicker_out": round(fo, 3) if fo is not None else None,
                     "flicker_ratio": round(fi / fo, 3) if fi and fo else None,
                     "rtf_720p": round(per * 25, 2)})
        print(f"[cmp] {aid} {eng}: {med} {per*1000:.0f}ms/帧 "
              f"flicker={rows[-1]['flicker_ratio']}")
    return {"id": aid, "rect": list(rect), "detection": det["kind"],
            "frames": len(frames), "results": rows}


def compare_engines_gt(case: dict, engines: list[str], n_frames: int = 8) -> dict:
    """有合成 GT 的派生件上做**严格**对比：修补结果 vs 母版。

    derived = 母版 + 烧录字幕，母版即 GT → PSNR/SSIM 可直接算。
    这是「方案A vs 方案B」唯一有资格下结论的地方。
    """
    derived = ROOT / case["derived_file"]
    master = ROOT / case["master"]
    if not derived.exists() or not master.exists():
        return {"error": "缺派生件或母版", "id": case["case_id"]}
    is_face = case.get("difficulty_type") == "hard_sub_face"
    if is_face:
        band = se.detect_face_subtitle(derived)
        if not band.get("found"):
            return {"error": "未检出人脸字幕", "id": case["case_id"]}
    else:
        band = se.detect_subtitle_band(derived)
        if not band.get("found"):
            return {"error": "未检出字幕带", "id": case["case_id"]}

    cap = cv2.VideoCapture(str(derived))
    W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.release()
    rect = se.region_of(band, W)

    capd = cv2.VideoCapture(str(derived))
    capm = cv2.VideoCapture(str(master))
    idxs = np.linspace(0, max(total - 1, 0), min(n_frames, max(total, 1))).astype(int)
    pairs = []
    for i in idxs:
        capd.set(cv2.CAP_PROP_POS_FRAMES, int(i))
        capm.set(cv2.CAP_PROP_POS_FRAMES, int(i))
        ok1, fd = capd.read()
        ok2, fm = capm.read()
        if ok1 and ok2:
            pairs.append((fd, fm))
    capd.release()
    capm.release()
    if not pairs:
        return {"error": "读不到帧", "id": case["case_id"]}

    h, w = pairs[0][0].shape[:2]
    mask = se.make_mask_rect(h, w, *rect)
    rows = []
    for eng in engines:
        fn = make_engine(eng)
        agg, t0 = [], time.time()
        for fd, fm in pairs:
            out = fn(fd, mask)
            agg.append(frame_metrics(out, fm, mask))
        per = (time.time() - t0) / len(pairs)
        med = {k: round(float(np.median([a[k] for a in agg])), 3) for k in agg[0]}
        rows.append({"engine": eng, "ms_per_frame": round(per * 1000, 1),
                     "median": med, "rtf_720p": round(per * 25, 2)})
        print(f"[cmp-gt] {case['case_id']} {eng}: {med} {per*1000:.0f}ms/帧")
    return {"id": case["case_id"], "rect": list(rect),
            "kind": "face" if is_face else "bottom",
            "frames": len(pairs), "results": rows}


def scenario_s3(ids: list[str], out_dir: Path) -> dict:
    """内部创作者工具：方案A vs 方案B 对比表 + 破损截图。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    gt_tables, real_tables, report = [], [], []

    # 1) 有 GT 的派生件 → 严格质量对比
    cases = json.loads((ROOT / "assets" / "cases.json").read_text(encoding="utf-8"))["cases"]
    gt_cases = [c for c in cases
                if c["difficulty_type"] in ("hard_sub_bottom", "hard_sub_face")]
    for c in gt_cases:
        r = compare_engines_gt(c, ["opencv_telea", "opencv_ns", "lama_onnx"])
        if not r.get("error"):
            gt_tables.append(r)

    # 2) 真实素材 → 闪烁指标 + 破损截图
    for aid in ids:
        r = compare_engines(aid, ["opencv_telea", "lama_onnx"])
        real_tables.append(r)
        src = raw_path(aid)
        rect, _ = detect_region(src)
        cap = cv2.VideoCapture(str(src))
        cap.set(cv2.CAP_PROP_POS_FRAMES,
                max(int(cap.get(cv2.CAP_PROP_FRAME_COUNT) * 0.5), 1))
        ok, fr = cap.read()
        cap.release()
        if not ok:
            continue
        h, w = fr.shape[:2]
        mask = se.make_mask_rect(h, w, *rect)
        items = [("original", fr)]
        for eng in ("opencv_telea", "lama_onnx"):
            try:
                items.append((eng, make_engine(eng)(fr, mask)))
            except SystemExit:
                continue
        png = out_dir / f"{aid}_compare.png"
        contact_sheet(items, png,
                      crop=(max(rect[0] - 40, 0), max(rect[1] - 30, 0),
                            min(rect[2] + 40, w), min(rect[3] + 30, h)))
        report.append(f"### {aid}\n\n![{aid}]({aid}_compare.png)\n")

    md = ["# 方案A vs 方案B 对比表（内部创作者工具口径）", "",
          "隔离变量：**同一 mask、同一帧序列，只换修补引擎**。", "",
          "## 一、合成 GT 派生件（严格：修补结果 vs 母版）", "",
          "mask 内 = 字幕区（擦得干不干净，越高越好）；mask 外 = 有没有误伤（应≈99dB）。", ""]
    for t in gt_tables:
        md += [f"### {t['id']}", "",
               f"字幕区 {t['rect']}（{t['kind']}），采样 {t['frames']} 帧", "",
               "| 引擎 | ms/帧 | RTF(720p) | PSNR in | PSNR out | SSIM in | SSIM out |",
               "|---|---|---|---|---|---|---|"]
        for row in t["results"]:
            m = row["median"]
            md.append(f"| {row['engine']} | {row['ms_per_frame']} | "
                      f"{row['rtf_720p']} | {m.get('psnr_in')} | {m.get('psnr_out')} | "
                      f"{m.get('ssim_in')} | {m.get('ssim_out')} |")
        md.append("")

    md += ["## 二、真实素材（无 GT：只能看闪烁与耗时）", "",
           "flicker = 修补后相邻帧差（mask 内）/ 相邻帧差（mask 外）。",
           "比值 ≫1 说明修补内容在**逐帧乱跳**（动画/运动镜头的典型问题）。", ""]
    for t in real_tables:
        if t.get("error"):
            continue
        md += [f"### {t['id']}", "",
               f"字幕区 {t['rect']}（{t['detection']}），采样 {t['frames']} 帧", "",
               "| 引擎 | ms/帧 | flicker in | flicker out | 比值 |",
               "|---|---|---|---|---|"]
        for row in t["results"]:
            md.append(f"| {row['engine']} | {row['ms_per_frame']} | "
                      f"{row.get('flicker_in')} | {row.get('flicker_out')} | "
                      f"{row.get('flicker_ratio')} |")
        md.append("")

    (out_dir / "compare_table.md").write_text("\n".join(md), encoding="utf-8")
    (out_dir / "compare_table.json").write_text(
        json.dumps({"gt": gt_tables, "real": real_tables},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "broken_repairs.md").write_text("\n".join(report), encoding="utf-8")
    print(f"-> {out_dir/'compare_table.md'}")
    return {"gt": gt_tables, "real": real_tables}


def scenario_s4(out_dir: Path) -> dict:
    """技术选型探路：LaMa ONNX 在本机的成本/速度/质量结论 + demo。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    rec: dict = {}
    t0 = time.time()
    eng = _lama_engine()
    sess = eng.sess
    rec["model"] = str(eng.path)
    rec["model_mb"] = round(eng.path.stat().st_size / 1e6, 1)
    rec["session_load_ms"] = int((time.time() - t0) * 1000)

    # 单帧速度（三种归一化都试，结论由指标定）
    vid = raw_path("S1_02")
    cap = cv2.VideoCapture(str(vid))
    cap.set(cv2.CAP_PROP_POS_FRAMES, 5)
    ok, fr = cap.read()
    cap.release()
    if not ok:
        return {"error": "读不到帧"}
    rect, det = detect_region(vid)
    h, w = fr.shape[:2]
    mask = se.make_mask_rect(h, w, *rect)
    for norm in ("unit", "imagenet"):
        e = type(eng)(eng.path, normalize=norm)
        t1 = time.time()
        out = e.inpaint(fr, mask)
        ms = (time.time() - t1) * 1000
        m = frame_metrics(out, fr, mask)
        rec.setdefault("normalize_trials", []).append(
            {"normalize": norm, "ms": round(ms, 1), **m})
        print(f"[lama:{norm}] {ms:.0f}ms {m}")
        imwrite_unicode(out_dir / f"lama_{norm}.png", np.hstack([fr, out]))

    (out_dir / "lama_report.json").write_text(
        json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    md = ["# 方案B（LaMa ONNX）本机实测", "",
          f"- 模型：`{rec['model']}`（{rec['model_mb']}MB，Apache-2.0）",
          f"- session 加载：{rec['session_load_ms']}ms", "",
          "| 归一化 | ms/帧(720p) | PSNR in | PSNR out | SSIM in |", "|---|---|---|---|---|"]
    for r in rec.get("normalize_trials", []):
        md.append(f"| {r['normalize']} | {r['ms']} | {r.get('psnr_in')} | "
                  f"{r.get('psnr_out')} | {r.get('ssim_in')} |")
    (out_dir / "lama_report.md").write_text("\n".join(md), encoding="utf-8")
    return rec


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("scenario", choices=["s1", "s2", "s3", "s4"])
    ap.add_argument("--id", action="append")
    ap.add_argument("--ids", help="逗号分隔（s3 用）")
    ap.add_argument("--out", default=str(RESULTS))
    args = ap.parse_args()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.scenario == "s1":
        ids = args.id or ["S1_02"]
        for aid in ids:
            print(f"\n===== 场景一 赛博出海 · {aid} =====")
            scenario_s1(aid, out_dir)
    elif args.scenario == "s2":
        ids = args.id or ["S2_01"]
        for aid in ids:
            print(f"\n===== 场景二 合法搬运 · {aid} =====")
            scenario_s2(aid, out_dir)
    elif args.scenario == "s3":
        ids = (args.ids.split(",") if args.ids else ["S1_02", "S2_01"])
        scenario_s3(ids, out_dir / "s3")
    else:
        scenario_s4(out_dir / "s4")


if __name__ == "__main__":
    main()
