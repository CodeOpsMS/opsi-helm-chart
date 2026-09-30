#!/usr/bin/env python3
"""Exercise the TimeSeries wire contract consumed by opsiconfd 4.3.56.11.

Uses only Python's standard library. Passwords come from REDIS_PASSWORD (or the
environment variable named with --password-env), never command-line arguments.
Creates UUID-scoped keys, assigns a ten-minute expiry, and deletes only those
keys in finally. Does not change server configuration or existing application
keys. Run against the isolated acceptance instance.

Source contracts (tag 4.3.56.11 at github.com/opsi-org/opsiconfd):
  opsiconfd/metrics/statistics.py: CREATE duplicate error, INFO rule shape,
    CREATERULE and DELETERULE during initial setup and subsequent restarts.
  opsiconfd/metrics/collector.py: ADD RETENTION ON_DUPLICATE SUM LABELS.
  opsiconfd/application/metrics.py: RANGE AGGREGATION avg numeric result rows.
  opsiconfd/check/redis.py: INFO reports module named 'timeseries'.
  opsiconfd/redis.py: DUMP/RESTORE within one module implementation.

Also tests TS.MADD for semantic RedisTimeSeries-to-Valkey export/import.
The unused INCRBY helper branch is intentionally not an acceptance requirement:
the active collector always requests ADD, and that helper's timestamp syntax
does not follow the documented INCRBY interface.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import socket
import ssl
import sys
import time
import uuid


class ResponseError(Exception):
    pass


class RedisWire:
    """Small binary-safe RESP2 client, matching OPSI's raw bytes responses."""

    def __init__(self, args):
        self.sock = socket.create_connection((args.host, args.port), timeout=10)
        if args.tls:
            context = ssl.create_default_context(cafile=args.ca_file)
            self.sock = context.wrap_socket(self.sock, server_hostname=args.host)
        self.reader = self.sock.makefile("rb")
        password = os.environ.get(args.password_env)
        if password:
            auth = ("AUTH", args.username, password) if args.username else ("AUTH", password)
            try:
                self.command(*auth)
            except ResponseError:
                raise ResponseError("authentication failed") from None
        self.command("SELECT", args.database)

    def close(self):
        self.reader.close()
        self.sock.close()

    def command(self, *args):
        parts = [arg if isinstance(arg, bytes) else str(arg).encode() for arg in args]
        self.sock.sendall(b"*%d\r\n" % len(parts) + b"".join(
            b"$%d\r\n" % len(part) + part + b"\r\n" for part in parts))
        return self.read_response()

    def read_response(self):
        line = self.reader.readline()
        if not line.endswith(b"\r\n"):
            raise ConnectionError("incomplete RESP reply")
        kind, value = line[:1], line[1:-2]
        if kind == b"+":
            return value
        if kind == b"-":
            # redis-py removes the generic ERR prefix before ResponseError.
            raise ResponseError(value.decode().removeprefix("ERR "))
        if kind == b":":
            return int(value)
        if kind == b"$":
            length = int(value)
            if length == -1:
                return None
            data = self.reader.read(length)
            if self.reader.read(2) != b"\r\n" or len(data) != length:
                raise ConnectionError("incomplete RESP bulk reply")
            return data
        if kind == b"*":
            length = int(value)
            return None if length == -1 else [self.read_response() for _ in range(length)]
        raise ValueError("unexpected RESP type; this probe requires RESP2")


def require(condition, detail):
    if not condition:
        raise AssertionError(detail)


def parsed_rules(info):
    """Apply the exact types/indexes required by OPSI's downsampling parser."""
    require(isinstance(info, list), "TS.INFO must be a RESP2 list")
    rules = {}
    found = False
    for index, value in enumerate(info):
        if isinstance(value, bytes) and "rules" in value.decode("utf8"):
            found = True
            for rule in info[index + 1]:
                rules[rule[0].decode("utf8")] = {
                    "time_bucket": rule[1], "aggregation": rule[2].decode("utf8")}
    require(found, "TS.INFO omits the rules field OPSI reads")
    return rules


