"""Supplemental mutable-domain backups never masquerade as full Study backups."""
from dataclasses import replace
import json
import pytest
from test_recovery import setup
from cognivault.runtime import load_gateway_from_config


def test_explicit_supplement_preserves_history_and_never_reads_study(tmp_path, monkeypatch):
    from cognivault.recovery import service
    g = setup(tmp_path)
    g.config = replace(g.config, recovery_include_study=False)
    original = tmp_path / 'gateway.toml'
    original.write_text('invented original profile')
    g.config = replace(g.config, gateway_config_file=original)
    before = (g.config.study_root / 'invented.txt').read_bytes()
    original_files = service.files

    def bounded_files(root, **kwargs):
        assert root != g.config.study_root
        return original_files(root, **kwargs)

    monkeypatch.setattr(service, 'files', bounded_files)
    plan = g.recovery_snapshot_plan()
    assert plan['configured_study'] is False
    assert 'study' not in plan['component_bytes']
    snapshot = g.create_recovery_snapshot('invented-supplement')
    assert snapshot['scope'] == 'mutable_domains_supplement'
    restored = g.verify_recovery_snapshot('invented-supplement', restore=True)
    assert restored['isolated_restore_verified'] and restored['ledger_verified']
    assert restored['study_read_verified'] is False
    assert restored['study_restore_state'] == 'excluded_by_configuration'
    assert restored['canonical'] == snapshot['canonical']
    assert restored['domain_readback']['documents']['readback_verified']
    assert (g.config.study_root / 'invented.txt').read_bytes() == before
    assert not (g.config.recovery_root / 'restores/invented-supplement/payload/study').exists()


@pytest.mark.parametrize('value', ['false', 0, None])
def test_runtime_rejects_nonboolean_supplement_scope(tmp_path, value):
    cfg = tmp_path / 'gateway.toml'
    scope = 'include_study=' + ('0' if value == 0 else json.dumps(value)) + '\n'
    cfg.write_text('[gateway]\nversion="0.6.0"\n[study]\nroot=' + json.dumps(str(tmp_path)) +
                   '\nqmd_collection="studyvault"\nqmd_version="2.8.3"\nqmd_executable="invented"\n'
                   '[history]\nbackend="not_configured"\n[recovery]\nroot=' +
                   json.dumps(str(tmp_path / 'recovery')) + '\n' + scope)
    with pytest.raises(ValueError):
        load_gateway_from_config(cfg)


def test_runtime_supplement_requires_explicit_false_and_defaults_full(tmp_path):
    cfg = tmp_path / 'gateway.toml'
    raw = '[gateway]\nversion="0.6.0"\n[study]\nroot=' + json.dumps(str(tmp_path)) + \
          '\nqmd_collection="studyvault"\nqmd_version="2.8.3"\nqmd_executable="invented"\n' + \
          '[history]\nbackend="not_configured"\n[recovery]\nroot=' + json.dumps(str(tmp_path / 'recovery')) + '\n'
    cfg.write_text(raw)
    assert load_gateway_from_config(cfg).config.recovery_include_study is True
    cfg.write_text(raw + 'include_study=false\n')
    assert load_gateway_from_config(cfg).config.recovery_include_study is False
