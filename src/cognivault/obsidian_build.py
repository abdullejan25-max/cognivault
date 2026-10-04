"""Explicit private Vault build through the configured Gateway, with read-back."""

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sys
from urllib.parse import quote, unquote

from .obsidian_projection import render_projection
from .obsidian_writer import ProjectionWriteError, write_projection
from .projection_collector import collect_projection
from .runtime import load_gateway_from_config


def _study_inventory(root: Path) -> dict:
    from .adapters.documents import _path_has_reparse_point
    if _path_has_reparse_point(root):
        raise ValueError("Unsafe Study root")
    inventory = {}
    for path in root.rglob('*'):
        if _path_has_reparse_point(path):
            raise ValueError("Unsafe Study member")
        if path.is_file():
            info = path.stat()
            inventory[path.relative_to(root).as_posix()] = (info.st_size, info.st_mtime_ns)
    return inventory


def build_projection(config: Path, vault: Path) -> dict:
    """Build twice from fresh collections; report aggregate checks, never private text."""
    from .adapters.documents import _path_has_reparse_point
    vault = vault.absolute()
    if _path_has_reparse_point(vault):
        raise ValueError('Unsafe Vault root')
    vault = vault.resolve(strict=True)
    if not (vault / '.obsidian').is_dir():
        raise ValueError("Target must be an existing Obsidian Vault")
    gateway = load_gateway_from_config(config)
    gateway._require_capability('projection')
    if _path_has_reparse_point(gateway.config.study_root):
        raise ValueError('Unsafe Study root')
    study = gateway.config.study_root.resolve(strict=True)
    # This build supports the established single-authority layout only.
    study_relative = study.relative_to(vault).as_posix()
    target = vault / 'V2Projection'
    if target.is_relative_to(study) or study.is_relative_to(target):
        raise ValueError('Study and generated projection must be disjoint')
    before = _study_inventory(study)

    def render():
        collection = collect_projection(gateway)
        files = render_projection(collection.snapshot)
        lines = ['# Study references', '', '> Generated index only. Linked Study files remain authoritative.', '']
        for relative in sorted(before):
            if relative.lower().endswith('.md'):
                # Relative paths are encoded; labels are plain safe basenames.
                label = Path(relative).stem.replace('[', '&#91;').replace(']', '&#93;').replace('\n', ' ')
                lines.append(f'- [{label}]({quote("../../" + study_relative + "/" + relative, safe="/")})')
        files['Study/index.md'] = '\n'.join(lines) + '\n'
        for text in files.values():
            if re.search(r'(?i)(?<![a-z0-9])[a-z]:[\\/]|\\\\[^\s\\]+\\|/Users/|/home/', text):
                raise ValueError('Projection contains an absolute private path')
        return collection, files

    def verify(files):
        manifest = json.loads((target / '.projection-manifest.json').read_text(encoding='utf-8'))
        if set(manifest['files']) != set(files):
            raise ValueError('Manifest ownership mismatch')
        for relative, text in files.items():
            data = (target / relative).read_bytes()
            if data != text.encode('utf-8'):
                raise ValueError('Projection read-back mismatch')
            for link in re.findall(r'\]\(([^)]+)\)', text):
                if '://' not in link and not link.startswith('#'):
                    destination = (target / relative).parent / unquote(link.split('#')[0])
                    if not destination.resolve().is_relative_to(vault) or not destination.is_file():
                        raise ValueError('Projection contains a broken relative link')
        return {str(path.relative_to(target)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in target.rglob('*') if path.is_file() and (str(path.relative_to(target)).replace('\\', '/') in files or path.name == '.projection-manifest.json')}

    _, files = render()
    write_projection(files, target)
    first_hashes = verify(files)
    second, second_files = render()
    if files != second_files:
        raise ValueError('Gateway input changed between builds')
    write_projection(second_files, target)
    second_hashes = verify(second_files)
    if first_hashes != second_hashes or before != _study_inventory(study):
        raise ValueError('Rebuild drift or Study modification detected')
    counts = Counter(record['source_id'] for record in second.snapshot.legacy_sources)
    return dict(source_counts=dict(sorted(counts.items())), source_total=sum(counts.values()),
                canonical_messages=second.history_item_count,
                wrong_answer_sources=second.wrong_answer_source_count,
                wrong_answer_analyses=second.wrong_answer_analysis_count,
                generated_files=len(files), study_files=len(before),
                study_markdown=sum(name.lower().endswith('.md') for name in before),
                read_back=True, manifest_reconciled=True, relative_links=True,
                deterministic_rebuild=True, study_unchanged=True, absolute_path_scan=True,
                max_path_length=max(len(str(target / path)) for path in files))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--vault', type=Path, required=True)
    args = parser.parse_args()
    try:
        report = build_projection(args.config, args.vault)
    except ProjectionWriteError as error:
        print(json.dumps({'error': 'projection_write_failed',
                          'recovery_required': error.recovery_required,
                          'cleanup_required': error.cleanup_required}), file=sys.stderr)
        raise SystemExit(1) from None
    except Exception:
        # Private titles, config paths and source text must never reach logs.
        raise SystemExit('Projection build failed; no private details logged') from None
    print(json.dumps(report, sort_keys=True))


if __name__ == '__main__':
    main()
