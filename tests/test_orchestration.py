"""Synthetic agents; no model credentials, production data or network."""
import asyncio
from dataclasses import replace
import json

import pytest

from cognivault.orchestration import (
    Descriptor, Engine, FileJournal, Registry, Result, SyntheticAdapter, Task,
)


class SimulatedCrash(BaseException):
    """Crash without asyncio.run's special KeyboardInterrupt handling."""


def task(**kw):
    return Task("task-1", "correlation-1", "study.analyze", {"source_id": "synthetic"},
                frozenset({"read", "write"}), **kw)


class Handler:
    required_grants = frozenset({"read", "write"})

    def __init__(self):
        self.writes = 0
        self.reads = 0
        self.keys = set()
        self.fail_verify = False

    async def prepare(self, request):
        return {"material": "PRIVATE_INPUT", "source_refs": ["synthetic"]}

    def validate(self, request, result, prepared):
        if result.output != {"answer": "PRIVATE_OUTPUT"} or result.evidence != ("synthetic",):
            raise ValueError("Untrusted output")

    async def commit(self, request, result, key):
        self.writes += key not in self.keys
        self.keys.add(key)
        return {"persisted": True, "sensitive": "PRIVATE_RECEIPT"}

    async def verify(self, request, result, receipt):
        self.reads += 1
        return not self.fail_verify


def setup(tmp_path, request=None, callback=None, descriptor=None):
    request = request or task()
    desc = descriptor or Descriptor("synthetic", "1", frozenset({request.kind}),
                                    frozenset({"text", "structured"}), "synthetic")
    def default(req, prepared):
        return Result(req.task_id, req.correlation_id, desc.agent_id, desc.version,
                      {"answer": "PRIVATE_OUTPUT"}, .95, ("synthetic",))
    adapter = SyntheticAdapter(replace(desc, data_scope='synthetic'), callback or default)
    if desc.data_scope != 'synthetic':
        # A stand-in for external policy tests; never presented as real execution.
        class PolicyProbe:
            calls = 0
            descriptor = desc
            async def healthy(self):
                raise AssertionError('External consent must precede any probe')
        adapter = PolicyProbe()
    registry = Registry()
    registry.register(adapter)
    handler = Handler()
    engine = Engine(registry, FileJournal(tmp_path), {request.kind: handler})
    return engine, handler, adapter


def run(engine, request=None, **kw):
    return asyncio.run(engine.run(request or task(), "synthetic", **kw))


def test_roundtrip_replay_and_metadata_privacy(tmp_path):
    engine, handler, adapter = setup(tmp_path)
    receipt = run(engine)
    assert receipt.state == "COMPLETED"
    assert receipt.attempts == 1
    assert receipt.output == {"answer": "PRIVATE_OUTPUT"}
    assert receipt.gateway_receipt["sensitive"] == "PRIVATE_RECEIPT"
    assert handler.writes == handler.reads == 1
    assert run(engine).state == "COMPLETED"
    assert handler.writes == adapter.calls == 1
    saved = "".join(p.read_text() for p in tmp_path.glob("*.json"))
    assert all(secret not in saved for secret in ("PRIVATE_INPUT", "PRIVATE_OUTPUT", "PRIVATE_RECEIPT"))
    assert engine.registry.describe("synthetic")["verified_kinds"] == ["study.analyze"]


@pytest.mark.parametrize("changes", [
    {"confidence": float("nan")}, {"confidence": True}, {"task_id": "other"},
    {"correlation_id": "other"}, {"adapter_version": "wrong"},
    {"output": {"tool": "create_memory", "capabilities": ["admin"]}},
    {"evidence": ("invented",)},
])
def test_untrusted_result_never_writes(tmp_path, changes):
    def respond(req, prepared):
        return replace(Result(req.task_id, req.correlation_id, "synthetic", "1",
                              {"answer": "PRIVATE_OUTPUT"}, .95, ("synthetic",)), **changes)
    engine, handler, _ = setup(tmp_path, callback=respond)
    assert run(engine).state == "FAILED"
    assert handler.writes == 0


@pytest.mark.parametrize("changes", [{"confidence": .1}, {"uncertainties": ("illegible",)}, {"evidence": ()}])
def test_review_blocks_write(tmp_path, changes):
    def respond(req, prepared):
        return replace(Result(req.task_id, req.correlation_id, "synthetic", "1",
                              {"answer": "PRIVATE_OUTPUT"}, .95, ("synthetic",)), **changes)
    engine, handler, _ = setup(tmp_path, callback=respond)
    assert run(engine).state == "WAITING_FOR_REVIEW"
    assert not handler.writes


