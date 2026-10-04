from dataclasses import replace
from pathlib import Path

import pytest

from cognivault import obsidian_build
from cognivault.obsidian_writer import ProjectionWriteError
from test_projection_collector import _projection_gateway


def test_existing_unicode_vault_build_readback_and_user_note_preservation(tmp_path, monkeypatch):
    gateway = _projection_gateway(tmp_path, source_count=1, analysis_count=1)
    vault = tmp_path / '学习空间'
    (vault / '.obsidian').mkdir(parents=True)
    study = vault / '学习资料'
    study.mkdir()
    original = study / '中文 [标题] (第一章).md'
    original.write_text('# 中文学习资料\n原文保留', encoding='utf-8')
    gateway.config = replace(gateway.config, study_root=study)
    monkeypatch.setattr(obsidian_build, 'load_gateway_from_config', lambda config: gateway)
    report = obsidian_build.build_projection(Path('synthetic.toml'), vault)
    assert report['deterministic_rebuild'] and report['relative_links'] and report['study_unchanged']
    assert original.read_text(encoding='utf-8') == '# 中文学习资料\n原文保留'
    note = vault / 'V2Projection' / '我的笔记.md'
    note.write_text('user authored', encoding='utf-8')
    obsidian_build.build_projection(Path('synthetic.toml'), vault)
    assert note.read_text(encoding='utf-8') == 'user authored'


def test_build_rejects_study_outside_explicit_vault_before_projection_write(tmp_path, monkeypatch):
    gateway = _projection_gateway(tmp_path, source_count=1, analysis_count=1)
    vault = tmp_path / 'vault'
    (vault / '.obsidian').mkdir(parents=True)
    study = tmp_path / 'external-study'
    study.mkdir()
    gateway.config = replace(gateway.config, study_root=study)
    monkeypatch.setattr(obsidian_build, 'load_gateway_from_config', lambda config: gateway)
    with pytest.raises(ValueError):
        obsidian_build.build_projection(Path('synthetic.toml'), vault)
    assert not (vault / 'V2Projection').exists()


@pytest.mark.parametrize('location', ['.', 'V2Projection', 'V2Projection/nested'])
def test_build_rejects_authoritative_study_overlap_before_writing(tmp_path, monkeypatch, location):
    gateway = _projection_gateway(tmp_path, source_count=1, analysis_count=1)
    vault = tmp_path / 'vault'
    (vault / '.obsidian').mkdir(parents=True)
    study = vault / location
    study.mkdir(parents=True, exist_ok=True)
    gateway.config = replace(gateway.config, study_root=study)
    monkeypatch.setattr(obsidian_build, 'load_gateway_from_config', lambda config: gateway)
    with pytest.raises(ValueError):
        obsidian_build.build_projection(Path('synthetic.toml'), vault)
    assert not (vault / 'V2Projection' / '.projection-manifest.json').exists()


def test_cli_preserves_recovery_flags_without_logging_private_error(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr('sys.argv', ['build', '--config', 'synthetic.toml', '--vault', str(tmp_path)])
    def failed_build(*args):
        raise ProjectionWriteError(recovery_required=True, cleanup_required=True)
    monkeypatch.setattr(obsidian_build, 'build_projection', failed_build)
    with pytest.raises(SystemExit) as error:
        obsidian_build.main()
    assert error.value.code == 1
    output = capsys.readouterr().err
    assert '"recovery_required": true' in output
    assert '"cleanup_required": true' in output
    assert str(tmp_path) not in output
