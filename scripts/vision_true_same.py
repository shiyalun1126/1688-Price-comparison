"""真同款测试：两张蓝白格衬衫应被判为同款（验证不漏判）。"""
from __future__ import annotations

from pathlib import Path

from app.matcher.vision import compare_images

SRC = Path(__file__).resolve().parent.parent / "素材"
a = SRC / "O1CN01ByFwJQ1F5jnveHF0s_!!2208152340436-0-cib.jpg"
b = SRC / "O1CN01pCQwQK1MysorjhjGe_!!2908521504-0-cib.jpg"

print("图A:", a.name, f"({a.stat().st_size//1024}KB)")
print("图B:", b.name, f"({b.stat().st_size//1024}KB)")
print("=" * 70)

r = compare_images(str(a), str(b))
print("判定:", "✅ 同款" if r.get("same") else "❌ 不同款")
print("tier:", r.get("tier"))
print("置信度:", r.get("confidence"))
print("理由:", r.get("reason"))
if r.get("key_diff"):
    print("差异:", r.get("key_diff"))