def test_policy_checks_precede_agent(tmp_path):
    engine, handler, adapter = setup(tmp_path)
    denied = replace(task(), grants=frozenset({"read"}))
    assert run(engine, denied).error_code == "PERMISSION_DENIED"
    assert not adapter.calls


def test_external_transfer_requires_explicit_authorization(tmp_path):
    desc = Descriptor("synthetic", "1", frozenset({"study.analyze"}), frozenset({"text"}), "external")
    engine, handler, adapter = setup(tmp_path, descriptor=desc)
    assert run(engine).error_code == "EXTERNAL_TRANSFER_DENIED"
    assert not adapter.calls


def test_timeout_is_bounded_and_retryable(tmp_path):
    async def wait(req, prepared):
        await asyncio.sleep(10)
    engine, handler, adapter = setup(tmp_path, task(timeout_s=.01), wait)
    receipt = run(engine, task(timeout_s=.01))
    assert receipt.state == "RETRYABLE_FAILURE"
    assert receipt.attempts == 2
    assert not handler.writes
    assert run(engine, task(timeout_s=.01)).attempts == 2


def test_cancellation_before_commit(tmp_path):
    engine, handler, adapter = setup(tmp_path)
    async def check():
        cancel = asyncio.Event()
        cancel.set()
        return await engine.run(task(), "synthetic", cancel=cancel)
    assert asyncio.run(check()).state == "CANCELLED"
    assert not handler.writes


def test_same_id_different_input_conflicts(tmp_path):
    engine, _, _ = setup(tmp_path)
    run(engine)
    with pytest.raises(ValueError, match="CONFLICT"):
        run(engine, replace(task(), inputs={"source_id": "other"}))


def test_corrupt_journal_is_not_new_task(tmp_path):
    engine, _, _ = setup(tmp_path)
    run(engine)
    record = next(tmp_path.glob("*.json"))
    record.write_text("{bad", encoding="utf-8")
    with pytest.raises(ValueError, match="JOURNAL_CORRUPT"):
        run(engine)


def test_independent_verification_failure_is_partial(tmp_path):
    engine, handler, _ = setup(tmp_path)
    handler.fail_verify = True
    assert run(engine).state == "PARTIAL"
    assert handler.writes == 1


def test_write_crash_requires_same_candidate_and_deduplicates(tmp_path):
    engine, handler, adapter = setup(tmp_path)
    original = handler.commit
    async def crash(req, result, key):
        await original(req, result, key)
        raise SimulatedCrash()
    handler.commit = crash
    with pytest.raises(SimulatedCrash):
        run(engine)
    assert handler.writes == 1
    handler.commit = original
    assert run(engine).state == "WAITING_FOR_REVIEW"
    result = Result("task-1", "correlation-1", "synthetic", "1",
                    {"answer": "PRIVATE_OUTPUT"}, .95, ("synthetic",))
    assert run(engine, resume_result=replace(result, confidence=.96)).error_code == "RESUME_RESULT_MISMATCH"
    assert run(engine, resume_result=result).state == "COMPLETED"
    assert handler.writes == adapter.calls == 1


def test_concurrent_instances_cannot_duplicate_task(tmp_path):
    engine, handler, adapter = setup(tmp_path)
    async def execute():
        entered = asyncio.Event()
        release = asyncio.Event()
        async def callback(req, prepared):
            entered.set()
            await release.wait()
            return Result(req.task_id, req.correlation_id, "synthetic", "1",
                          {"answer": "PRIVATE_OUTPUT"}, .95, ("synthetic",))
        adapter.callback = callback
        first = asyncio.create_task(engine.run(task(), "synthetic"))
        await entered.wait()
        other = Engine(engine.registry, FileJournal(tmp_path), engine.handlers)
        with pytest.raises(ValueError, match="TASK_BUSY"):
            await other.run(task(), "synthetic")
        release.set()
        return await first
    assert asyncio.run(execute()).state == "COMPLETED"
    assert handler.writes == 1


@pytest.mark.parametrize("kw", [{"timeout_s": 0}, {"timeout_s": float("inf")},
                                {"max_attempts": 100}, {"min_confidence": float("nan")}])
def test_invalid_limits_rejected(kw):
    with pytest.raises(ValueError):
        task(**kw).validate()