def samples(rows):
    result = []
    for row in rows:
        require(isinstance(row, list) and len(row) == 2, "invalid RANGE row shape")
        require(isinstance(row[0], int), "RANGE timestamp must be integer")
        number = float(row[1])  # Same conversion as OPSI's Grafana endpoint.
        require(math.isfinite(number), "non-finite metric result")
        result.append([row[0], number])
    return result


def run(args):
    client = RedisWire(args)
    prefix = "opsi-contract:" + uuid.uuid4().hex
    keys = {name: f"{prefix}:{name}" for name in (
        "source", "minute", "hour", "day", "global", "node", "depot",
        "copy", "plain", "plain-copy", "import")}
    checked = []

    def keep_short(key):
        require(client.command("EXPIRE", key, 600) == 1, "test key expiry failed")

    def create(key, retention=60000, labels=()):
        command = ["TS.CREATE", key, "RETENTION", retention]
        if labels:
            command.extend(["LABELS", *labels])
        require(client.command(*command) == b"OK", "TS.CREATE did not return OK")
        keep_short(key)

    try:
        require(client.command("PING") == b"PONG", "PING failed")
        needed = ["TS.CREATE", "TS.INFO", "TS.ADD", "TS.CREATERULE", "TS.DELETERULE", "TS.RANGE", "TS.MADD"]
        info = client.command("COMMAND", "INFO", *needed)
        require(len(info) == len(needed) and all(info), "required TS command missing")
        checked.append("required command capabilities")

        modules = [dict(zip(row[::2], row[1::2])) for row in client.command("MODULE", "LIST")]
        module_names = [row[b"name"].decode() for row in modules]
        opsi_required_module_name_present = "timeseries" in module_names
        general_info = client.command("INFO").decode()
        require(any("name=" + name + "," in general_info for name in module_names), "INFO omits loaded module metadata")

        labels = ("node_name", "contract", "worker_num", "1")
        create(keys["source"], labels=labels)
        create(keys["global"])
        create(keys["node"], labels=("node_name", "contract"))
        create(keys["depot"], labels=("depot_id", "contract.example.invalid"))
        require(parsed_rules(client.command("TS.INFO", keys["source"])) == {}, "new series already has rules")
        try:
            client.command("TS.CREATE", keys["source"], "RETENTION", 60000, "LABELS", *labels)
        except ResponseError as error:
            require(str(error) == "TSDB: key already exists", "duplicate CREATE error differs from OPSI's exact comparison")
        else:
            raise AssertionError("duplicate CREATE unexpectedly succeeded")
        checked.append("series scopes and exact restart duplicate error")

        for name, retention, duration in (
            ("minute", 86400000, 60000), ("hour", 5184000000, 3600000),
            ("day", 126144000000, 86400000)):
            create(keys[name], retention, labels)
            require(client.command("TS.CREATERULE", keys["source"], keys[name], "AGGREGATION", "avg", duration) == b"OK", "CREATERULE failed")
        rules = parsed_rules(client.command("TS.INFO", keys["source"]))
        for name, duration in (("minute", 60000), ("hour", 3600000), ("day", 86400000)):
            require(rules[keys[name]]["time_bucket"] == duration, "rule duration type/value differs")
            require(rules[keys[name]]["aggregation"].lower() == "avg", "rule aggregation differs")
        checked.append("OPSI minute/hour/day retention and INFO rule parsing")

        # Repeat setup as on an OPSI restart: existing keys must raise exactly
        # the swallowed error, and parsed rule values must prevent re-creation.
        for name, retention in (("source", 60000), ("minute", 86400000),
                                ("hour", 5184000000), ("day", 126144000000)):
            try:
                client.command("TS.CREATE", keys[name], "RETENTION", retention, "LABELS", *labels)
            except ResponseError as error:
                require(str(error) == "TSDB: key already exists", "repeated setup duplicate error differs")
            else:
                raise AssertionError("repeated setup unexpectedly recreated existing key")
        require(parsed_rules(client.command("TS.INFO", keys["source"])) == rules,
                "repeated setup does not retain the existing downsampling rules")
        checked.append("repeated OPSI downsampling setup remains idempotent")

        # Replace one existing rule as OPSI does after a changed configuration.
        require(client.command("TS.DELETERULE", keys["source"], keys["minute"]) == b"OK", "DELETERULE failed")
        require(keys["minute"] not in parsed_rules(client.command("TS.INFO", keys["source"])), "deleted rule remains visible")
        client.command("TS.CREATERULE", keys["source"], keys["minute"], "AGGREGATION", "avg", 1000)
        base = int(time.time()) * 1000
        for offset, value in ((1, 2), (1, 3), (200, 7), (1001, 11)):
            timestamp = base + offset
            require(client.command("TS.ADD", keys["source"], timestamp, value, "RETENTION", 60000,
                                   "ON_DUPLICATE", "SUM", "LABELS", *labels) == timestamp, "TS.ADD timestamp reply differs")
        rows = samples(client.command("TS.RANGE", keys["source"], base, base + 999, "AGGREGATION", "avg", 1000))
        require(rows == [[base, 6.0]], "duplicate SUM / RANGE avg differs")
        compacted = samples(client.command("TS.RANGE", keys["minute"], "-", "+"))
        require(compacted == [[base, 6.0]], "compaction output differs")
        checked.append("rule replacement, duplicate SUM, aggregate query and real compaction")

        # Global OPSI metrics really send an empty LABELS clause.
        client.command("TS.ADD", keys["global"], base, 4, "RETENTION", 60000, "ON_DUPLICATE", "SUM", "LABELS")
        require(samples(client.command("TS.RANGE", keys["global"], "-", "+")) == [[base, 4.0]], "global empty LABELS clause unsupported")
        checked.append("global metric ADD with empty LABELS")

        dumped = client.command("DUMP", keys["global"])
        require(client.command("RESTORE", keys["copy"], 600000, dumped, "REPLACE") == b"OK", "same-module RESTORE failed")
        require(samples(client.command("TS.RANGE", keys["copy"], "-", "+")) == [[base, 4.0]], "same-module backup roundtrip differs")
        client.command("SET", keys["plain"], "contract", "EX", 600)
        client.command("RESTORE", keys["plain-copy"], 600000, client.command("DUMP", keys["plain"]), "REPLACE")
        require(client.command("GET", keys["plain-copy"]) == b"contract", "native Redis type roundtrip differs")
        checked.append("same-module and native-type DUMP/RESTORE (not foreign module compatibility)")

        create(keys["import"], labels=labels)
        require(client.command("TS.MADD", keys["import"], base, 2.5, keys["import"], base + 1000, 4.5) == [base, base + 1000], "TS.MADD import reply differs")
        require(samples(client.command("TS.RANGE", keys["import"], "-", "+")) == [[base, 2.5], [base + 1000, 4.5]], "semantic sample import differs")
        checked.append("semantic sample export/import")

        require(args.expected_module in module_names, "expected module identity missing; functional checks passed")
        return {"success": True, "module_names": module_names,
                "opsi_required_module_name_present": opsi_required_module_name_present, "checks": checked}
    finally:
        try:
            client.command("DEL", *keys.values())
        finally:
            client.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=6379)
    parser.add_argument("--database", type=int, default=0)
    parser.add_argument("--username", default="")
    parser.add_argument("--password-env", default="REDIS_PASSWORD")
    parser.add_argument("--expected-module", choices=("ts", "timeseries"), default="timeseries")
    parser.add_argument("--tls", action="store_true")
    parser.add_argument("--ca-file")
    args = parser.parse_args()
    try:
        result = run(args)
    except Exception as error:
        # Server errors can echo arbitrary input; redact any configured password.
        detail = str(error)
        password = os.environ.get(args.password_env)
        if password:
            detail = detail.replace(password, "[redacted]")
        print(json.dumps({"success": False, "error_type": type(error).__name__, "error": detail}))
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
