#!/usr/bin/env python3
"""Create a checked source archive from a clean, committed Git tree only."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile

from check_release import ROOT, check, scan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', help='新 ZIP 路径；不能覆盖已有文件')
    args = parser.parse_args()
    result = check(history=True)
    if not result['ok']:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1
    dirty = subprocess.check_output(['git','status','--porcelain','--untracked-files=normal'],cwd=ROOT)
    if dirty:
        raise SystemExit('请先提交全部公开源文件；忽略的本机状态不会打包。')
    output = Path(args.output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    prefix = 'wechat-codex-reply-loop/'
    with output.open('xb') as archive:
        subprocess.run(['git','archive','--format=zip','--prefix='+prefix,'HEAD'],
                       cwd=ROOT, stdout=archive, check=True)
    try:
        with zipfile.ZipFile(output) as archive:
            for entry in archive.infolist():
                if entry.is_dir():
                    continue
                issues = scan(entry.filename[len(prefix):],archive.read(entry))
                if issues:
                    raise RuntimeError('发布包中包含不允许的文件类别')
        sha = hashlib.sha256(output.read_bytes()).hexdigest()
        revision = subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
        print(json.dumps({'ok':True,'archive':str(output),'sha256':sha,'revision':revision,
                          'source':'git archive HEAD; ignored files excluded'},indent=2))
    except BaseException:
        output.unlink()
        raise
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
