"""图片上传工具与路径转换。"""
from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import UploadFile

# 项目根目录（app/ 的父级）
PROJECT_ROOT = Path(__file__).resolve().parent.parent
UPLOAD_DIR = PROJECT_ROOT / "data" / "uploads"


def ensure_upload_dir(base: Path | None = None) -> Path:
    d = base or UPLOAD_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_upload(file: UploadFile, upload_dir: Path | None = None) -> str:
    """保存上传的图片，返回绝对路径（供后端读取）。"""
    d = ensure_upload_dir(upload_dir)
    suffix = Path(file.filename or "upload.jpg").suffix.lower() or ".jpg"
    if suffix not in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
        suffix = ".jpg"
    name = f"{uuid.uuid4().hex}{suffix}"
    path = d / name
    path.write_bytes(file.file.read())
    return str(path)


def to_web_path(path: str | None) -> str | None:
    """把本地绝对路径转成网页可访问的 URL 路径。

    - 位于 data/uploads/ 下 → /uploads/xxx.jpg
    - 其他（如素材/）→ 返回 None（网页无法直接访问，由前端降级处理）
    """
    if not path:
        return None
    try:
        p = Path(path).resolve()
        uploads = UPLOAD_DIR.resolve()
        if p.is_relative_to(uploads):
            return f"/uploads/{p.name}"
    except (OSError, ValueError):
        pass
    return None


def to_web_paths(paths: list[str] | None) -> list[str]:
    """批量转换，过滤掉无法访问的路径。"""
    if not paths:
        return []
    out = []
    for p in paths:
        web = to_web_path(p)
        if web:
            out.append(web)
    return out