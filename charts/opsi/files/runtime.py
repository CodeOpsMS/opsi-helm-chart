"""Small stdlib runtime around the supported upstream OPSI entrypoint.

No secret is logged, no host key is generated, and no database restore runs here.
The pinned reference image adds cryptography to the upstream server image.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import ssl
import subprocess
import sys
import tempfile
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request

CONFIG = Path("/opt/opsi-chart/config.json")
CA_FILE = Path("/etc/opsi/ssl/opsi-ca-cert.pem")
MARKER = Path("/run/opsi-chart-ready")
BASE_URL = "https://127.0.0.1:4447"


def config():
    return json.loads(CONFIG.read_text())


def fingerprint():
    return hashlib.sha256(CONFIG.read_bytes()).hexdigest()


def check_identity(path, expected):
    if path.exists():
        identity = tomllib.loads(path.read_text()).get("host", {}).get("id")
        if identity != expected:
            raise RuntimeError("Persisted host ID differs from server.hostId; perform an explicit restore/rename before starting")


def launch():
    cfg = config()
    check_identity(Path("/data/etc/opsi.conf"), cfg["server"]["hostId"])
    MARKER.unlink(missing_ok=True)
    env = os.environ.copy()
    # Percent-encode credentials before the upstream shell builds its URLs.
    quote = lambda key: urllib.parse.quote(env[key], safe="")
    env["OPSICONFD_MYSQL_INTERNAL_URL"] = (
        f"mysql://{quote('MYSQL_USER')}:{quote('MYSQL_PASSWORD')}@"
        f"{env['MYSQL_HOST']}:{env['MYSQL_PORT']}/{quote('MYSQL_DATABASE')}"
    )
    env["OPSICONFD_REDIS_INTERNAL_URL"] = (
        f"redis://default:{quote('REDIS_PASSWORD')}@{env['REDIS_HOST']}:{env['REDIS_PORT']}"
        f"?db={env['REDIS_DATABASE']}"
    )
    os.execve("/usr/bin/zsh", ["zsh", "/opt/opsi-chart/entrypoint.zsh"], env)


def request(path, data=None, method=None, base_url=None, ca_file=None):
    base_url = base_url or BASE_URL
    ca_file = ca_file or CA_FILE
    headers = {}
    if data is not None or method == "PROPFIND":
        credentials = f"{os.environ['OPSI_ADMIN_USER']}:{os.environ['OPSI_ADMIN_PASSWORD']}"
        headers["Authorization"] = "Basic " + base64.b64encode(credentials.encode()).decode()
        headers["Content-Type"] = "application/json"
    if method == "PROPFIND":
        headers["Depth"] = "0"
    req = urllib.request.Request(base_url + path, data=data, headers=headers, method=method)
    context = ssl.create_default_context(cafile=str(ca_file))
    # Never inherit an HTTP proxy for localhost probes or bootstrap credentials.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=context))
    with opener.open(req, timeout=5) as response:
        return response.read(), response.status


def rpc(method, params):
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    body, _ = request("/rpc", json.dumps(payload).encode())
    result = json.loads(body)
    if result.get("error"):
        # Backend errors can include passwords/host objects. Do not emit them.
        raise RuntimeError(f"OPSI RPC {method} failed")
    return result.get("result")


def status_ok(body):
    status = dict(line.split(":", 1) for line in body.decode().splitlines() if ":" in line)
    return status.get("status", "").strip() == "ok" and status.get("redis-status", "").strip() == "ok"


def atomic_write(path, payload):
    target = path.resolve()
    previous = target.stat()
    with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".opsi-ca-", delete=False) as handle:
        temp = Path(handle.name)
        try:
            os.fchmod(handle.fileno(), previous.st_mode & 0o777)
            os.fchown(handle.fileno(), previous.st_uid, previous.st_gid)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
            os.replace(temp, target)
        finally:
            temp.unlink(missing_ok=True)


def merge_ca(ca_file=CA_FILE, extra_dir=Path("/opt/opsi-additional-ca")):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization

    original = ca_file.read_bytes()
    existing = x509.load_pem_x509_certificates(original)
    if not existing:
        raise RuntimeError("Existing OPSI CA bundle is empty")
    # The native endpoint returns the authoritative own OPSI CA only. Compare
    # fingerprints, not CNs (different GlobalSign roots share a common name).
    own_pem, _ = request("/ssl/opsi-ca-cert.pem", ca_file=ca_file)
    own = x509.load_pem_x509_certificate(own_pem)
    own_fp = own.fingerprint(hashes.SHA256())
    if own_fp not in {cert.fingerprint(hashes.SHA256()) for cert in existing}:
        raise RuntimeError("Native OPSI CA is missing from persisted bundle")
    certs = [own] + [cert for cert in existing if cert.fingerprint(hashes.SHA256()) != own_fp]
    for path in sorted(extra_dir.glob("*/*")):
        if path.suffix.lower() not in (".pem", ".crt") or not path.is_file():
            continue
        pem = path.read_bytes()
        if b"PRIVATE KEY" in pem:
            raise RuntimeError("Additional CA input contains a private key")
        additional = x509.load_pem_x509_certificates(pem)
        if not additional:
            raise RuntimeError("Additional CA input contains no certificates")
        for cert in additional:
            if not cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca:
                raise RuntimeError("Additional certificate is not a CA")
        certs.extend(additional)
    unique = {}
    for cert in certs:
        unique.setdefault(cert.fingerprint(hashes.SHA256()), cert)
    merged = b"".join(cert.public_bytes(serialization.Encoding.PEM) for cert in unique.values())
    if merged != original:
        atomic_write(ca_file, merged)
    return {fp.hex() for fp in unique}


def reconcile(cfg):
    server = cfg["server"]
    own = rpc("host_getObjects", {"id": server["hostId"]})
    if len(own) != 1 or own[0]["type"] != "OpsiConfigserver":
        raise RuntimeError("Expected exactly the configured OpsiConfigserver")
    # Keep the complete original object so constructors cannot default a missing
    # opsiHostKey or other optional field. Only the explicit endpoint fields move.
    fields = ("ipAddress", "depotRemoteUrl", "depotWebdavUrl", "repositoryRemoteUrl", "workbenchRemoteUrl")
    update = {key: server[key] for key in fields if own[0].get(key) != server[key]}
    if update:
        rpc("host_updateObject", {"host": {**own[0], **update}})
    defaults = {
        "clientconfig.configserver.url": server["configServiceUrls"],
        "clientconfig.depot.protocol": ["webdav"],
        "clientconfig.depot.protocol.netboot": ["webdav"],
    }
    for config_id, values in defaults.items():
        current = rpc("config_getObjects", {"id": config_id})
        if current:
            obj = current[0]
            if obj.get("defaultValues") == values and (config_id != "clientconfig.configserver.url" or obj.get("multiValue")):
                continue
            obj["defaultValues"] = values
            obj["possibleValues"] = list(dict.fromkeys((obj.get("possibleValues") or []) + values))
            if config_id == "clientconfig.configserver.url":
                obj["multiValue"] = True
            rpc("config_updateObjects", {"configs": [obj]})
        else:
            rpc("config_createUnicode", {"id": config_id, "possibleValues": values, "defaultValues": values,
                                       "editable": True, "multiValue": config_id == "clientconfig.configserver.url"})


def bootstrap():
    cfg = config()
    deadline = time.monotonic() + cfg["bootstrap"]["timeoutSeconds"]
    while time.monotonic() < deadline:
        try:
            if not status_ok(request("/status/")[0]):
                raise RuntimeError("OPSI dependencies are not healthy")
            break
        except (OSError, ValueError, RuntimeError, urllib.error.URLError):
            time.sleep(3)
    else:
        raise RuntimeError("Timed out waiting for the native OPSI API")
    check_identity(Path("/etc/opsi/opsi.conf"), cfg["server"]["hostId"])
    merge_ca()
    if cfg["bootstrap"]["enabled"]:
        reconcile(cfg)
    MARKER.write_text(fingerprint())
    print("OPSI chart bootstrap complete", flush=True)


def ready():
    if not MARKER.exists() or MARKER.read_text() != fingerprint():
        raise RuntimeError("OPSI bootstrap has not completed")
    if not status_ok(request("/status/")[0]):
        raise RuntimeError("OPSI status reports a dependency error")
    if config()["pxe"]["enabled"]:
        result = subprocess.run(["/usr/bin/supervisorctl", "status", "tftpd", "opsipxeconfd"],
                                capture_output=True, text=True, timeout=3, check=True)
        states = {parts[0]: parts[1] for line in result.stdout.splitlines() if len(parts := line.split()) >= 2}
        if any(states.get(name) != "RUNNING" for name in ("tftpd", "opsipxeconfd")):
            raise RuntimeError("PXE services are not running")


def helm_test():
    global BASE_URL, CA_FILE
    BASE_URL = os.environ["OPSI_TEST_URL"]
    CA_FILE = Path("/data/etc/ssl/opsi-ca-cert.pem")
    if not status_ok(request("/status/")[0]):
        raise RuntimeError("OPSI status reports a dependency error")
    server = config()["server"]
    own = rpc("host_getObjects", {"id": server["hostId"]})
    if len(own) != 1 or own[0].get("depotWebdavUrl") != server["depotWebdavUrl"]:
        raise RuntimeError("OPSI identity or depot configuration differs from Helm values")
    if request("/depot/", method="PROPFIND")[1] != 207:
        raise RuntimeError("Authenticated WebDAV request failed")
    print("Verified native TLS, Redis status, authenticated API identity and WebDAV")


if __name__ == "__main__":
    try:
        {"launch": launch, "bootstrap": bootstrap, "ready": ready, "test": helm_test}[sys.argv[1]]()
    except Exception as error:
        # Avoid exception strings from network/URI libraries exposing credentials.
        detail = str(error) if isinstance(error, RuntimeError) else type(error).__name__
        print(f"OPSI chart {sys.argv[1]} failed: {detail}", file=sys.stderr)
        sys.exit(1)
