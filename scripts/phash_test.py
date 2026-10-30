"""pHash 正确性自测：生成测试图，验证相似度行为。"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from app.matcher.image import hamming, image_similarity, phash

DATA = Path(__file__).resolve().parent.parent / "data" / "test_images"
DATA.mkdir(parents=True, exist_ok=True)


def make(path: Path, kind: str) -> None:
    img = Image.new("L", (256, 256), 255)
    d = ImageDraw.Draw(img)
    if kind == "a":
        d.rectangle([40, 40, 216, 216], fill=30)
        d.ellipse([80, 80, 176, 176], fill=160)
    else:
        d.ellipse([40, 40, 216, 216], fill=30)
        d.rectangle([80, 80, 176, 176], fill=160)
    img.save(path)


a1 = DATA / "a.png"
a2 = DATA / "a_resized.png"   # 同图不同尺寸
a3 = DATA / "a_watermark.png"  # 同图 + 水印（模拟 1688 水印差异）
b = DATA / "b.png"            # 不同图

make(a1, "a")
make(a2, "a"); Image.open(a2).resize((180, 180)).save(a2)
make(a3, "a")
img = Image.open(a3).convert("L")
d = ImageDraw.Draw(img)
d.text((10, 10), "1688", fill=0)
img.save(a3)
make(b, "b")

h1 = phash(str(a1))
print("a 的 pHash:", f"{h1:064b}")
print("同图不同尺寸  相似度:", round(image_similarity(str(a1), str(a2)), 3))
print("同图加水印    相似度:", round(image_similarity(str(a1), str(a3)), 3))
print("完全不同图    相似度:", round(image_similarity(str(a1), str(b)), 3))
print("自比          相似度:", round(image_similarity(str(a1), str(a1)), 3))
print("OK")