"""edge-tts 语音合成后端（微软 Edge 在线 TTS，CPU、无需权重）。

为什么用它而不是 IndexTTS2：
  - IndexTTS2 是 B 站自研、效果好，但**权重与 torch-CUDA 环境本机跑不动**，且 License 是
    B 站自定义协议（>1亿 MAU 需单独授权、禁用于改进其它 AI 模型）—— 见检索台账；
  - edge-tts 纯 Python + WebSocket，几 KB 依赖，语种覆盖够用，适合做**可插拔 TTS 后端的默认实现**。

网络依赖意味着它天生会失败 —— 所以这里必须带重试 + 退避，且失败要能被上层降级接管。
"""
import asyncio
import time
from pathlib import Path

from ..config import TTSConfig
from ..media import probe as media_probe


class EdgeTTSProvider:
    name = "edge_tts"

    def __init__(self, cfg: TTSConfig):
        self.cfg = cfg

    def available(self) -> bool:
        try:
            import edge_tts  # noqa: F401
            return True
        except Exception:
            return False

    def synthesize(self, items: list, out_dir: Path) -> list:
        """批量合成。items: list[{"text","voice"}]；返回 list[dict]，每项含 path/duration。

        单段失败会重试（指数退避）；全部失败则抛异常，由 Service 降级。
        """
        import edge_tts

        out_dir.mkdir(parents=True, exist_ok=True)

        async def _run() -> list:
            out = []
            for i, item in enumerate(items):
                text = item.get("text", "")
                voice = item.get("voice", "")
                if not text.strip():
                    out.append({"index": i, "path": "", "duration": 0.0, "text": text})
                    continue
                path = out_dir / f"seg_{i:04d}.mp3"
                last_err = None
                for attempt in range(self.cfg.max_retries + 1):
                    try:
                        await asyncio.wait_for(
                            edge_tts.Communicate(text, voice).save(str(path)),
                            timeout=self.cfg.timeout)
                        dur = media_probe.probe(path).get("duration") or 0.0
                        out.append({"index": i, "path": str(path),
                                    "duration": round(float(dur), 3), "text": text})
                        break
                    except Exception as e:
                        last_err = e
                        time.sleep(1.5 * (attempt + 1))
                else:
                    raise RuntimeError(f"edge-tts 合成失败: {last_err}")
            return out

        return asyncio.run(_run())