def test_unavailable_agent_is_failed(tmp_path):
    engine, handler, _ = setup(tmp_path)
    receipt = asyncio.run(engine.run(task(), "missing"))
    assert receipt.state == "FAILED"
    assert receipt.error_code == "AGENT_UNAVAILABLE"
    assert handler.writes == 0


def test_empty_retrieval_is_valid_without_fabricated_evidence(tmp_path):
    def respond(req, prepared):
        return Result(req.task_id, req.correlation_id, "synthetic", "1", {"answer": "PRIVATE_OUTPUT"}, 1, ())
    engine, handler, _ = setup(tmp_path, callback=respond)
    handler.allow_empty_evidence = True
    handler.validate = lambda *_: None
    assert run(engine).state == "COMPLETED"


def test_verified_pending_task_replay_never_recommits(tmp_path):
    engine, handler, adapter = setup(tmp_path)
    original = handler.commit
    async def pending(req, result, key):
        return {**await original(req, result, key), "requires_review": True}
    handler.commit = pending
    assert run(engine).state == "WAITING_FOR_REVIEW"
    assert run(engine).verified is True
    assert handler.reads == adapter.calls == handler.writes == 1


def test_verify_crash_recovers_in_one_attempt(tmp_path):
    engine, handler, _ = setup(tmp_path)
    async def crash(*args):
        raise SimulatedCrash()
    handler.verify = crash
    with pytest.raises(SimulatedCrash):
        run(engine)
    handler.verify = lambda *args: asyncio.sleep(0, result=True)
    result = Result("task-1", "correlation-1", "synthetic", "1", {"answer": "PRIVATE_OUTPUT"}, .95, ("synthetic",))
    assert run(engine, resume_result=result).state == "COMPLETED"


def test_cancel_while_waiting_on_uncertain_write_is_partial(tmp_path):
    engine, handler, _ = setup(tmp_path)
    async def crash(*args):
        raise SimulatedCrash()
    handler.commit = crash
    with pytest.raises(SimulatedCrash):
        run(engine)
    assert run(engine).state == "WAITING_FOR_REVIEW"
    async def cancel():
        event = asyncio.Event()
        event.set()
        return await engine.run(task(), "synthetic", cancel=event)
    assert asyncio.run(cancel()).state == "PARTIAL"


@pytest.mark.parametrize("mutate", [
    lambda r: r.update(state="COMPLETED", verified=False),
    lambda r: r.update(attempts=-2),
    lambda r: r.update(commit_intent=False),
    lambda r: r.update(events=["RECEIVED", "COMPLETED"]),
    lambda r: r.update(schema=True),
])
def test_valid_json_but_invalid_journal_rejected(tmp_path, mutate):
    engine, _, _ = setup(tmp_path)
    run(engine)
    file = next(tmp_path.glob("*.json"))
    record = json.loads(file.read_text())
    mutate(record)
    file.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(ValueError, match="JOURNAL_CORRUPT"):
        run(engine)


def test_partial_readback_can_resume_after_missing_candidate(tmp_path):
    engine, handler, _ = setup(tmp_path)
    base = handler.commit
    partial = True
    async def commit(*args):
        return {**await base(*args), "partial": partial}
    handler.commit = commit
    assert run(engine).state == "PARTIAL"
    assert run(engine).state == "WAITING_FOR_REVIEW"
    partial = False
    result = Result("task-1", "correlation-1", "synthetic", "1", {"answer": "PRIVATE_OUTPUT"}, .95, ("synthetic",))
    assert run(engine, resume_result=result).state == "COMPLETED"


def test_cooperative_cancellation_cleanup_finishes_before_retry(tmp_path):
    async def check():
        active = 0
        peak = 0
        async def slow(req, prepared):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            try:
                await asyncio.sleep(100)
            finally:
                await asyncio.sleep(.02)
                active -= 1
        engine, _, adapter = setup(tmp_path, task(timeout_s=.01), slow)
        receipt = await engine.run(task(timeout_s=.01), "synthetic")
        assert active == 0
        assert peak == 1
        assert adapter.calls == 2
        assert receipt.state == "RETRYABLE_FAILURE"
    asyncio.run(check())


def test_stalled_cancellation_stops_retry_and_is_observed(tmp_path):
    async def check():
        async def stubborn(req, prepared):
            try:
                await asyncio.sleep(100)
            except asyncio.CancelledError:
                await asyncio.sleep(.3)
                raise RuntimeError("PRIVATE_UNSAFE_ERROR")
        engine, _, adapter = setup(tmp_path, task(timeout_s=.01), stubborn)
        receipt = await engine.run(task(timeout_s=.01), "synthetic")
        assert receipt.state == "FAILED"
        assert receipt.error_code == "CANCELLATION_STALLED"
        assert adapter.calls == 1
        await asyncio.sleep(.35)
        assert not engine.pending_operations
    asyncio.run(check())


