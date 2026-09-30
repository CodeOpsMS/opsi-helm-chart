#!/usr/bin/env python3
"""Verify the finished artifact in isolated Valkey containers, including restarts."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

# Public upstream server; the exact SUSE runtime is additionally accepted in-cluster.
VALKEY_IMAGE = "valkey/valkey:9.1.2@sha256:418652cfb58ef879d4978c33553735d7147016032d5aefaa14c828e611eb9dfd"
spec = importlib.util.spec_from_file_location("contract", Path(__file__).with_name("valkey-opsi-contract.py"))
contract = importlib.util.module_from_spec(spec)
spec.loader.exec_module(contract)


def docker(*args, check=True):
    result = subprocess.run(["docker", *args], check=False, text=True, capture_output=True)
    if check and result.returncode:
        raise RuntimeError(f"docker {args[0]} failed: {result.stderr.strip()}\n{result.stdout.strip()}")
    return result.stdout.strip()


def connection_args(port):
    return argparse.Namespace(host="127.0.0.1", port=port, tls=False, ca_file=None,
                              username="", password_env="VALKEY_CI_NO_PASSWORD", database=0,
                              expected_module="timeseries")


def wait_ready(name):
    port = int(docker("port", name, "6379/tcp").rsplit(":", 1)[1])
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            client = contract.RedisWire(connection_args(port))
            contract.require(client.command("PING") == b"PONG", "PING failed")
            return port, client
        except (OSError, ConnectionError, contract.ResponseError):
            if docker("inspect", "--format", "{{.State.Running}}", name) != "true":
                raise RuntimeError("Valkey stopped before readiness") from None
            time.sleep(0.5)
    raise TimeoutError("Valkey was not ready within 60 seconds")


def verify_metadata(image):
    dynamic = docker("run", "--rm", "--entrypoint", "cat", image, "/usr/share/valkey-timeseries/elf-dynamic.txt")
    libraries = set(re.findall(r"\(NEEDED\).*\[(.*?)\]", dynamic))
    allowed = {"libgcc_s.so.1", "libc.so.6", "libm.so.6", "libdl.so.2", "libpthread.so.0", "libstdc++.so.6", "ld-linux-x86-64.so.2"}
    contract.require(libraries and libraries <= allowed, f"unexpected runtime libraries: {libraries - allowed}")
    versions = docker("run", "--rm", "--entrypoint", "cat", image, "/usr/share/valkey-timeseries/elf-versions.txt")
    glibc = [tuple(map(int, item.split("."))) for item in re.findall(r"\bGLIBC_(\d+\.\d+)", versions)]
    contract.require(glibc and max(glibc) <= (2, 36), "module exceeds Bookworm's glibc 2.36 baseline")
    print(json.dumps({"needed_libraries": sorted(libraries), "maximum_glibc": ".".join(map(str, max(glibc)))}), flush=True)


def seed(client):
    for key in ("persistent:source", "persistent:minute"):
        contract.require(client.command("TS.CREATE", key, "RETENTION", 86400000,
                                        "LABELS", "scope", "persistence") == b"OK", "persistent CREATE failed")
    client.command("TS.CREATERULE", "persistent:source", "persistent:minute", "AGGREGATION", "AVG", 60000)
    for timestamp, value in ((120000, 1.5), (150000, 2.5), (180000, 4)):
        client.command("TS.ADD", "persistent:source", timestamp, value)
    verify_persisted(client)


def verify_persisted(client):
    contract.require(contract.samples(client.command("TS.RANGE", "persistent:source", "-", "+")) ==
                     [[120000, 1.5], [150000, 2.5], [180000, 4.0]], "source samples changed after persistence")
    contract.require(contract.samples(client.command("TS.RANGE", "persistent:minute", "-", "+")) ==
                     [[120000, 2.0]], "compacted samples changed after persistence")
    rules = contract.parsed_rules(client.command("TS.INFO", "persistent:source"))
    contract.require(rules["persistent:minute"]["time_bucket"] == 60000, "rule lost after persistence")
    info = client.command("TS.INFO", "persistent:source")
    fields = dict(zip(info[::2], info[1::2]))
    contract.require(fields[b"labels"] == [[b"scope", b"persistence"]], "labels lost after persistence")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image")
    args = parser.parse_args()
    os.environ.pop("VALKEY_CI_NO_PASSWORD", None)
    verify_metadata(args.image)
    docker("pull", "--platform", "linux/amd64", VALKEY_IMAGE)
    prefix = "valkey-ts-ci-" + uuid.uuid4().hex[:12]
    module_volume = prefix + "-module"
    volumes = [module_volume]
    containers = []
    try:
        docker("volume", "create", module_volume)
        # Emulate Kubernetes fsGroup on the shared EmptyDir, then use the real init command.
        docker("run", "--rm", "--user", "0:0", "-v", module_volume + ":/out",
               "--entrypoint", "sh", args.image, "-ec", "chown 1000:1000 /out")
        docker("run", "--rm", "-v", module_volume + ":/out", args.image)
        # Retried init containers must replace their own previously chmod-0555 artifact.
        docker("run", "--rm", "-v", module_volume + ":/out", args.image)
        for mode in ("rdb", "aof"):
            data_volume = prefix + "-" + mode
            volumes.append(data_volume)
            docker("volume", "create", data_volume)
            docker("run", "--rm", "--user", "0:0", "-v", data_volume + ":/data",
                   "--entrypoint", "sh", args.image, "-ec", "chown 1000:1000 /data")
            name = prefix + "-" + mode
            containers.append(name)
            server_args = ["--dir", "/data", "--save", "", "--loadmodule", "/modules/libvalkey_timeseries.so",
                           "--timeseries.ts-compatibility-mode", "strict", "--timeseries.ts-num-threads", "2",
                           "--appendonly", "yes" if mode == "aof" else "no"]
            if mode == "aof":
                # Force actual AOF command replay rather than silently testing RDB twice.
                server_args += ["--appendfsync", "always", "--aof-use-rdb-preamble", "no"]
            docker("run", "-d", "--name", name, "--platform", "linux/amd64", "--user", "1000:1000",
                   "--read-only", "-p", "127.0.0.1::6379", "-v", module_volume + ":/modules:ro",
                   "-v", data_volume + ":/data", "--entrypoint", "valkey-server", VALKEY_IMAGE, *server_args)
            port, client = wait_ready(name)
            try:
                config = client.command("CONFIG", "GET", "*compatibility*")
                contract.require(config == [b"timeseries.ts-compatibility-mode", b"strict"], "strict config prefix/value differs")
                threads = client.command("CONFIG", "GET", "timeseries.ts-num-threads")
                contract.require(threads == [b"timeseries.ts-num-threads", b"2"], "module thread limit differs")
                print(json.dumps({"module_bytes": int(docker("exec", name, "stat", "-c", "%s", "/modules/libvalkey_timeseries.so")),
                                  "compatibility_config": [item.decode() for item in config],
                                  "thread_config": [item.decode() for item in threads]}), flush=True)
                print(json.dumps({"mode": mode, "contract": contract.run(connection_args(port))}), flush=True)
                seed(client)
                if mode == "rdb":
                    contract.require(client.command("SAVE") == b"OK", "RDB SAVE failed")
                    docker("exec", name, "test", "-s", "/data/dump.rdb")
                else:
                    client.command("BGREWRITEAOF")
                    deadline = time.monotonic() + 60
                    while time.monotonic() < deadline:
                        info = client.command("INFO", "persistence").decode()
                        if "aof_rewrite_in_progress:0\r\n" in info:
                            contract.require("aof_last_bgrewrite_status:ok\r\n" in info, "AOF rewrite failed")
                            break
                        time.sleep(0.2)
                    else:
                        raise TimeoutError("AOF rewrite did not finish")
                    docker("exec", name, "sh", "-ec", "test ! -e /data/dump.rdb; test -n \"$(find /data/appendonlydir -name '*.base.aof' -size +0c)\"")
            finally:
                client.close()
            # A forced stop proves persisted content, independent of graceful shutdown saving.
            docker("kill", name)
            docker("start", name)
            port, client = wait_ready(name)
            try:
                verify_persisted(client)
                client.command("TS.ADD", "persistent:source", 240000, 8)
                contract.require(contract.samples(client.command("TS.RANGE", "persistent:minute", "-", "+")) ==
                                 [[120000, 2.0], [180000, 4.0]], "aggregation did not resume after restart")
                print(json.dumps({"mode": mode, "restart": "passed", "contract": contract.run(connection_args(port))}), flush=True)
            finally:
                client.close()
        print("Artifact init, OPSI contract, strict mode, RDB and pure AOF restarts passed", flush=True)
    except Exception:
        for name in containers:
            print(docker("logs", name, check=False), file=sys.stderr)
        raise
    finally:
        for name in containers:
            docker("rm", "-f", name, check=False)
        for volume in reversed(volumes):
            docker("volume", "rm", volume, check=False)


if __name__ == "__main__":
    main()
