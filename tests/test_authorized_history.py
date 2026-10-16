"""Invented inputs only; preparation never touches any configured V2 store."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest


SCRIPT = Path(__file__).parents[1] / "scripts" / "prepare_authorized_history.py"


def module():
    assert SCRIPT.is_file(), "authorized History preparation CLI is missing"
    spec = importlib.util.spec_from_file_location("authorized_history", SCRIPT)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def setup(tmp_path):
    source = tmp_path / "inputs"
    source.mkdir()
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    return source, inbox


def manifests(inbox, result):
    return [json.loads((inbox / m["relative_manifest"]).read_bytes()) for m in result["manifests"]]


def test_exact_bytes_private_manifest_and_replay(tmp_path):
    source, inbox = setup(tmp_path)
    raw = b'{"role":"user","content":"Invented confidential words"}\r\n'
    (source / "a.jsonl").write_bytes(raw)
    api = module()
    first = api.prepare([f"codex={source}"], inbox)
    second = api.prepare([f"codex={source}"], inbox)
    assert first == second
    assert first["prepared"] == 1
    m = manifests(inbox, first)[0]
    assert set(m) == {"schema_version", "entries"}
    entry = m["entries"][0]
    assert set(entry) == {"source_system", "source_format", "record_kind", "sha256", "byte_count", "relative_path"}
    assert entry["record_kind"] == "raw_session"
    assert (inbox / entry["relative_path"]).read_bytes() == raw
    assert entry["sha256"] == hashlib.sha256(raw).hexdigest()
    assert entry["byte_count"] == len(raw)
    assert str(source) not in json.dumps(first)
    assert "Invented confidential words" not in json.dumps(first)
    for ref in first["manifests"]:
        assert hashlib.sha256((inbox / ref["relative_manifest"]).read_bytes()).hexdigest() == ref["expected_manifest_sha256"]


def test_explicit_files_formats_exclusions_and_broken_tail(tmp_path):
    source, inbox = setup(tmp_path)
    (source / "good.jsonl").write_bytes(b'{}\n')
    (source / "broken.jsonl").write_bytes(b'{}\n{"incomplete":')
    (source / "history.db").write_bytes(b"not a database; do not open")
    (source / "auth.jsonl").write_bytes(b'{}\n')
    secrets = source / "credentials"
    secrets.mkdir()
    (secrets / "a.jsonl").write_bytes(b'{}\n')
    (source / "export.json").write_bytes(b'[]')
    api = module()
    result = api.prepare([f"codex={source}", f"chatgpt={source / 'export.json'}"], inbox)
    assert result["prepared"] == 2
    assert result["skipped"]["invalid_jsonl"] == 1
    assert {e["source_system"] for m in manifests(inbox, result) for e in m["entries"]} == {"codex", "chatgpt"}


def test_manifest_batches_and_category_identity(tmp_path):
    source, inbox = setup(tmp_path)
    for n in range(3):
        (source / f"{n}.jsonl").write_bytes(json.dumps({"n": n}).encode())
    api = module()
    result = api.prepare([f"codex={source}", f"workbuddy={source / '0.jsonl'}"], inbox, batch_size=2)
    batches = manifests(inbox, result)
    assert [len(m["entries"]) for m in batches] == [2, 2]
    assert result["prepared"] == 4
    assert len({e["relative_path"] for m in batches for e in m["entries"]}) == 3
    with pytest.raises(api.PreparationError):
        api.prepare([f"codex={source}"], inbox, batch_size=5001)


def test_unsafe_boundaries_are_rejected(tmp_path):
    source, inbox = setup(tmp_path)
    api = module()
    for descriptor, target in [(f"codex={inbox}", inbox), (f"claude={source}", inbox), (f"codex={source}", source / "nested")]:
        target.mkdir(exist_ok=True)
        with pytest.raises(api.PreparationError):
            api.prepare([descriptor], target)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    output = repo / "inbox"
    output.mkdir()
    with pytest.raises(api.PreparationError):
        api.prepare([f"codex={source}"], output)


def test_hardlink_source_is_never_read(tmp_path):
    source, inbox = setup(tmp_path)
    original = source / "a.jsonl"
    original.write_bytes(b'{}\n')
    (source / "alias.jsonl").hardlink_to(original)
    result = module().prepare([f"codex={source}"], inbox)
    assert result["prepared"] == 0
    assert result["skipped"]["unsafe_source"] == 2


def test_changed_source_is_skipped_and_not_published(tmp_path, monkeypatch):
    source, inbox = setup(tmp_path)
    original = source / "a.jsonl"
    original.write_bytes(b'{}\n')
    api = module()
    normal = api._hash_stream
    calls = 0

    def mutate_after_copy(stream, output=None):
        nonlocal calls
        result = normal(stream, output)
        calls += 1
        if calls == 1:
            original.write_bytes(b'{"changed":true}\n')
        return result

    monkeypatch.setattr(api, "_hash_stream", mutate_after_copy)
    result = api.prepare([f"codex={source}"], inbox)
    assert result["prepared"] == 0
    assert result["skipped"]["source_changed"] == 1
    assert not list((inbox / "authorized-history" / "files").glob("*.jsonl"))


def test_cli_output_is_sanitized(tmp_path, capsys):
    source, inbox = setup(tmp_path)
    (source / "a.jsonl").write_bytes(b'{"private":"Invented PRIVATE"}\n')
    api = module()
    assert api.main(["--source", f"codex={source}", "--inbox", str(inbox)]) == 0
    output = capsys.readouterr()
    assert "Invented PRIVATE" not in output.out + output.err
    assert str(source) not in output.out + output.err
    assert json.loads(output.out)["state"] == "PREPARED_ONLY"
    assert api.main(["--source", f"invalid={source}", "--inbox", str(inbox)]) == 2
    output = capsys.readouterr()
    assert str(source) not in output.out + output.err


def test_all_supported_sources_keep_original_formats(tmp_path):
    source, inbox = setup(tmp_path)
    (source / "session.jsonl").write_bytes(b'{}\n')
    (source / "hermes.json").write_bytes(b'{"messages":[]}')
    # The preparation boundary copies ZIP bytes; Gateway validates the actual
    # export contract later, so even this invented unsupported ZIP stays exact.
    (source / "export.zip").write_bytes(b"Invented ZIP evidence")
    result = module().prepare([f"codex={source / 'session.jsonl'}", f"workbuddy={source / 'session.jsonl'}",
                               f"hermes={source / 'hermes.json'}", f"chatgpt={source / 'export.zip'}"], inbox)
    entries = [e for m in manifests(inbox, result) for e in m["entries"]]
    assert {(e["source_system"], e["source_format"]) for e in entries} == {
        ("codex", "jsonl"), ("workbuddy", "jsonl"), ("hermes", "json"), ("chatgpt", "zip")}


def test_output_corruption_is_preserved_as_conflict(tmp_path):
    source, inbox = setup(tmp_path)
    (source / "a.jsonl").write_bytes(b'{}\n')
    api = module()
    first = api.prepare([f"codex={source}"], inbox)
    entry = manifests(inbox, first)[0]["entries"][0]
    saved = inbox / entry["relative_path"]
    saved.write_bytes(b"Synthetic corrupted existing snapshot")
    second = api.prepare([f"codex={source}"], inbox)
    assert second["prepared"] == 0
    assert second["skipped"]["output_conflict"] == 1
    assert saved.read_bytes() == b"Synthetic corrupted existing snapshot"


@pytest.mark.skipif(os.name != "nt", reason="Windows reparse-point safety")
def test_junction_inputs_and_output_are_not_followed(tmp_path):
    import _winapi
    source, inbox = setup(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "a.jsonl").write_bytes(b'{}\n')
    _winapi.CreateJunction(str(outside), str(source / "alias"))
    api = module()
    result = api.prepare([f"codex={source}"], inbox)
    assert result["prepared"] == 0
    assert result["skipped"]["excluded_directory"] == 1
    with pytest.raises(api.PreparationError):
        api.prepare([f"codex={source / 'alias'}"], inbox)
    _winapi.CreateJunction(str(outside), str(inbox / "bad-output"))
    with pytest.raises(api.PreparationError):
        api.prepare([f"codex={outside}"], inbox / "bad-output")


def test_argparse_failures_never_echo_private_values(capsys):
    api = module()
    assert api.main(["--source", "codex=C:/private/synthetic", "--inbox", "C:/private/inbox",
                     "--batch-size", "C:/private/sensitive"]) == 2
    output = capsys.readouterr()
    assert "C:/private" not in output.err + output.out


def test_same_identity_with_conflicting_formats_is_not_guessed(tmp_path):
    source, inbox = setup(tmp_path)
    (source / "same.json").write_bytes(b'{}\n')
    (source / "same.jsonl").write_bytes(b'{}\n')
    result = module().prepare([f"hermes={source}"], inbox)
    assert result["prepared"] == 0
    assert result["skipped"]["ambiguous_format"] == 2


def test_total_byte_budget_is_shared_across_source_roots(tmp_path, monkeypatch):
    source, inbox = setup(tmp_path)
    (source / "a.jsonl").write_bytes(b'{}\n')
    (source / "b.jsonl").write_bytes(b'{"b":1}\n')
    api = module()
    monkeypatch.setattr(api, "MAX_TOTAL_BYTES", 4, raising=False)
    with pytest.raises(api.PreparationError, match="total_byte_limit"):
        api.prepare([f"codex={source / 'a.jsonl'}", f"codex={source / 'b.jsonl'}"], inbox)
    assert not list((inbox / "authorized-history" / "manifests").glob("*.json"))


def test_global_entry_budget_covers_repeated_roots(tmp_path, monkeypatch):
    source, inbox = setup(tmp_path)
    (source / "a.jsonl").write_bytes(b'{}\n')
    (source / "b.jsonl").write_bytes(b'{"b":1}\n')
    api = module()
    monkeypatch.setattr(api, "MAX_ENTRIES", 1)
    with pytest.raises(api.PreparationError, match="entry_limit"):
        api.prepare([f"codex={source / 'a.jsonl'}", f"codex={source / 'b.jsonl'}"], inbox)


def test_cli_conflicts_return_nonzero_with_sanitized_summary(tmp_path, capsys):
    source, inbox = setup(tmp_path)
    (source / "a.jsonl").write_bytes(b'{}\n')
    api = module()
    first = api.prepare([f"codex={source}"], inbox)
    entry = manifests(inbox, first)[0]["entries"][0]
    (inbox / entry["relative_path"]).write_bytes(b"Synthetic corrupted bytes")
    assert api.main(["--source", f"codex={source}", "--inbox", str(inbox)]) == 1
    output = capsys.readouterr()
    assert json.loads(output.out)["state"] == "PREPARED_WITH_ERRORS"
    assert str(source) not in output.out + output.err


def stat_with(info, **changes):
    values = {name: getattr(info, name) for name in dir(info) if name.startswith("st_")}
    return SimpleNamespace(**(values | changes))


def test_stable_source_with_distinct_path_and_handle_ctime(tmp_path, monkeypatch):
    """Windows 3.12 path ctime can be birth time while fstat uses change time."""
    source, inbox = setup(tmp_path)
    original = source / "a.jsonl"
    raw = b'{"invented":true}\n'
    original.write_bytes(raw)
    api = module()
    fstat = os.fstat
    handle_ctime = original.lstat().st_ctime_ns + 1_000_000
    monkeypatch.setattr(api.os, "fstat", lambda fd: stat_with(fstat(fd), st_ctime_ns=handle_ctime))

    result = api.prepare([f"codex={original}"], inbox)

    assert result["prepared"] == 1
    assert result["skipped"].get("source_changed", 0) == 0
    entry = manifests(inbox, result)[0]["entries"][0]
    assert entry["sha256"] == hashlib.sha256(raw).hexdigest()
    assert (inbox / entry["relative_path"]).read_bytes() == raw


@pytest.mark.parametrize("field", ["st_dev", "st_ino", "st_size", "st_mtime_ns"])
def test_path_handle_identity_mismatch_is_still_rejected(tmp_path, monkeypatch, field):
    source, inbox = setup(tmp_path)
    original = source / "a.jsonl"
    original.write_bytes(b'{}\n')
    api = module()
    fstat = os.fstat

    def different_identity(fd):
        info = fstat(fd)
        return stat_with(info, **{field: getattr(info, field) + 1})

    monkeypatch.setattr(api.os, "fstat", different_identity)
    result = api.prepare([f"codex={original}"], inbox)
    assert result["prepared"] == 0
    assert result["skipped"]["source_changed"] == 1


@pytest.mark.parametrize("query", ["path", "handle"])
def test_each_api_keeps_its_own_ctime_stability_check(tmp_path, monkeypatch, query):
    source, inbox = setup(tmp_path)
    original = source / "a.jsonl"
    original.write_bytes(b'{}\n')
    api = module()
    fstat, safe_file = os.fstat, api._safe_file
    path_calls = handle_calls = 0
    path_ctime = original.lstat().st_ctime_ns

    def path_info(path):
        nonlocal path_calls
        info = safe_file(path)
        if path == original:
            path_calls += 1
            # prepare's size hint and snapshot's initial path check precede copy.
            return stat_with(info, st_ctime_ns=path_ctime + int(query == "path" and path_calls >= 3))
        return info

    def handle_info(fd):
        nonlocal handle_calls
        handle_calls += 1
        return stat_with(fstat(fd), st_ctime_ns=path_ctime + 1_000_000
                         + int(query == "handle" and handle_calls >= 2))

    monkeypatch.setattr(api, "_safe_file", path_info)
    monkeypatch.setattr(api.os, "fstat", handle_info)
    result = api.prepare([f"codex={original}"], inbox)
    assert result["prepared"] == 0
    assert result["skipped"]["source_changed"] == 1
    assert not list((inbox / "authorized-history" / "files").glob("*.jsonl"))


def test_second_hash_rejects_rewrite_even_when_metadata_appears_unchanged(tmp_path, monkeypatch):
    source, inbox = setup(tmp_path)
    original = source / "a.jsonl"
    original.write_bytes(b'{"x":1}\n')
    api = module()
    safe_file, hash_stream = api._safe_file, api._hash_stream
    path_info = original.lstat()
    with original.open("rb") as stream:
        handle_info = os.fstat(stream.fileno())
    hashes = 0

    def change_after_first_hash(stream, output=None):
        nonlocal hashes
        result = hash_stream(stream, output)
        if Path(stream.name) == original:
            hashes += 1
            if hashes == 1:
                original.write_bytes(b'{"x":2}\n')
        return result

    # Model coarse or restored timestamps without weakening production checks.
    monkeypatch.setattr(api, "_safe_file", lambda path: path_info if path == original else safe_file(path))
    monkeypatch.setattr(api.os, "fstat", lambda fd: handle_info)
    monkeypatch.setattr(api, "_hash_stream", change_after_first_hash)
    result = api.prepare([f"codex={original}"], inbox)
    assert hashes == 2
    assert result["prepared"] == 0
    assert result["skipped"]["source_changed"] == 1
    assert not list((inbox / "authorized-history" / "files").glob("*.jsonl"))
