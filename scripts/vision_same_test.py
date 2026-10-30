"""同款识别测试：验证视觉模型能否识别「同一款货的不同拍法」。

重点测试 H(模特) vs I(模特) 是否为同款；G(挂拍大格) 是否被正确排除。
"""
from __future__ import annotations

from pathlib import Path

from app.matcher.vision import compare_images

SRC = Path(__file__).resolve().parent.parent / "素材"
files = {
    "A_黑白细格-挂拍": "1001bd0b244611075a0ad3e28def4f29~tplv-be4g95zd3a-448x448.jpeg",
    "C_黑灰格-模特": "jpeg_m_51340b671d15c51817f1063dd8331bca_sx_149442_www1200-1200~tplv-be4g95zd3a-448x448.jpeg",
    "G_黑白方块-挂拍": "5f65564fc0a6f46ff33b7f580356561a~tplv-be4g95zd3a-448x448.jpeg",
    "H_黑白格-男模": "dd46c5ffbd9d0ce99e1287610c854168~tplv-be4g95zd3a-448x448.jpeg",
    "I_黑白格-男模近景": "ef4e8e796f982685d17f369ddd479f30~tplv-be4g95zd3a-448x448.jpeg",
}

pairs = [
    ("H_黑白格-男模", "I_黑白格-男模近景", "【重点】疑似同款"),
    ("C_黑灰格-模特", "I_黑白格-男模近景", "【重点】疑似同款"),
    ("C_黑灰格-模特", "H_黑白格-男模", "【重点】疑似同款"),
    ("A_黑白细格-挂拍", "G_黑白方块-挂拍", "对照：不同款"),
    ("A_黑白细格-挂拍", "H_黑白格-男模", "对照：挂着 vs 穿着"),
    ("G_黑白方块-挂拍", "H_黑白格-男模", "对照：不同款"),
]

for n1, n2, label in pairs:
    try:
        r = compare_images(str(SRC / files[n1]), str(SRC / files[n2]))
        same = "✅ 同款" if r.get("same") else "❌ 不同"
        print(f"{label}")
        print(f"  {n1}  vs  {n2}")
        print(f"  判定: {same} | tier={r.get('tier')} | 置信={r.get('confidence')}")
        print(f"  理由: {r.get('reason','')}")
        if r.get("key_diff"):
            print(f"  差异: {r.get('key_diff')}")
        print()
    except Exception as e:
        print(f"{n1} vs {n2}: ERROR {e}\n")