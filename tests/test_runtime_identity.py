"""Runtime identity must distinguish restarts without inspecting private data."""
import json
import re
import os
import pytest

from cognivault.config import AppConfig
from cognivault.gateway import Gateway


def test_health_identifies_unconfigured_memory_without_opening_it(tmp_path):
    gateway = Gateway(AppConfig('0.8.0'), None)
    health = gateway.health_report()
    assert health['memory'] == {'configured': False, 'status': 'not_configured'}
    assert re.fullmatch('[0-9a-f]{32}', health['runtime']['instance_id'])
    assert re.fullmatch('[0-9a-f]{64}', health['runtime']['startup_source_sha256'])
    assert str(tmp_path) not in json.dumps(health)


def test_configured_memory_does_not_claim_ready_or_create_missing_store(tmp_path):
    from cognivault.adapters.memory import SQLiteMemoryStore
    database = tmp_path / 'private-memory.db'
    gateway = Gateway(AppConfig('0.8.0', memory_database=database), None,
                      memory_store=SQLiteMemoryStore(database))
    assert gateway.health_report()['memory'] == {'configured': True, 'status': 'not_checked'}
    assert not database.exists()


def test_runtime_identity_is_stable_until_gateway_reconstruction():
    first = Gateway(AppConfig('0.8.0'), None)
    first_identity = first.health_report()['runtime']
    assert first.health_report()['runtime'] == first_identity
    second_identity = Gateway(AppConfig('0.8.0'), None).health_report()['runtime']
    assert first_identity['instance_id'] != second_identity['instance_id']
    assert first_identity['startup_source_sha256'] == second_identity['startup_source_sha256']


def test_source_fingerprint_is_bounded_private_free_and_sensitive_to_code(tmp_path):
    from cognivault.runtime_identity import source_fingerprint
    package = tmp_path / 'package'
    package.mkdir()
    (package / 'module.py').write_text('x = 1\n', encoding='utf-8')
    digest = source_fingerprint(package)
    assert re.fullmatch('[0-9a-f]{64}', digest)
    # Configuration and user data are never part of the source identity.
    (package / 'private.db').write_text('PRIVATE', encoding='utf-8')
    (package / 'config.toml').write_text('PRIVATE', encoding='utf-8')
    assert source_fingerprint(package) == digest
    (package / 'module.py').write_text('x = 2\n', encoding='utf-8')
    assert source_fingerprint(package) != digest
    (package / 'large.py').write_bytes(b'x' * (512 * 1024 + 1))
    assert source_fingerprint(package) is None


def test_source_identity_unavailable_does_not_break_gateway(monkeypatch):
    import cognivault.runtime_identity as identity
    monkeypatch.setattr(identity, 'source_fingerprint', lambda root: None)
    health = Gateway(AppConfig('0.8.0'), None).health_report()
    assert health['runtime']['startup_source_sha256'] is None
    assert health['memory']['status'] == 'not_configured'


def test_source_fingerprint_refuses_linked_source(tmp_path):
    from cognivault.runtime_identity import source_fingerprint
    outside = tmp_path / 'outside.py'
    outside.write_text('PRIVATE', encoding='utf-8')
    package = tmp_path / 'package'
    package.mkdir()
    try:
        (package / 'linked.py').symlink_to(outside)
    except OSError:
        pytest.skip('symlink creation unavailable')
    assert source_fingerprint(package) is None


def test_source_fingerprint_refuses_hardlinked_source(tmp_path):
    from cognivault.runtime_identity import source_fingerprint
    package = tmp_path / 'package'
    package.mkdir()
    original = tmp_path / 'outside.py'
    original.write_text('PRIVATE', encoding='utf-8')
    os.link(original, package / 'linked.py')
    assert source_fingerprint(package) is None


def test_unreadable_subtree_cannot_produce_partial_fingerprint(tmp_path, monkeypatch):
    from cognivault.runtime_identity import source_fingerprint
    package = tmp_path / 'package'
    hidden = package / 'subtree'
    hidden.mkdir(parents=True)
    (package / 'module.py').write_text('x=1', encoding='utf-8')
    original_scandir = os.scandir
    def denied(path):
        if str(path) == str(hidden):
            raise PermissionError('PRIVATE FAILURE')
        return original_scandir(path)
    monkeypatch.setattr(os, 'scandir', denied)
    assert source_fingerprint(package) is None


def test_source_scan_bounds_non_python_entries(tmp_path):
    from cognivault.runtime_identity import source_fingerprint
    package = tmp_path / 'package'
    package.mkdir()
    (package / 'module.py').write_text('x=1', encoding='utf-8')
    for index in range(1025):
        (package / f'ignored-{index}.txt').touch()
    assert source_fingerprint(package) is None
