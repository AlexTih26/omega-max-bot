"""Загрузка документов в docs/."""

from __future__ import annotations

import re
import time
from pathlib import Path

DOCS_ROOT = Path(__file__).resolve().parent.parent / "docs"
ALLOWED_FOLDERS = frozenset({"incoming", "reports", "registry"})
ALLOWED_EXT = frozenset({".pdf", ".xlsx", ".xls", ".png", ".jpg", ".jpeg", ".csv", ".doc", ".docx"})
MAX_BYTES = 15 * 1024 * 1024


def _safe_name(name: str) -> str:
    base = Path(name).name
    base = re.sub(r"[^\w\s.\-()А-Яа-яЁё]", "_", base, flags=re.UNICODE)
    base = base.strip("._") or "file"
    return base[:180]


def save_upload(folder: str, filename: str, data: bytes) -> dict:
    if folder not in ALLOWED_FOLDERS:
        raise ValueError("invalid folder")
    if len(data) > MAX_BYTES:
        raise ValueError("file too large (max 15 MB)")
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXT:
        raise ValueError(f"unsupported type: {ext or 'unknown'}")
    safe = _safe_name(filename)
    target_dir = DOCS_ROOT / folder
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / safe
    if path.exists():
        stem = path.stem
        path = target_dir / f"{stem}_{int(time.time())}{ext}"
    path.write_bytes(data)
    return {
        "folder": folder,
        "name": path.name,
        "path": str(path.relative_to(DOCS_ROOT.parent)),
        "size": len(data),
    }


def list_documents() -> list[dict]:
    items: list[dict] = []
    for folder in sorted(ALLOWED_FOLDERS):
        dir_path = DOCS_ROOT / folder
        if not dir_path.is_dir():
            continue
        for f in sorted(dir_path.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            if not f.is_file() or f.name.startswith("."):
                continue
            st = f.stat()
            items.append(
                {
                    "folder": folder,
                    "name": f.name,
                    "size": st.st_size,
                    "modified": st.st_mtime,
                }
            )
    return items
