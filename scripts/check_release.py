#!/usr/bin/env python3
"""Conservative public-source check. Findings contain categories, never contents."""
import argparse
import json
from pathlib import Path, PurePosixPath
import re
import subprocess


ROOT = Path(__file__).resolve().parents[1]
MAX_BYTES = 2 * 1024 * 1024
PATTERNS = {
    'personal_home_path': re.compile(rb'/(?:Users|home)/[A-Za-z0-9_.-]+/'),
    'api_key_like': re.compile(rb'\bsk-[A-Za-z0-9_-]{20,}'),
    'github_token_like': re.compile(rb'\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})'),
    'private_key': re.compile(rb'-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----'),
    'device_id_assignment': re.compile(rb'(?i)\b(?:udid|device_id)\s*[=:]\s*[\x22\x27]?[0-9a-f]{8}-[0-9a-f]{16}\b'),
}


def path_issues(name):
    path = PurePosixPath(name)
    private = {'.state', '.superpowers', '.venv', '__pycache__', 'artifacts', 'screenshots', 'logs'}
    if path.is_absolute() or '..' in path.parts or set(path.parts) & private:
        return ['private_or_unsafe_path']
    if (path.name in {'config.json', 'calibration.json', 'auth.json', '.env', '.DS_Store'} or
            path.name.endswith(('.sqlite3', '.sqlite3-wal', '.sqlite3-shm', '.db', '.log',
                                '.png', '.jpg', '.jpeg', '.heic', '.mp4', '.zip', '.gz',
                                '.tar', '.p12', '.p8', '.mobileprovision', '.pem', '.key'))):
        return ['private_or_binary_artifact']
    return []


def scan(name, data, mode='100644'):
    issues = path_issues(name)
    if mode not in ('100644', '100755'):
        issues.append('unsupported_file_mode')
    if len(data) > MAX_BYTES:
        return issues + ['oversized_source']
    try:
        data.decode('utf-8')
    except UnicodeError:
        return issues + ['non_text_source']
    issues.extend(category for category, pattern in PATTERNS.items() if pattern.search(data))
    return issues


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT)


def check(history=False):
    findings = []
    checked = 0
    for record in git('ls-files', '--stage', '-z').split(b'\0'):
        if not record:
            continue
        metadata, raw_name = record.split(b'\t', 1)
        mode, oid, stage = metadata.decode().split()
        name = raw_name.decode('utf-8')
        path = ROOT / name
        data = path.read_bytes() if path.is_file() and not path.is_symlink() else b''
        issues = scan(name, data, mode)
        if not path.is_file() or path.is_symlink() or stage != '0':
            issues.append('missing_symlink_or_unmerged_source')
        if issues:
            findings.append({'file': name, 'categories': sorted(set(issues))})
        checked += 1
    objects = 0
    if history:
        # Scan commit messages as well as every reachable historical file blob.
        oids = git('rev-list', '--objects', '--all', '--no-object-names').splitlines()
        process = subprocess.Popen(['git', 'cat-file', '--batch'], cwd=ROOT,
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        try:
            for oid in oids:
                process.stdin.write(oid + b'\n')
                process.stdin.flush()
                header = process.stdout.readline().split()
                if len(header) != 3:
                    raise RuntimeError('invalid git object response')
                size = int(header[2])
                data = process.stdout.read(size)
                process.stdout.read(1)
                if header[1] not in (b'blob', b'commit'):
                    continue
                issues = scan('history-source.txt', data)
                if issues:
                    findings.append({'object': oid.decode(), 'categories': issues})
                objects += 1
        finally:
            process.stdin.close()
            process.stdout.close()
            process.wait(timeout=5)
        for raw in set(git('log', '--all', '--name-only', '--pretty=format:', '-z').split(b'\0')):
            name = raw.decode('utf-8').strip('\n')
            if name and path_issues(name):
                findings.append({'historical_file':name, 'categories':path_issues(name)})
    return {'ok':not findings, 'tracked_files':checked, 'historical_objects':objects,
            'findings':findings, 'note':'启发式检查不替代人工检查；未输出匹配原文。'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--history', action='store_true')
    args = parser.parse_args()
    result = check(args.history)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
