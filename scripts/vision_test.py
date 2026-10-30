"""用真实服装图验证多模态视觉比对效果。

设定一个基准款 A（黑白细格挂拍），与其余 5 张逐一比对，
看模型能否正确地区分「同款」与「不同款」。
"""
from __future__ import annotations

import json
from pathlib import Path

from app.matcher.vision import compare_images

SRC = Path(__file__).resolve().parent.parent / "素材"
files = {
    "A_黑白细格-挂拍": "1001bd0b244611075a0ad3e28def4f29~tplv-be4g95zd3a-448x448.jpeg",
    "B_绿黑格-模特": "419fcf1e8087c63340668cb4c77b7e54~tplv-be4g95zd3a-448x448.jpeg",
    "C_黑灰格-模特": "jpeg_m_51340b671d15c51817f1063dd8331bca_sx_149442_www1200-1200~tplv-be4g95zd3a-448x448.jpeg",
    "D_黑白细格-躺拍": "jpeg_m_72907ffe91411a20e04beab7226252c5_sx_1008443_www800-800~tplv-be4g95zd3a-448x448.jpeg",
    "E_红黑格-平铺": "jpeg_m_9ff592da080cf12ad7aefa05773494bb_sx_120420_www800-800~tplv-be4g95zd3a-448x448.jpeg",
    "F_黑白大格-挂拍": "XIudEOhG_m_58c308838291322c1f614a54fe6c5104_sx_313931_www1200-1200~tplv-be4g95zd3a-448x448.jpeg",
}

base_name = "A_黑白细格-挂拍"
base_path = SRC / files[base_name]

print(f"基准款：{base_name}\n")
print(f"{'候选':20s} {'同款':4s} {'tier':10s} {'置信':5s} 理由")
print("-" * 100)

for name, fn in files.items():
    if name == base_name:
        continue
    try:
        r = compare_images(str(base_path), str(SRC / fn))
        same = "✅" if r.get("same") else "❌"
        print(f"{name:20s} {same:4s} {r.get('tier',''):10s} {r.get('confidence',''):5} {r.get('reason','')}")
        if r.get("key_diff"):
            print(f"{'':33s}差异: {r.get('key_diff')}")
    except Exception as e:
        print(f"{name:20s} ERROR: {e}")
    print()