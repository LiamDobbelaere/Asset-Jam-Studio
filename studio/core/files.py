"""Removing generated files: plain delete, or a secure delete that overwrites the bytes with zeros first.

Only paths inside one of the served roots (paths.FILE_ROOTS) are ever touched, and never a root itself.
"""
import os
import shutil

from .paths import FILE_ROOTS

CHUNK = 4 << 20   # 4 MiB of zeros, written repeatedly over the file


def inside_roots(path):
    """True when `path` lies strictly inside one of the served roots (so it is ours to delete)."""
    p = os.path.abspath(path)
    return any(p.startswith(os.path.abspath(r) + os.sep) for r in FILE_ROOTS.values())


def wipe_file(path):
    """Overwrite every byte of the file with zeros and flush it to disk. Best effort: the OS and the drive
    (SSD wear levelling, copy-on-write file systems) may keep old blocks; it still beats a plain unlink."""
    size = os.path.getsize(path)
    zeros = b"\0" * min(CHUNK, size) if size else b""
    with open(path, "r+b", buffering=0) as f:
        left = size
        while left > 0:
            n = min(len(zeros), left)
            f.write(zeros[:n])
            left -= n
        f.flush()
        os.fsync(f.fileno())
        f.truncate(0)
        f.flush()
        os.fsync(f.fileno())


def remove(path, secure=False):
    """Delete a file or a whole folder. With `secure`, every file is zeroed before it is unlinked.
    Returns the list of paths removed. Paths outside the served roots are refused (ValueError)."""
    if not inside_roots(path):
        raise ValueError(f"refusing to delete outside the asset folders: {path}")
    removed = []
    if os.path.isdir(path) and not os.path.islink(path):
        for root, dirs, files in os.walk(path, topdown=False):
            for fn in files:
                fp = os.path.join(root, fn)
                _remove_file(fp, secure)
                removed.append(fp)
        shutil.rmtree(path, ignore_errors=True)
        removed.append(path)
    elif os.path.lexists(path):
        _remove_file(path, secure)
        removed.append(path)
    return removed


def _remove_file(path, secure):
    if secure and os.path.isfile(path) and not os.path.islink(path):
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        wipe_file(path)
    os.remove(path)
