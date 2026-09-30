#!/usr/bin/env python3
"""Copy the three stopped OPSI file trees into a new, verified storage target.

Run in a maintenance Pod: source PVC read-only, new destination writable.
Ownership is deliberately not copied; mapped UID/GID must match the export.
Identity/configuration, credentials and TFTP are never selected by this tool.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile

DIRECTORIES = ("depot", "repository", "workbench")


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def raise_walk_error(error):
    raise error


def inventory(root):
    result = {}
    for parent, dirs, files in os.walk(root, followlinks=False, onerror=raise_walk_error):
        for path in [Path(parent)] + [Path(parent) / name for name in dirs + files]:
            info = path.lstat()
            name = str(path.relative_to(root))
            if stat.S_ISLNK(info.st_mode):
                result[name] = ["link", os.readlink(path)]
            elif stat.S_ISDIR(info.st_mode):
                result[name] = ["directory", stat.S_IMODE(info.st_mode)]
            elif stat.S_ISREG(info.st_mode):
                result[name] = ["file", stat.S_IMODE(info.st_mode), info.st_size, digest(path)]
            else:
                raise RuntimeError("Source contains an unsupported special file")
    return result


def prepare(source, target, host_id, uid, gid):
    if target.exists() or target.is_symlink():
        raise RuntimeError("Destination already exists; refusing to overwrite or merge")
    if target.parent.resolve() != target.parent.absolute():
        raise RuntimeError("Destination parent must not contain symlinks")
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".opsi-files-copy-", dir=target.parent))
    result = {}
    try:
        for name in DIRECTORIES:
            original = source / name
            if original.is_symlink() or not original.is_dir():
                raise RuntimeError(f"Source {name} must be a real directory")
            before = inventory(original)
            shutil.copytree(original, stage / name, symlinks=True)
            if before != inventory(stage / name) or before != inventory(original):
                raise RuntimeError(f"Source changed or verification failed for {name}")
            for parent, dirs, files in os.walk(stage / name, followlinks=False, onerror=raise_walk_error):
                for path in [Path(parent)] + [Path(parent) / entry for entry in dirs + files]:
                    info = path.lstat()
                    if (info.st_uid, info.st_gid) != (uid, gid):
                        raise RuntimeError("Copied file ownership does not match the expected export mapping")
                    if stat.S_ISREG(info.st_mode):
                        with path.open('rb') as stream:
                            os.fsync(stream.fileno())
                descriptor = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            result[name] = {"entries": len(before), "verified": True}
        marker = {"version": 1, "hostId": host_id, "preparedUid": uid, "preparedGid": gid}
        with (stage / '.opsi-file-storage.json').open('x') as stream:
            json.dump(marker, stream)
            stream.flush()
            os.fsync(stream.fileno())
        stage.chmod(0o2770)
        os.rename(stage, target)
        descriptor = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--target', type=Path, required=True)
    parser.add_argument('--host-id', required=True)
    parser.add_argument('--uid', type=int, required=True)
    parser.add_argument('--gid', type=int, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.source, args.target, args.host_id, args.uid, args.gid), indent=2))
