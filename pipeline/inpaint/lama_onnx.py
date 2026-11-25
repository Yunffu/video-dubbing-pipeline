"""方案 B：LaMa 内容感知修补（ONNX，CPU 可跑）。

为什么是它：我们要做的是**变量隔离**的对照实验。
方案 A（OpenCV inpaint）是「边缘扩散」，LaMa 是「生成式内容感知」——
在同一张 mask 上换引擎，就能单独量出「修补引擎」这一个变量的影响，
再叠加「mask 粒度」这个变量，就知道到底该优化哪一头。

模型：Carve/LaMa-ONNX 的 lama_fp32.onnx（Apache-2.0，208MB）
  - 固定输入 512x512，image=(1,3,512,512) float，mask=(1,1,512,512) float
  - 官方说明：原 big-lama 的 ONNX 移植，FFT 单元与原实现一致

分块策略（输入尺寸固定 512）：
  - 纵向：以字幕带为中心取 512 高的窗口（带子上下都有上下文，模型才有东西可参考）
  - 横向：512 宽的瓦片，步长 448（64 重叠），只把**洞内像素**写回 → 接缝不可见
  - 窗口不足 512 时用 edge 复制补齐

归一化：模型卡没写清楚，用 `unit`(/255) 与 `imagenet` 两种都试，**由指标决定**，
不靠猜 —— 这也是评测层存在的意义。
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

TILE = 512
STRIDE = 448
MODEL_PATH = Path(__file__).resolve().parent.parent.parent / "_work" / "models" / "lama_fp32.onnx"

_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32).reshape(1, 1, 3)
_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32).reshape(1, 1, 3)


class LamaOnnx:
    """LaMa ONNX 修补器（懒加载 session）。"""

    def __init__(self, model_path: Path | None = None, normalize: str = "unit"):
        self.path = Path(model_path or MODEL_PATH)
        self.normalize = normalize
        self._sess = None

    @property
    def sess(self):
        if self._sess is None:
            if not self.path.exists():
                raise FileNotFoundError(f"缺模型：{self.path}")
            import onnxruntime as ort
            so = ort.SessionOptions()
            so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            self._sess = ort.InferenceSession(
                str(self.path), sess_options=so,
                providers=["CPUExecutionProvider"])
        return self._sess

    def available(self) -> bool:
        return self.path.exists()

    def _prep_image(self, patch_bgr: np.ndarray) -> np.ndarray:
        rgb = cv2.cvtColor(patch_bgr, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        if self.normalize == "imagenet":
            rgb = (rgb - _MEAN) / _STD
        return np.transpose(rgb, (2, 0, 1))[None, ...].astype(np.float32)

    def _post(self, out: np.ndarray) -> np.ndarray:
        x = np.transpose(out[0], (1, 2, 0))
        if self.normalize == "imagenet":
            x = x * _STD + _MEAN
        x = np.clip(x, 0.0, 1.0)
        return cv2.cvtColor((x * 255).astype(np.uint8), cv2.COLOR_RGB2BGR)

    def inpaint(self, img_bgr: np.ndarray, mask_u8: np.ndarray) -> np.ndarray:
        """img_bgr: HxWx3 uint8；mask_u8: HxW uint8（>0 = 要补的洞）。返回补好的图。"""
        h, w = img_bgr.shape[:2]
        ys, xs = np.where(mask_u8 > 0)
        if len(ys) == 0:
            return img_bgr.copy()

        # 纵向窗口：以洞的中心对齐 512 高
        cy = (int(ys.min()) + int(ys.max())) // 2
        ry0 = cy - TILE // 2
        ry1 = ry0 + TILE
        if ry0 < 0:
            ry1 -= ry0
            ry0 = 0
        if ry1 > h:
            ry0 -= (ry1 - h)
            ry1 = h
        ry0 = max(ry0, 0)
        ry1 = min(ry1, h)

        out = img_bgr.copy()
        for x0 in range(0, max(w - TILE + 1, 1), STRIDE):
            x1 = x0 + TILE
            if x1 > w:
                x0 = max(w - TILE, 0)
                x1 = w
            patch = img_bgr[ry0:ry1, x0:x1]
            pmask = mask_u8[ry0:ry1, x0:x1]
            if not pmask.any():
                continue
            ph, pw = patch.shape[:2]
            if ph < TILE or pw < TILE:
                p = np.pad(patch, ((0, TILE - ph), (0, TILE - pw), (0, 0)), mode="edge")
                m = np.pad(pmask, ((0, TILE - ph), (0, TILE - pw)), mode="constant")
            else:
                p, m = patch, pmask
            res = self.sess.run(None, {"image": self._prep_image(p),
                                       "mask": (m > 0).astype(np.float32)[None, None, ...]})[0]
            fixed = self._post(res)
            hole = pmask > 0
            out[ry0:ry1, x0:x1][hole] = fixed[:ph, :pw][hole]
        return out
