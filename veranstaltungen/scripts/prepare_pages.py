#!/usr/bin/env python3
"""Stage the existing GitHub Pages site plus /veranstaltungen/ calendar output."""
import argparse
from pathlib import Path
import shutil


def stage(repo, calendar, output):
    repo, calendar, output = repo.resolve(), calendar.resolve(), output.resolve()
    if repo == output or repo in output.parents:
        raise ValueError('Pages-Ausgabe muss außerhalb des Quellrepositorys liegen')
    if not (repo / 'index.html').is_file():
        raise ValueError('Bestehende Startseite fehlt; Veröffentlichung wäre unvollständig')
    if not (calendar / 'dist/index.html').is_file():
        raise ValueError('Kalenderausgabe fehlt')
    if output.exists() and any(output.iterdir()):
        raise ValueError('Pages-Ziel muss leer sein; vorhandene Dateien werden nicht gelöscht')
    output.mkdir(parents=True, exist_ok=True)
    excluded = {'.git', '.github', '.openai', 'node_modules', '__pycache__', '.venv'}
    def copy_tree(source, target, public_root=False):
        target.mkdir(parents=True, exist_ok=True)
        for item in source.iterdir():
            if item.name in excluded or item.name.startswith('.') or (public_root and item.resolve() == calendar):
                continue
            if item.is_symlink():
                raise ValueError('Symbolischer Link wird nicht veröffentlicht: ' + item.name)
            dest = target / item.name
            if item.is_dir(): copy_tree(item, dest)
            elif item.is_file(): shutil.copy2(item, dest)
    copy_tree(repo, output, public_root=True)
    destination = output / 'veranstaltungen'
    if destination.exists():
        raise ValueError('Öffentlicher Kalenderpfad kollidiert mit vorhandenen Dateien')
    copy_tree(calendar / 'dist', destination)
    (output / '.nojekyll').write_text('')
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo-root', type=Path, required=True)
    parser.add_argument('--calendar-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(stage(args.repo_root, args.calendar_root, args.output))
