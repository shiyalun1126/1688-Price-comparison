"""用真实服装素材校准 pHash 阈值。

观察目标：
- 同色系/同格型（A、C、D、F 黑白格纹）应得到较高相似度
- 异色（B 绿色、E 红色）应得到明显较低的相似度
"""
from __future__ import annotations

from itertools import combinations
from pathlib import Path

from app.matcher.image import hamming, phash

SRC = Path(__file__).resolve().parent.parent / "素材"
files = {
    "A_黑白细格-挂拍": "1001bd0b244611075a0ad3e28def4f29~tplv-be4g95zd3a-448x448.jpeg",
    "B_绿黑格-模特": "419fcf1e8087c63340668cb4c77b7e54~tplv-be4g95zd3a-448x448.jpeg",
    "C_黑灰格-模特": "jpeg_m_51340b671d15c51817f1063dd8331bca_sx_149442_www1200-1200~tplv-be4g95zd3a-448x448.jpeg",
    "D_黑白细格-躺拍": "jpeg_m_72907ffe91411a20e04beab7226252c5_sx_1008443_www800-800~tplv-be4g95zd3a-448x448.jpeg",
    "E_红黑格-平铺": "jpeg_m_9ff592da080cf12ad7aefa05773494bb_sx_120420_www800-800~tplv-be4g95zd3a-448x448.jpeg",
    "F_黑白大格-挂拍": "XIudEOhG_m_58c308838291322c1f614a54fe6c5104_sx_313931_www1200-1200~tplv-be4g95zd3a-448x448.jpeg",
}

hashes = {}
for name, fn in files.items():
    p = SRC / fn
    hashes[name] = phash(str(p))
    print(f"{name:20s} pHash={hashes[name]:016x}")

print("\n=== 两两汉明距离（越小越像，0=完全相同，64=完全不同）===")
print(f"{'组合':42s} {'距离':>4s} {'相似度':>6s}")
rows = []
for (n1, h1), (n2, h2) in combinations(hashes.items(), 2):
    d = hamming(h1, h2)
    sim = 1.0 - d / 64
    rows.append((d, n1, n2, sim))

rows.sort()
for d, n1, n2, sim in rows:
    print(f"{n1} vs {n2:20s} {d:4d} {sim:6.2f}")

print("\n=== 按距离排序后，最相似的 3 对 ===")
for d, n1, n2, sim in rows[:3]:
    print(f"  {n1} vs {n2}: 距离 {d}, 相似度 {sim:.2f}")