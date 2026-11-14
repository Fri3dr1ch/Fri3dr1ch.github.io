#!/usr/bin/env python3
"""Create a ready-to-copy repository overlay, excluding private Sites metadata."""
import argparse
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
DIRECTORIES = ('data', 'dist', 'docs', 'scripts', 'tests')
FILES = ('README.md', 'DEPLOYMENT.md', 'requirements-import.txt', 'requirements-import.lock', 'requirements-import-review.txt',
         'requirements-dev.txt', 'package.json', 'package-lock.json', '.gitignore')


def workflow_path(root):
    """Resolve both the standalone source and installed /veranstaltungen layout."""
    root = root.resolve()
    candidates = [root / '.github/workflows/calendar.yml']
    if root.name == 'veranstaltungen':
        candidates.append(root.parent / '.github/workflows/calendar.yml')
    found = [path for path in candidates if path.is_file()]
    if len(found) != 1:
        raise ValueError('Genau eine calendar.yml in der Projekt- oder Repository-Wurzel erforderlich')
    if found[0].is_symlink():
        raise ValueError('Workflow darf kein symbolischer Link sein')
    return found[0]


def build(root, target):
    workflow = workflow_path(root)
    target.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(target, 'w', ZIP_DEFLATED) as archive:
        for name in FILES:
            path = root / name
            if not path.is_file(): raise ValueError('Paketdatei fehlt: ' + name)
            archive.write(path, 'veranstaltungen/' + name)
        for directory in DIRECTORIES:
            for path in sorted((root / directory).rglob('*')):
                if path.is_symlink(): raise ValueError('Symbolischer Link im Quellpaket')
                if not path.is_file() or any(part in {'__pycache__', 'node_modules'} for part in path.parts) or path.suffix in {'.pyc', '.tmp'}:
                    continue
                archive.write(path, 'veranstaltungen/' + path.relative_to(root).as_posix())
        archive.write(workflow, '.github/workflows/calendar.yml')
    return target


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--root', type=Path, default=ROOT)
    args = parser.parse_args()
    print(build(args.root, args.output))
