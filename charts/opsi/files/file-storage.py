"""Validate prepared file storage and optionally align local OPSI service IDs.

This intentionally leaves native opsi-set-rights enabled. Identity, private
keys and TFTP use their original volumes, outside the mapped file export.
"""
import grp
import json
import os
from pathlib import Path
import pwd
import shutil
import stat
import subprocess
import tempfile
import tomllib

DIRECTORIES = ("depot", "repository", "workbench")
MARKER = ".opsi-file-storage.json"


def validate_marker(root, host_id):
    path = root / MARKER
    if path.is_symlink() or not path.is_file():
        raise RuntimeError("File storage has no preparation marker; see docs/storage.md")
    marker = json.loads(path.read_text())
    if marker.get("version") != 1 or marker.get("hostId") != host_id:
        raise RuntimeError("File storage belongs to another server or has an unsupported marker")
    for name in DIRECTORIES:
        path = root / name
        if path.is_symlink() or not path.is_dir():
            raise RuntimeError(f"File storage directory {name} must be a prepared real directory")


def validate_ids(uid, gid):
    if uid is None and gid is None:
        return
    if any(type(value) is not int or value < 1 or value > 2147483647 for value in (uid, gid)):
        raise RuntimeError("Both mapped IDs must be positive non-root integers")
    for lookup, value, expected, attribute in (
        (pwd.getpwuid, uid, "opsiconfd", "pw_name"),
        (grp.getgrgid, gid, "opsifileadmins", "gr_name"),
    ):
        try:
            entry = lookup(value)
        except KeyError:
            continue
        if getattr(entry, attribute) != expected:
            raise RuntimeError(f"Mapped ID {value} is already assigned to another local identity")


def align_ids(uid, gid):
    validate_ids(uid, gid)
    if uid is None:
        return
    # usermod may update home ownership: perform this before upstream symlinks
    # the image homes to persistent storage. Never recurse over the NFS mount.
    if any(Path(path).is_symlink() for path in ("/var/lib/opsi", "/var/lib/opsiconfd")):
        raise RuntimeError("Mapped IDs must be configured before init_volumes")
    old_gid = grp.getgrnam("opsifileadmins").gr_gid
    members = [user.pw_name for user in pwd.getpwall() if user.pw_gid == old_gid]
    if set(members) - {"opsiconfd", "pcpatch"}:
        raise RuntimeError("Unexpected primary users of opsifileadmins; refusing automatic remapping")
    if old_gid != gid:
        subprocess.run(["groupmod", "-g", str(gid), "opsifileadmins"], check=True)
        for user in members:
            subprocess.run(["usermod", "-g", str(gid), user], check=True)
    if pwd.getpwnam("opsiconfd").pw_uid != uid:
        subprocess.run(["usermod", "-u", str(uid), "opsiconfd"], check=True)
    if (pwd.getpwnam("opsiconfd").pw_uid, grp.getgrnam("opsifileadmins").gr_gid) != (uid, gid):
        raise RuntimeError("Local OPSI IDs do not match configured mapped IDs")


def probe(path, uid, gid):
    """Only touch our own scratch directory, as the actual service identity."""
    directory = Path(tempfile.mkdtemp(prefix=".opsi-access-", dir=path))
    try:
        directory.chmod(0o2770)
        if directory.stat().st_mode & 0o7777 != 0o2770:
            raise RuntimeError("File export does not preserve the OPSI setgid directory mode")
        target = directory / "probe"
        with target.open("xb") as stream:
            stream.write(b"opsi file storage check\n")
            stream.flush()
            os.fsync(stream.fileno())
        if (target.stat().st_uid, target.stat().st_gid) != (uid, gid):
            raise RuntimeError("File export ownership differs from the OPSI service IDs")
        target.chmod(0o660)
        # Package checksum/list generation also uses unconditional group-only chown.
        os.chown(target, -1, gid)
        if target.stat().st_mode & 0o777 != 0o660:
            raise RuntimeError("File export does not preserve OPSI file permissions")
        os.replace(target, directory / "renamed")
        executable = directory / "executable"
        executable.write_text("#!/bin/sh\nexit 0\n")
        executable.chmod(0o770)
        subprocess.run([str(executable)], check=True)
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        shutil.rmtree(directory)


def raise_walk_error(error):
    raise error


def validate_tree(path, uid, gid):
    """Mapped exports cannot repair pre-existing foreign inode owners."""
    for parent, dirs, files in os.walk(path, followlinks=False, onerror=raise_walk_error):
        for entry in [Path(parent)] + [Path(parent) / name for name in dirs + files]:
            info = entry.lstat()
            if stat.S_ISLNK(info.st_mode):
                continue  # OPSI's depot directory rule leaves links untouched.
            if (info.st_uid, info.st_gid) != (uid, gid):
                raise RuntimeError("Prepared file tree contains a foreign owner; recopy it before activation")


def main():
    config = json.loads(Path("/opt/opsi-chart/config.json").read_text())
    storage = config["fileStorage"]
    validate_marker(Path("/opt/opsi-file-storage"), config["server"]["hostId"])
    opsi_config = Path("/data/etc/opsi.conf")
    if opsi_config.exists():
        groups = tomllib.loads(opsi_config.read_text()).get("groups", {})
        if groups.get("fileadmingroup", "opsifileadmins") != "opsifileadmins":
            raise RuntimeError("File storage currently requires the standard opsifileadmins group")
    align_ids(storage.get("mappedUid"), storage.get("mappedGid"))
    user = pwd.getpwnam("opsiconfd")
    gid = grp.getgrnam("opsifileadmins").gr_gid
    if storage.get("mappedUid") is not None:
        for name in DIRECTORIES:
            validate_tree(Path("/data/lib") / name, user.pw_uid, gid)
    pid = os.fork()
    if pid == 0:
        try:
            os.setgroups(os.getgrouplist(user.pw_name, user.pw_gid))
            os.setgid(gid)
            os.setuid(user.pw_uid)
            for name in DIRECTORIES:
                probe(Path("/data/lib") / name, user.pw_uid, gid)
        except Exception as error:
            print(f"OPSI file storage preflight failed: {error}", flush=True)
            os._exit(1)
        os._exit(0)
    _, status = os.waitpid(pid, 0)
    if os.waitstatus_to_exitcode(status) != 0:
        raise RuntimeError("File storage service-user preflight failed")
    print("Prepared OPSI file storage verified; native permission checks remain enabled", flush=True)


if __name__ == "__main__":
    main()