def test_artifact_identifiers_survive_receipt_replay(tmp_path):
    engine, handler, _ = setup(tmp_path)
    saved = {'analysis_id':'wrong-analysis://sha256/'+'a'*64,
             'source_id':'wrong-answer://sha256/'+'b'*64, 'version':1}
    async def commit(*args):
        return {'analysis':{**saved, 'reasoning':'PRIVATE_OUTPUT'}}
    handler.commit = commit
    assert run(engine).artifacts == (saved,)
    assert run(engine).artifacts == (saved,)


def test_registry_selects_local_capable_agent_without_external_consent():
    registry = Registry()
    for name, scope, capabilities in [('remote','external',{'image','structured'}),
                                      ('text','local',{'structured'}), ('vision','local',{'image','structured'})]:
        from types import SimpleNamespace
        registry.register(SimpleNamespace(descriptor=Descriptor(name,'1',frozenset({'study.analyze'}),frozenset(capabilities),scope)))
    request = replace(task(), required_capabilities=frozenset({'image','structured'}))
    assert registry.select(request) == 'vision'


def test_engine_selects_registered_agent(tmp_path):
    engine, _, _ = setup(tmp_path)
    assert asyncio.run(engine.run(task())).state == 'COMPLETED'


def test_capability_mismatch_prevents_material_read(tmp_path):
    request = replace(task(), required_capabilities=frozenset({'image','structured'}))
    engine, _, adapter = setup(tmp_path)
    receipt = run(engine, request)
    assert receipt.error_code == 'CAPABILITY_UNAVAILABLE'
    assert adapter.calls == 0


def test_registered_version_change_cannot_inherit_verified_status(tmp_path):
    engine, _, adapter = setup(tmp_path)
    run(engine)
    adapter.descriptor = replace(adapter.descriptor, version='2')
    with pytest.raises(ValueError, match='AGENT_DESCRIPTOR_CHANGED'):
        engine.registry.describe('synthetic')


def test_file_lock_is_cross_process_and_released_after_exit(tmp_path):
    import subprocess
    import sys
    script = ('import sys; from cognivault.orchestration import FileJournal; '
              'j=FileJournal(sys.argv[1]); '
              'ctx=j.lock("task-1"); ctx.__enter__(); print("locked",flush=True); input()')
    child = subprocess.Popen([sys.executable, '-c', script, str(tmp_path)],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == 'locked'
        with pytest.raises(ValueError, match='TASK_BUSY'):
            with FileJournal(tmp_path).lock('task-1'):
                pass
        child.stdin.write('\n')
        child.stdin.flush()
        child.wait(timeout=10)
        with FileJournal(tmp_path).lock('task-1'):
            pass
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=10)


@pytest.mark.parametrize('malformed', [{'ok':True}, 'claimed success', [], None])
def test_malformed_response_object_returns_failure_receipt(tmp_path, malformed):
    engine, handler, _ = setup(tmp_path, callback=lambda *_: malformed)
    receipt = run(engine)
    assert receipt.state in {'FAILED', 'RETRYABLE_FAILURE'}
    assert not handler.writes


def test_synthetic_adapter_cannot_claim_real_execution():
    descriptor = Descriptor('fake-real', '1', frozenset({'study.analyze'}), frozenset({'structured'}), 'external')
    with pytest.raises(ValueError, match='SYNTHETIC_SCOPE_REQUIRED'):
        SyntheticAdapter(descriptor, None)


def test_many_explicit_recoveries_do_not_corrupt_valid_journal(tmp_path):
    engine, handler, _ = setup(tmp_path)
    handler.fail_verify = True
    run(engine)
    file = next(tmp_path.glob('*.json'))
    record = json.loads(file.read_text())
    record['events'] += ['COMMITTING','VERIFYING','PARTIAL'] * 90
    engine.journal.save('task-1', record)
    assert run(engine).state == 'WAITING_FOR_REVIEW'


def test_journal_capacity_rejection_preserves_last_valid_record(tmp_path):
    engine, _, _ = setup(tmp_path)
    run(engine)
    record = engine.journal.load('task-1')
    with pytest.raises(ValueError, match='JOURNAL_CAPACITY'):
        engine.journal.save('task-1', {**record, 'oversized':'x'*100_001})
    assert run(engine).state == 'COMPLETED'
