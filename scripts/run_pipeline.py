#!/usr/bin/env python3
"""端到端跑一条：A 语言视频 → B 语言配音成片。

用法:
    python scripts/run_pipeline.py --video <in.mp4> --tgt-lang en --out-dir _work/runs
    python scripts/run_pipeline.py --video <in.mp4> --tgt-lang en --no-tts   # 只到字幕（离线可跑）

设计原则（对齐 JD 职责 3「评估效果、耗时、成本及稳定性」）：
  - 每一步都记 耗时 / 方案 / 是否降级 / 失败原因；
  - 任何一步失败都**降级而不是崩**，降级会写进 run.json；
  - 成片 + 外挂字幕 + 逐段音频 + run.json 全部落盘 → 可复现、可回归。
"""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.asr.service import ASRService          # noqa: E402
from pipeline.compose import build_audio_track, mux   # noqa: E402
from pipeline.config import ASRConfig, TranslateConfig, TTSConfig  # noqa: E402
from pipeline.contracts import PipelineResult, StageStat  # noqa: E402
from pipeline.media import probe as media_probe       # noqa: E402
from pipeline.subtitle import build_ass, build_srt, burn_in  # noqa: E402
from pipeline.translate.registry import TranslatingService  # noqa: E402
from pipeline.tts.service import TTSService            # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def price_usd_per_mtok() -> tuple[float, float]:
    """每百万 token 的价格（美元）。价格会变，这里是可配置口径，不是权威数字。"""
    import os
    pin = float(os.environ.get("PRICE_IN_PER_MTOK", "0.27"))
    pout = float(os.environ.get("PRICE_OUT_PER_MTOK", "1.10"))
    return pin, pout


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--src-lang", default="zh")
    ap.add_argument("--tgt-lang", default="en")
    ap.add_argument("--out-dir", default="_work/runs")
    ap.add_argument("--work-dir", default=None, help="中间产物目录，默认 <out-dir>/<name>.work")
    ap.add_argument("--no-tts", action="store_true", help="只出字幕，不配音")
    ap.add_argument("--burn-subtitles", action="store_true", help="把字幕烧录进画面")
    ap.add_argument("--json", help="run.json 输出路径")
    args = ap.parse_args()

    video = Path(args.video)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = video.stem
    work = Path(args.work_dir) if args.work_dir else out_dir / f"{stem}.work"
    work.mkdir(parents=True, exist_ok=True)

    res = PipelineResult()
    t_start = time.time()

    # 0) 探测（坏素材在这里就会暴露：ffprobe 失败 → 兜底解析）
    info = media_probe.probe(video)
    res.stages.append(StageStat(name="probe", provider="ffprobe/ffmpeg",
                                 elapsed_ms=0, extra=info))
    if info.get("error"):
        print(f"[probe] 警告: {info['error']}")
    if not info.get("has_video"):
        print("[probe] 该文件没有视频流，终止")
        sys.exit(2)
    duration = info.get("duration") or 0.0
    print(f"[probe] {info.get('width')}x{info.get('height')} @ {info.get('fps')}fps "
          f"{duration:.1f}s  音轨={info.get('has_audio')}  来源={info.get('source')}")

    # 1) ASR
    asr_svc = ASRService(ASRConfig())
    print(f"[asr] 后端: {asr_svc.describe()['providers']}")
    asr = asr_svc.transcribe(str(video), args.src_lang if args.src_lang != "auto" else None)
    res.stages.append(asr.stat)
    print(f"[asr] {len(asr.segments)} 段, 耗时 {asr.stat.elapsed_ms}ms, "
          f"语种={asr.language}, 降级={asr.stat.degraded}")
    if not asr.segments:
        print("[asr] 没有识别到任何语音，终止")
        sys.exit(3)

    # 2) 翻译
    tr_svc = TranslatingService(TranslateConfig())
    print(f"[translate] 后端: {tr_svc.describe()['providers']}")
    tr = tr_svc.translate(asr.segments, asr.language, args.tgt_lang)
    res.stages.append(tr.stat)
    for w in tr.warnings[:5]:
        print(f"[translate] 警告: {w}")
    print(f"[translate] {len(tr.segments)} 段, 耗时 {tr.stat.elapsed_ms}ms, "
          f"降级={tr.stat.degraded}, token={tr.stat.extra.get('total_tokens')}")

    # 3) 字幕（外挂 + 可选烧录）
    ass_path = build_ass(tr.segments, work / "subtitles.ass",
                         width=info.get("width") or 1280, height=info.get("height") or 720)
    build_srt(tr.segments, work / "subtitles.srt")

    # 4) TTS
    tts_stat = None
    tts_segments = []
    if not args.no_tts:
        tts_svc = TTSService(TTSConfig())
        print(f"[tts] 后端: {tts_svc.describe()['providers']}")
        tts = tts_svc.synthesize(tr.segments, args.tgt_lang, work)
        res.stages.append(tts.stat)
        tts_stat = tts.stat
        tts_segments = tts.segments
        print(f"[tts] {len(tts_segments)} 段, 耗时 {tts.stat.elapsed_ms}ms, "
              f"降级={tts.stat.degraded}")

    # 5) 音轨回贴 + 合成
    out_video = out_dir / f"{stem}.dub.{args.tgt_lang}.mp4"
    mix_info = {"ok": False, "error": "未执行（--no-tts）", "segments": [], "stats": {}}
    if tts_segments:
        wav = work / "dub_track.wav"
        mix_info = build_audio_track(tts_segments, wav, total_duration=duration)
        res.stages.append(StageStat(name="compose_audio", provider="ffmpeg",
                                     elapsed_ms=0,
                                     degraded=not mix_info["ok"],
                                     error=mix_info.get("error", ""),
                                     extra={"stats": mix_info.get("stats", {}),
                                            "segments": mix_info.get("segments", [])[:50]}))
        print(f"[compose] 变速 {mix_info.get('stats')}")
        if mix_info["ok"]:
            ok, err = mux(video, wav, out_video)
            res.stages.append(StageStat(name="mux", provider="ffmpeg",
                                         elapsed_ms=0, degraded=not ok, error=err))
            if not ok:
                print(f"[mux] 失败: {err}")

    # 6) 烧录字幕（可选）
    if args.burn_subtitles and out_video.exists():
        burned = out_dir / f"{stem}.dub.{args.tgt_lang}.sub.mp4"
        ok, err = burn_in(out_video, ass_path, burned)
        res.stages.append(StageStat(name="burn_subtitles", provider="ffmpeg/libass",
                                     elapsed_ms=0, degraded=not ok, error=err))
        if ok:
            out_video = burned
            print(f"[subtitle] 已烧录 -> {burned}")
        else:
            print(f"[subtitle] 烧录失败（降级：只出外挂字幕）: {err}")

    # 7) 成本与总耗时
    pin, pout = price_usd_per_mtok()
    tin = tr.stat.extra.get("prompt_tokens") or 0
    tout = tr.stat.extra.get("completion_tokens") or 0
    cost_usd = (tin * pin + tout * pout) / 1_000_000
    total_ms = int((time.time() - t_start) * 1000)
    res.output_video = str(out_video)
    res.meta = {
        "source_video": str(video),
        "source_info": info,
        "src_lang": args.src_lang, "tgt_lang": args.tgt_lang,
        "segments": len(tr.segments),
        "work_dir": str(work),
        "cost": {
            "prompt_tokens": tin, "completion_tokens": tout,
            "price_in_per_mtok": pin, "price_out_per_mtok": pout,
            "translate_cost_usd": round(cost_usd, 6),
            "tts_chars": sum(len(s.text) for s in tr.segments),
            "wall_ms": total_ms,
            "cost_per_min_audio_usd": round(cost_usd / max(duration / 60, 1e-6), 6),
        },
        "mix": mix_info.get("stats", {}),
    }

    print("\n" + res.to_table())
    print(f"\n成片: {out_video}")
    print(f"耗时 {total_ms}ms  翻译成本 ${cost_usd:.6f}  "
          f"(token {tin}+{tout})  字幕: {ass_path}")

    out_json = Path(args.json) if args.json else work / "run.json"
    out_json.write_text(json.dumps(res.to_dict(), ensure_ascii=False, indent=2),
                        encoding="utf-8")
    print(f"-> {out_json}")


if __name__ == "__main__":
    main()
