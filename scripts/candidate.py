#!/usr/bin/env python3
"""Record and verify the exact archive promoted from CI to a release."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import tarfile


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def archive_metadata(path):
    with tarfile.open(path, 'r:gz') as archive:
        member = archive.extractfile('opsi/Chart.yaml')
        if member is None:
            raise ValueError('Missing Chart.yaml')
        chart = member.read().decode()
    result = {}
    for key in ('name', 'version', 'appVersion'):
        match = re.search(r'^' + key + r':\s*[\"\']?([^\s\"\']+)', chart, re.M)
        if match is None:
            raise ValueError(f'Missing chart field: {key}')
        result[key] = match.group(1)
    if result['name'] != 'opsi' or path.name != f"opsi-{result['version']}.tgz":
        raise ValueError('Unexpected chart name or archive filename')
    return result


def verify(directory, source=None, expected_sha=None):
    metadata = json.loads((directory / 'candidate.json').read_text())
    packages = list(directory.glob('*.tgz'))
    if len(packages) != 1:
        raise ValueError('Expected exactly one candidate archive')
    package = packages[0]
    actual = sha256(package)
    if metadata.get('package') != package.name or metadata.get('sha256') != actual:
        raise ValueError('Candidate archive differs from its recorded checksum')
    chart = archive_metadata(package)
    if any(metadata.get(key) != value for key, value in chart.items()):
        raise ValueError('Candidate chart metadata differs from its provenance')
    if not re.fullmatch(r'[0-9a-f]{40}', metadata.get('source_sha', '')):
        raise ValueError('Invalid source commit')
    if source is not None and metadata['source_sha'] != source:
        raise ValueError('Candidate belongs to a different source commit')
    if expected_sha is not None and actual != expected_sha:
        raise ValueError('Candidate differs from the locally accepted archive')
    return metadata


def verify_run(run, jobs, repository):
    expected = {
        'event': 'push', 'status': 'completed', 'conclusion': 'success',
        'head_branch': 'main', 'path': '.github/workflows/ci.yml',
    }
    if any(run.get(key) != value for key, value in expected.items()):
        raise ValueError('Release requires successful main-push Helm CI')
    if run.get('head_repository', {}).get('full_name') != repository:
        raise ValueError('CI run belongs to a different repository')
    if not re.fullmatch(r'[0-9a-f]{40}', run.get('head_sha', '')):
        raise ValueError('Invalid CI source commit')
    required = {'Static / Helm 3', 'Static / Helm 4', 'Build candidate',
                'Runtime / Kubernetes 1.34'}
    successful = {job['name'] for job in jobs if job.get('conclusion') == 'success'}
    if not required.issubset(successful):
        raise ValueError('A required CI job did not succeed')
    return run['head_sha']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['create', 'verify', 'verify-run'])
    parser.add_argument('directory', type=Path)
    parser.add_argument('--source')
    parser.add_argument('--sha256')
    parser.add_argument('--field')
    args = parser.parse_args()
    if args.action == 'verify-run':
        run = json.loads((args.directory / 'run.json').read_text())
        pages = json.loads((args.directory / 'jobs.json').read_text())
        jobs = [job for page in pages for job in page['jobs']]
        print(verify_run(run, jobs, os.environ['GITHUB_REPOSITORY']))
        return
    if args.action == 'create':
        packages = list(args.directory.glob('*.tgz'))
        if len(packages) != 1:
            raise ValueError('Expected exactly one newly packaged archive')
        package = packages[0]
        metadata = archive_metadata(package) | {
            'package': package.name, 'sha256': sha256(package),
            'source_sha': os.environ['GITHUB_SHA'],
            'run_id': os.environ['GITHUB_RUN_ID'],
            'run_attempt': os.environ['GITHUB_RUN_ATTEMPT'],
        }
        (args.directory / 'candidate.json').write_text(json.dumps(metadata, indent=2) + '\n')
    metadata = verify(args.directory, args.source, args.sha256)
    print(metadata[args.field] if args.field else json.dumps(metadata, indent=2))


if __name__ == '__main__':
    main()
