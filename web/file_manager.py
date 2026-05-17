"""
File management — list and clear uploads/outputs manually.
"""
import shutil
from datetime import datetime
from pathlib import Path


def _safe_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _format_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def list_files(directory: Path) -> list:
    if not directory.exists():
        return []
    items = []
    for p in sorted(directory.rglob("*"), key=lambda x: x.stat().st_mtime if x.exists() else 0, reverse=True):
        if p.is_file() and p.name != ".gitkeep":
            rel = p.relative_to(directory)
            stat = p.stat()
            items.append({
                "path": str(rel).replace("\\", "/"),
                "name": p.name,
                "size": _safe_size(p),
                "size_human": _format_size(_safe_size(p)),
                "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
                "modified_ts": stat.st_mtime,
            })
    return items


def storage_summary(uploads_dir: Path, output_dir: Path) -> dict:
    u_files = list_files(uploads_dir)
    o_files = list_files(output_dir)
    return {
        "uploads": {
            "count": len(u_files),
            "total_bytes": sum(f["size"] for f in u_files),
            "total_human": _format_size(sum(f["size"] for f in u_files)),
            "files": u_files,
        },
        "outputs": {
            "count": len(o_files),
            "total_bytes": sum(f["size"] for f in o_files),
            "total_human": _format_size(sum(f["size"] for f in o_files)),
            "files": o_files,
        },
    }


def clear_directory(directory: Path, keep_gitkeep: bool = True) -> int:
    """Delete all files (and subdirs) in directory. Returns count deleted."""
    if not directory.exists():
        return 0
    count = 0
    for child in directory.iterdir():
        if keep_gitkeep and child.name == ".gitkeep":
            continue
        try:
            if child.is_file() or child.is_symlink():
                child.unlink()
                count += 1
            elif child.is_dir():
                shutil.rmtree(child)
                count += 1
        except OSError:
            pass
    return count


def delete_one(directory: Path, relative_path: str) -> bool:
    """Safely delete a single file inside `directory`. Prevents path traversal."""
    if not relative_path:
        return False
    target = (directory / relative_path).resolve()
    try:
        target.relative_to(directory.resolve())
    except ValueError:
        return False  # outside the directory
    if not target.exists() or not target.is_file():
        return False
    try:
        target.unlink()
        return True
    except OSError:
        return False
