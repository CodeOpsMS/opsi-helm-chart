import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('candidate', Path(__file__).parents[1] / 'scripts/candidate.py')
candidate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(candidate)


class CandidateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.package = self.root / 'opsi-0.1.0-rc.1.tgz'
        chart = b'name: opsi\nversion: 0.1.0-rc.1\nappVersion: "4.3.56.11"\n'
        with tarfile.open(self.package, 'w:gz') as archive:
            info = tarfile.TarInfo('opsi/Chart.yaml')
            info.size = len(chart)
            archive.addfile(info, io.BytesIO(chart))
        self.metadata = candidate.archive_metadata(self.package) | {
            'package': self.package.name, 'source_sha': 'a' * 40,
            'sha256': candidate.sha256(self.package), 'run_id': '123', 'run_attempt': '1',
        }
        (self.root / 'candidate.json').write_text(json.dumps(self.metadata))

    def test_exact_archive_and_source_are_accepted(self):
        self.assertEqual(candidate.verify(self.root, 'a' * 40, self.metadata['sha256']), self.metadata)

    def test_different_live_archive_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'locally accepted'):
            candidate.verify(self.root, expected_sha='b' * 64)

    def test_tampered_archive_is_rejected(self):
        with self.package.open('ab') as package:
            package.write(b'changed')
        with self.assertRaisesRegex(ValueError, 'checksum'):
            candidate.verify(self.root)

    def test_different_commit_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'different source'):
            candidate.verify(self.root, source='b' * 40)

    def test_ambiguous_archives_are_rejected(self):
        (self.root / 'opsi-another.tgz').write_bytes(self.package.read_bytes())
        with self.assertRaisesRegex(ValueError, 'exactly one'):
            candidate.verify(self.root)


class ReleaseGateTests(unittest.TestCase):
    def setUp(self):
        self.repository = 'CodeOpsMS/opsi-helm-chart'
        self.run = dict(event='push', status='completed', conclusion='success',
                        head_branch='main', path='.github/workflows/ci.yml', head_sha='a' * 40,
                        head_repository={'full_name': self.repository})
        self.jobs = [{'name': name, 'conclusion': 'success'} for name in
                     ('Static / Helm 3', 'Static / Helm 4', 'Build candidate', 'Runtime / Kubernetes 1.34')]

    def test_successful_main_push_is_accepted(self):
        self.assertEqual(candidate.verify_run(self.run, self.jobs, self.repository), 'a' * 40)

    def test_pr_and_manual_runs_are_not_release_sources(self):
        for event in ('pull_request', 'workflow_dispatch'):
            with self.subTest(event=event), self.assertRaises(ValueError):
                candidate.verify_run(self.run | {'event': event}, self.jobs, self.repository)

    def test_skipped_or_missing_runtime_does_not_pass(self):
        for conclusion in ('skipped', 'failure', 'cancelled'):
            with self.subTest(conclusion=conclusion), self.assertRaises(ValueError):
                jobs = self.jobs[:-1] + [self.jobs[-1] | {'conclusion': conclusion}]
                candidate.verify_run(self.run, jobs, self.repository)

    def test_foreign_repository_is_rejected(self):
        with self.assertRaises(ValueError):
            candidate.verify_run(self.run | {'head_repository': {'full_name': 'someone/fork'}}, self.jobs, self.repository)


if __name__ == '__main__':
    unittest.main()
