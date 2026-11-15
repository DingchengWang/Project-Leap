#!/usr/bin/env python3
"""Assemble committed host and pinned module sources without Git metadata."""
from __future__ import annotations

import argparse
import io
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = 'tools/build/module-sources.json'


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args])


def archive_files(root, commit, paths=None):
    command = ['archive', '--format=tar', commit]
    if paths is not None:
        command += ['--', *paths]
    with tarfile.open(fileobj=io.BytesIO(git(root, *command)), mode='r:') as archive:
        files = {}
        for item in archive:
            path = PurePosixPath(item.name)
            if path.is_absolute() or '..' in path.parts or '.git' in path.parts:
                raise ValueError('Unexpected archive path: ' + item.name)
            if item.isdir():
                continue
            if not item.isfile():
                raise ValueError('Source export requires regular files: ' + item.name)
            files[item.name] = (archive.extractfile(item).read(), 0o755 if item.mode & 0o111 else 0o644)
    if paths is not None and set(files) != set(paths):
        raise ValueError('Module export differs from its explicit file list.')
    return files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fluorescence', type=Path, required=True)
    parser.add_argument('--counting', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.expanduser().resolve()
    if output.exists() or output.is_symlink():
        parser.error('Output must be a new directory; existing source and build trees are not overwritten.')
    host_commit = git(ROOT, 'rev-parse', 'HEAD').decode().strip()
    lock = json.loads(git(ROOT, 'show', host_commit + ':' + LOCK_PATH))
    if lock.get('schema_version') != 1:
        parser.error('Unsupported module source lock schema.')
    source_roots = {'fluorescence': args.fluorescence.resolve(), 'counting': args.counting.resolve()}
    for checkout in [ROOT, *source_roots.values()]:
        if output == checkout or output.is_relative_to(checkout):
            parser.error('The assembly output must be outside all three source checkouts.')
    files = archive_files(ROOT, host_commit)
    commits = {'main': host_commit}
    for name, checkout in source_roots.items():
        entry = lock['modules'][name]
        commit = entry['commit']
        if not isinstance(commit, str) or not re.fullmatch(r'[0-9a-f]{40}', commit):
            parser.error('Module commit is not pinned: ' + name)
        if git(checkout, 'rev-parse', '--show-toplevel').decode().strip() != str(checkout):
            parser.error('Not the expected module checkout: ' + str(checkout))
        exported = archive_files(checkout, commit, entry['export_files'])
        overlap = files.keys() & exported.keys()
        if overlap:
            parser.error('Multiple repositories own assembly paths: ' + ', '.join(sorted(overlap)))
        files.update(exported)
        commits[name] = commit
    output.mkdir(parents=True)
    for relative, (data, mode) in files.items():
        destination = output / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        destination.chmod(mode)
    record = {'schema_version': 1, 'repositories': commits, 'exported_files': len(files)}
    (output / '.source-assembly.json').write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps({'output': str(output), **record}))


if __name__ == '__main__':
    main()
