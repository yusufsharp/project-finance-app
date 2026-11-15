#!/usr/bin/env python3
"""Package runnable project sources, excluding secrets, data and binary artifacts."""
from pathlib import Path
import hashlib
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'defense' / 'finance-tracker-source.zip'
ROOT_FILES = {'.dockerignore', '.gitignore', '.env.example', 'README.md', 'HOMEWORK.md',
              'docker-compose.yml', 'docker-compose.hw4.yml'}
SOURCE_DIRS = {'frontend', 'operations', 'notifications', 'network', 'ha', 'scripts', 'docs'}
EXTENSIONS = {'.py', '.js', '.mjs', '.css', '.html', '.sql', '.yml', '.yaml', '.sh', '.md', '.txt', '.json'}


def main():
    names = subprocess.check_output(['git', 'ls-files', '--cached', '--others',
                                     '--exclude-standard', '-z'], cwd=ROOT).decode().split('\0')
    files = []
    for name in sorted(set(filter(None, names))):
        rel = Path(name)
        if not (ROOT / rel).is_file() or '__pycache__' in rel.parts:
            continue
        if rel.parts[:2] == ('docs', 'course'):
            continue
        source = rel.parts[0] in SOURCE_DIRS and (rel.suffix in EXTENSIONS or rel.name == 'Dockerfile')
        guide = rel.parts[0] == 'defense' and rel.suffix == '.md' and len(rel.parts) == 2
        if name in ROOT_FILES or source or guide:
            files.append(rel)
    OUTPUT.parent.mkdir(exist_ok=True)
    manifest = []
    with zipfile.ZipFile(OUTPUT, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for rel in files:
            data = (ROOT / rel).read_bytes()
            archive.write(ROOT / rel, 'finance-tracker/' + rel.as_posix())
            manifest.append(hashlib.sha256(data).hexdigest() + '  ' + rel.as_posix())
        archive.writestr('finance-tracker/SHA256SUMS.txt', '\n'.join(manifest) + '\n')
    print(f'{OUTPUT}: {len(files)} source files, {OUTPUT.stat().st_size} bytes')


if __name__ == '__main__':
    main()
