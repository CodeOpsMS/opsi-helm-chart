"""Seed/upgrade a TFTP PVC without replacing administrator or client files.

Only files previously recorded as unmodified image files may be replaced.
Conflicts stop before any writes; removed image files are retained deliberately.
The source /tftpboot is never hidden by the target PVC in the init container.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

MANIFEST = ".opsi-chart-image-files.json"


def signature(path):
    if path.is_symlink():
        return "link:" + os.readlink(path)
    if not path.exists():
        return None
    if not path.is_file():
        return "directory"
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "file:" + digest.hexdigest()


def source_files(source):
    for root, dirs, files in os.walk(source, followlinks=False):
        for name in sorted(dirs + files):
            path = Path(root) / name
            if path.is_file() or path.is_symlink():
                yield path.relative_to(source).as_posix(), path


def sync(source, target):
    if not source.is_dir() or not any(source.iterdir()):
        raise RuntimeError("The image has no /tftpboot files")
    target.mkdir(parents=True, exist_ok=True)
    manifest = target / MANIFEST
    if manifest.is_symlink():
        raise RuntimeError("TFTP manifest must not be a symlink")
    old = json.loads(manifest.read_text()) if manifest.exists() else {}
    next_manifest = dict(old)
    changes = []
    conflicts = []
    for relative, src in source_files(source):
        dst = target / relative
        for ancestor in dst.parents:
            if ancestor == target:
                break
            if ancestor.is_symlink() or (ancestor.exists() and not ancestor.is_dir()):
                raise RuntimeError(f"Unsafe destination ancestor: {ancestor.relative_to(target)}")
        new_hash = signature(src)
        current_hash = signature(dst)
        if current_hash == new_hash:
            next_manifest[relative] = new_hash
        elif current_hash is None or current_hash == old.get(relative):
            changes.append((src, dst))
            next_manifest[relative] = new_hash
        else:
            conflicts.append(relative)
    if conflicts:
        raise RuntimeError("TFTP image files have local changes; preserve/reconcile them before retrying: " + ", ".join(conflicts))
    # Preserve empty runtime directories as well, without following links.
    for root, dirs, _ in os.walk(source, followlinks=False):
        for name in dirs:
            src = Path(root) / name
            if src.is_symlink():
                continue
            dst = target / src.relative_to(source)
            if dst.is_symlink() or (dst.exists() and not dst.is_dir()):
                raise RuntimeError(f"Unsafe TFTP destination directory: {src.relative_to(source)}")
            if not dst.exists():
                dst.mkdir(parents=True, mode=src.stat().st_mode & 0o777)
                if os.geteuid() == 0:
                    os.chown(dst, src.stat().st_uid, src.stat().st_gid)
    for src, dst in changes:
        dst.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=dst.parent, prefix=".opsi-chart-", delete=False) as handle:
            temp = Path(handle.name)
        try:
            if src.is_symlink():
                temp.unlink()
                temp.symlink_to(os.readlink(src))
            else:
                shutil.copy2(src, temp)
            # Retain package ownership; files must remain readable by tftpd.
            info = src.lstat()
            try:
                os.chown(temp, info.st_uid, info.st_gid, follow_symlinks=False)
            except PermissionError:
                if os.geteuid() == 0:
                    raise
            os.replace(temp, dst)
        finally:
            if temp.exists() or temp.is_symlink():
                temp.unlink()
    with tempfile.NamedTemporaryFile(mode="w", dir=target, prefix=".opsi-manifest-", delete=False) as handle:
        temp = Path(handle.name)
        try:
            json.dump(next_manifest, handle, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
            os.chmod(temp, 0o600)
            os.replace(temp, manifest)
        finally:
            temp.unlink(missing_ok=True)
    print(f"TFTP image synchronization complete: {len(changes)} changes; custom files preserved")


if __name__ == "__main__":
    try:
        sync(Path(sys.argv[1]), Path(sys.argv[2]))
    except Exception as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
