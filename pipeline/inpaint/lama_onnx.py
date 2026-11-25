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
MERGE_GAP = 24      # 行段间空隙 ≤ 这个行数算同一行文字
MAX_GROUPS = 3      # 最多开几个纵向窗口（多行字幕一般 1–2 个）
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
        """输出后处理。

        **关键**：这个 ONNX 图的输出**已经是 0–255**（不是 0–1）。
        v1 按「输出 0–1」处理 → clip 到 [0,1] 再乘 255 → 全图饱和成白色 →
        表现为「LaMa 填出一块亮斑」，一度以为是模型在大洞上的失败，
        实际是自己的后处理错了。
        判定依据（不靠猜）：拿**非洞区**跟原图比 —— 正确的后处理下应当逐像素一致
        （PSNR=99），错误的会整片偏色。
        """
        x = np.transpose(out[0], (1, 2, 0))
        if self.normalize == "imagenet":
            x = x * _STD + _MEAN
        x = np.clip(x, 0.0, 255.0)
        return cv2.cvtColor(x.astype(np.uint8), cv2.COLOR_RGB2BGR)

    def _row_runs(self, mask_u8: np.ndarray) -> list[tuple[int, int]]:
        """mask 中「有像素的行」的连续段 → 合并成「文本行组」。

        为什么不能直接用 ys.min()/ys.max()：
        真实字幕 mask 常带**重编码噪点**（GT 差分里散落的孤立像素），
        min/max 会被噪点拉到全画面 → 512 高的窗口算出来就**对不上真正的字幕行**，
        结果每个瓦片里都没有洞 → 模型什么都没补 → 输出与输入逐像素相同。
        这个 bug 真实发生过：M1_01 上表现为「LaMa 在逐文字 mask 上完全失效」。
        （见 docs/notes/09-抗压与失败归类.md §3.4）

        合并规则（两轮）：
          1. 相邻行段间空隙 ≤ MERGE_GAP 行 → 视为同一行文字（一个字一行会断成多段）；
          2. 合并后按像素数排序，只保留**像素数 ≥ 最大组的 20%** 的组，最多 MAX_GROUPS 个。
             —— 这一步把重编码噪点组（像素数极少）剔掉，否则每个噪点组都会开一个
             512 高的窗口，推理量翻十几倍（实测 M1_01 从 20 分钟跑到 1.5 小时）。
        """
        row_has = (mask_u8 > 0).any(axis=1)
        runs: list[tuple[int, int]] = []
        k = 0
        n = len(row_has)
        while k < n:
            if not row_has[k]:
                k += 1
                continue
            s = k
            while k < n and row_has[k]:
                k += 1
            runs.append((s, k - 1))
        if not runs:
            return []

        # 第 1 轮：合并相近的行段
        merged: list[list[int]] = []
        for s, e in runs:
            if merged and s - merged[-1][1] - 1 <= MERGE_GAP:
                merged[-1][1] = e
            else:
                merged.append([s, e])

        # 第 2 轮：按像素数筛掉噪点组
        scored = sorted(
            ((int((mask_u8[s:e + 1] > 0).sum()), s, e) for s, e in merged),
            reverse=True)
        if not scored:
            return []
        top = scored[0][0]
        keep = [(s, e) for px, s, e in scored if px >= top * 0.2][:MAX_GROUPS]
        return sorted(keep)

    def inpaint(self, img_bgr: np.ndarray, mask_u8: np.ndarray) -> np.ndarray:
        """img_bgr: HxWx3 uint8；mask_u8: HxW uint8（>0 = 要补的洞）。返回补好的图。"""
        h, w = img_bgr.shape[:2]
        if not (mask_u8 > 0).any():
            return img_bgr.copy()

        out = img_bgr.copy()
        # 每个「有效行段」单独开一个 512 高的窗口（多行字幕 → 多个窗口）
        for rs, re in self._row_runs(mask_u8):
            cy = (rs + re) // 2
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
            if ry1 <= ry0:
                continue

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
