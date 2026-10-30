"""图像感知哈希（pHash）与相似度计算。

用 Pillow 纯实现，零重依赖：
- 缩放到 32×32 灰度 → DCT → 取左上 8×8 → 以中位数为阈值 → 64 位哈希。
- 汉明距离 → 相似度 0~1。

注意（服装场景核心约束）：
  图像相似仅作「召回信号」，同图 ≠ 同款。最终同款判定必须叠加属性/询价核验。
  因此这里给出的是「图相似参考分」，供 Matcher 作为召回门槛，而非最终结论。
"""
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image

_HASH_BITS = 64
_SIZE = 32      # 缩放边长
_LOW = 8        # 取 DCT 左上 8×8


def _gray_small(path: str) -> list[list[float]]:
    """载入图片 → 32×32 灰度 float 矩阵。"""
    img = Image.open(path).convert("L").resize((_SIZE, _SIZE), Image.LANCZOS)
    px = list(img.getdata())
    return [px[y * _SIZE:(y + 1) * _SIZE] for y in range(_SIZE)]


def _dct_2d(matrix: list[list[float]]) -> list[list[float]]:
    """简化 2D DCT（对 32×32 足够）。"""
    n = len(matrix)
    result = [[0.0] * n for _ in range(n)]
    for u in range(n):
        for v in range(n):
            s = 0.0
            for x in range(n):
                for y in range(n):
                    s += matrix[x][y] * math.cos((2 * x + 1) * u * math.pi / (2 * n)) \
                                       * math.cos((2 * y + 1) * v * math.pi / (2 * n))
            cu = 1.0 / math.sqrt(2) if u == 0 else 1.0
            cv = 1.0 / math.sqrt(2) if v == 0 else 1.0
            result[u][v] = cu * cv * s / 4.0
    return result


def phash(path: str) -> int:
    """返回 64 位 pHash 整数。"""
    p = Path(path)
    if not p.exists():
        return 0
    matrix = _gray_small(str(p))
    dct = _dct_2d(matrix)
    low = [dct[x][y] for x in range(_LOW) for y in range(_LOW)]
    median = sorted(low)[len(low) // 2]
    h = 0
    for i, v in enumerate(low):
        if v > median:
            h |= (1 << (_HASH_BITS - 1 - i))
    return h


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def image_similarity(path_a: str, path_b: str) -> float:
    """两张图的 pHash 相似度 0~1（1 = 极可能同一图源）。"""
    ha, hb = phash(path_a), phash(path_b)
    if not ha or not hb:
        return 0.0
    dist = hamming(ha, hb)
    # 汉明距离 0~64 → 相似度 1~0；<=8 视为高度相似（pHash 常见阈值）
    return 1.0 - dist / _HASH_BITS


def image_digest(path: str) -> str:
    """保留原名：返回 pHash 的十六进制（供上层需要确定性标识时使用）。"""
    return f"{phash(path):016x}"