"""验证 BuddySwitch 网关下的视觉匹配链路。"""
from __future__ import annotations

import time
from pathlib import Path

from app.matcher.vision import VISION_MODEL, compare_images

SRC = Path(__file__).resolve().parent.parent.parent / "素材"

A = SRC / "O1CN01ByFwJQ1F5jnveHF0s_!!2208152340436-0-cib.jpg"   # 已知同款
B = SRC / "O1CN01pCQwQK1MysorjhjGe_!!2908521504-0-cib.jpg"     # 已知同款
C = SRC / "1001bd0b244611075a0ad3e28def4f29~tplv-be4g95zd3a-448x448.jpeg"  # 黑白细格挂拍（不同款）

print("视觉模型:", VISION_MODEL)
print("=" * 60)

for p in (A, B, C):
    print(f"{'存在' if p.exists() else '缺失'}  {p.name[:50]}")
print("=" * 60)

# 用例1：同款（应判 same=True）
print("\n【用例1】两张已知同款图 → 期望判为同款")
t0 = time.time()
try:
    r = compare_images(str(A), str(B))
    print(f"  耗时 {time.time()-t0:.1f}s")
    print(f"  same={r.get('same')} tier={r.get('tier')} conf={r.get('confidence')}")
    print(f"  理由: {r.get('reason','')[:120]}")
except Exception as e:
    print(f"  ❌ 失败: {type(e).__name__}: {str(e)[:200]}")

# 用例2：不同款（应判 same=False）
print("\n【用例2】同款图 vs 黑白细格挂拍 → 期望判为不同款")
t0 = time.time()
try:
    r = compare_images(str(A), str(C))
    print(f"  耗时 {time.time()-t0:.1f}s")
    print(f"  same={r.get('same')} tier={r.get('tier')} conf={r.get('confidence')}")
    print(f"  理由: {r.get('reason','')[:120]}")
except Exception as e:
    print(f"  ❌ 失败: {type(e).__name__}: {str(e)[:200]}")