"""Run with: python3 -m unittest discover -s charts/opsi/tests/unit -v"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

CHART = Path(__file__).resolve().parents[2]


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, CHART / "files" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


runtime = load("runtime", "runtime.py")
tftp = load("tftp", "tftp-sync.py")


def certificate(name, is_ca=True):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    return (x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(datetime.now(timezone.utc) - timedelta(days=1))
            .not_valid_after(datetime.now(timezone.utc) + timedelta(days=10))
            .add_extension(x509.BasicConstraints(ca=is_ca, path_length=None), critical=True)
            .sign(key, hashes.SHA256()).public_bytes(serialization.Encoding.PEM))


class RuntimeTests(unittest.TestCase):
    def test_early_volume_binding_preserves_upstream_rights_trigger(self):
        with tempfile.TemporaryDirectory() as directory:
            mock = Path(directory) / "entrypoint.zsh"
            mock.write_text('''typeset -g init_calls=0
function init_volumes {
    (( init_calls += 1 ))
    if (( init_calls == 1 )); then return 1; fi
    return 0
}
function entrypoint {
    typeset run_set_rights=false
    init_volumes || run_set_rights=true
    [[ "$run_set_rights" == true ]] || exit 9
    [[ "$init_calls" == 2 ]] || exit 10
    print "rights preserved"
}
''')
            script = (CHART / "files/entrypoint.zsh").read_text()
            script = script.replace("source /entrypoint.sh set_environment_vars", 'source "$MOCK_ENTRYPOINT"')
            script = script.replace("/usr/bin/python3 /opt/opsi-chart/runtime.py bootstrap &", "true")
            result = subprocess.run(["zsh"], input=script, text=True, capture_output=True,
                                    env={**os.environ, "MOCK_ENTRYPOINT": str(mock)})
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("rights preserved", result.stdout)

    def test_dependency_error_with_http_200_is_unready(self):
        self.assertFalse(runtime.status_ok(b"status: error\nredis-status: error\n"))
        self.assertFalse(runtime.status_ok(b"status: ok\n"))
        self.assertTrue(runtime.status_ok(b"status: ok\nredis-status: ok\nredis-error: \n"))

    def test_restore_identity_must_match(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "opsi.conf"
            path.write_text('[host]\nid = "restored.example.invalid"\nkey = "unchanged"\n')
            runtime.check_identity(path, "restored.example.invalid")
            with self.assertRaises(RuntimeError):
                runtime.check_identity(path, "new.example.invalid")
            self.assertIn('key = "unchanged"', path.read_text())

    def test_ca_merge_preserves_identity_and_is_idempotent(self):
        own, extra = certificate("OPSI CA"), certificate("Additional root")
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            target = base / "persistent-ca.pem"
            target.write_bytes(own)
            target.chmod(0o640)
            link = base / "ca.pem"
            link.symlink_to(target)
            extras = base / "extras" / "existing"
            extras.mkdir(parents=True)
            (extras / "root.crt").write_bytes(extra + extra)
            with patch.object(runtime, "request", return_value=(own, 200)):
                fingerprints = runtime.merge_ca(link, extras.parent)
                content = target.read_bytes()
                modified = target.stat().st_mtime_ns
                self.assertEqual(fingerprints, runtime.merge_ca(link, extras.parent))
            self.assertEqual(len(fingerprints), 2)
            self.assertEqual(content, own + extra)
            self.assertEqual(modified, target.stat().st_mtime_ns)
            self.assertEqual(target.stat().st_mode & 0o777, 0o640)
            self.assertTrue(link.is_symlink())

    def test_leaf_and_key_are_rejected_without_modifying_ca(self):
        own = certificate("OPSI CA")
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            ca = base / "ca.pem"
            ca.write_bytes(own)
            extras = base / "extras" / "values"
            extras.mkdir(parents=True)
            for invalid in (certificate("Leaf", is_ca=False), b"-----BEGIN PRIVATE KEY-----\ninvalid"):
                (extras / "bad.pem").write_bytes(invalid)
                with patch.object(runtime, "request", return_value=(own, 200)):
                    with self.assertRaises(RuntimeError):
                        runtime.merge_ca(ca, extras.parent)
                self.assertEqual(ca.read_bytes(), own)

    def test_reconcile_preserves_hostkey_and_unrelated_data(self):
        own = {"id": "opsi.example.invalid", "type": "OpsiConfigserver", "opsiHostKey": "unchanged-test-key",
               "notes": "keep me", "depotRemoteUrl": "smb://old/depot", "ipAddress": "192.0.2.1"}
        before = deepcopy(own)
        desired = {"hostId": own["id"], "ipAddress": "192.0.2.2", "configServiceUrls": ["https://opsi.example.invalid:443"],
                   **{key: f"webdavs://opsi.example.invalid:4447/{path}" for key, path in
                      [("depotRemoteUrl", "depot"), ("depotWebdavUrl", "depot"),
                       ("repositoryRemoteUrl", "repository"), ("workbenchRemoteUrl", "workbench")]}}
        calls = []

        def api(method, params):
            calls.append((method, deepcopy(params)))
            if method == "host_getObjects":
                self.assertEqual(params, {"id": own["id"]})
                return [own]
            if method == "config_getObjects":
                return []
            return None

        with patch.object(runtime, "rpc", side_effect=api):
            runtime.reconcile({"server": desired})
        changed = next(params["host"] for method, params in calls if method == "host_updateObject")
        self.assertEqual(changed["opsiHostKey"], before["opsiHostKey"])
        self.assertEqual(changed["notes"], before["notes"])
        self.assertEqual(own, before)
        defaults = {params["id"]: params["defaultValues"] for method, params in calls if method == "config_createUnicode"}
        self.assertEqual(set(defaults), {"clientconfig.configserver.url", "clientconfig.depot.protocol", "clientconfig.depot.protocol.netboot"})
        self.assertEqual(defaults["clientconfig.depot.protocol.netboot"], ["webdav"])


class TftpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name) / "image"
        self.target = Path(self.temp.name) / "pvc"
        self.source.mkdir()
        (self.source / "bootimage").mkdir()
        (self.source / "bootimage" / "kernel").write_text("version one")
        (self.source / "cfg").mkdir()

    def test_seed_upgrade_preserves_product_and_client_files(self):
        tftp.sync(self.source, self.target)
        (self.target / "cfg" / "client.cfg").write_text("client state")
        (self.target / "hwinvent.py").write_text("installed product")
        (self.source / "bootimage" / "kernel").write_text("version two")
        tftp.sync(self.source, self.target)
        self.assertEqual((self.target / "bootimage" / "kernel").read_text(), "version two")
        self.assertEqual((self.target / "cfg" / "client.cfg").read_text(), "client state")
        self.assertEqual((self.target / "hwinvent.py").read_text(), "installed product")

    def test_modified_stock_file_blocks_before_writing_other_files(self):
        tftp.sync(self.source, self.target)
        (self.target / "bootimage" / "kernel").write_text("operator modified")
        (self.source / "new-loader").write_text("new")
        with self.assertRaisesRegex(RuntimeError, "local changes"):
            tftp.sync(self.source, self.target)
        self.assertFalse((self.target / "new-loader").exists())
        self.assertEqual((self.target / "bootimage" / "kernel").read_text(), "operator modified")

    def test_repeated_sync_and_removed_image_files_are_lossless(self):
        tftp.sync(self.source, self.target)
        old = (self.target / tftp.MANIFEST).read_text()
        tftp.sync(self.source, self.target)
        self.assertEqual((self.target / tftp.MANIFEST).read_text(), old)
        (self.source / "bootimage" / "kernel").unlink()
        (self.source / "loader").write_text("new format")
        tftp.sync(self.source, self.target)
        self.assertTrue((self.target / "bootimage" / "kernel").exists())

    def test_symlink_ancestor_cannot_escape_pvc(self):
        self.target.mkdir()
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        (self.target / "bootimage").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(RuntimeError, "Unsafe"):
            tftp.sync(self.source, self.target)
        self.assertFalse((outside / "kernel").exists())

    def test_image_symlinks_are_preserved_not_followed(self):
        (self.source / "linux").symlink_to("bootimage")
        tftp.sync(self.source, self.target)
        self.assertTrue((self.target / "linux").is_symlink())
        self.assertEqual((self.target / "linux").readlink(), Path("bootimage"))


class ChartTests(unittest.TestCase):
    def helm(self, *args):
        return subprocess.run(["helm", "template", "opsi", str(CHART), "-f", str(CHART / "ci/basic-values.yaml"), *args],
                              text=True, capture_output=True)

    def test_digest_persistence_and_non_pxe_render(self):
        result = self.helm("--set", "image.repository=registry.example.invalid/opsi", "--set", "image.digest=sha256:" + "a" * 64)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("registry.example.invalid/opsi@sha256:" + "a" * 64, result.stdout)
        self.assertIn("type: Recreate", result.stdout)
        self.assertIn("helm.sh/resource-policy: keep", result.stdout)
        self.assertNotIn("hostNetwork: true", result.stdout)
        self.assertNotIn("kind: Secret", result.stdout)

    def test_progress_deadline_includes_bootstrap_and_startup_margin(self):
        default = self.helm()
        self.assertEqual(default.returncode, 0, default.stderr)
        self.assertIn("progressDeadlineSeconds: 1080", default.stdout)
        custom = self.helm("--set", "bootstrap.timeoutSeconds=1200")
        self.assertEqual(custom.returncode, 0, custom.stderr)
        self.assertIn("progressDeadlineSeconds: 1380", custom.stdout)

    def test_smoke_mounts_only_public_ca_without_root_or_extra_capabilities(self):
        result = self.helm("--show-only", "templates/tests/smoke.yaml")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("mountPath: /opt/opsi-test-ca.pem", result.stdout)
        self.assertIn("subPath: etc/ssl/opsi-ca-cert.pem", result.stdout)
        self.assertNotIn("mountPath: /data", result.stdout)
        self.assertIn("runAsNonRoot: true", result.stdout)
        self.assertIn("drop: [ALL]", result.stdout)
        self.assertNotIn("fsGroup:", result.stdout)

    def test_pxe_existing_claims_and_suspended_connector(self):
        result = self.helm("-f", str(CHART / "ci/pxe-values.yaml"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("dnsPolicy: ClusterFirstWithHostNet", result.stdout)
        self.assertIn("mountPath: /tftp-volume", result.stdout)
        self.assertIn("claimName: opsi-existing-data", result.stdout)
        self.assertIn("suspend: true", result.stdout)
        self.assertNotIn("kind: PersistentVolumeClaim", result.stdout)

    def test_invalid_values_rejected(self):
        invalid = ("replicaCount=2", "pxe.enabled=true", "redis.port=6380", "admin.existingSecret=", "mysql.host=",
                   "connector.enabled=true", "admin.username=root", "server.externalUrl=http://opsi.example.invalid",
                   "server.depotWebdavUrl=smb://opsi.example.invalid/depot", "server.ipAddress=999.2.3.4")
        for value in invalid:
            with self.subTest(value=value):
                self.assertNotEqual(self.helm("--set", value).returncode, 0)

    def test_restore_mode_zero_replicas_has_no_test_pod(self):
        result = self.helm("--set", "replicaCount=0")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("replicas: 0", result.stdout)
        self.assertNotIn("helm.sh/hook: test", result.stdout)


if __name__ == "__main__":
    unittest.main()
