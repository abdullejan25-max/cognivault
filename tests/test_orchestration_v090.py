"""v0.9 fault injection: no production configuration or external model access."""
import asyncio
from dataclasses import replace
import json

import pytest

from cognivault.orchestration import Descriptor, Engine, FileJournal, Registry, SyntheticAdapter
from test_orchestration import setup, task


def test_cancel_at_commit_intent_never_starts_write(tmp_path):
    engine, handler, _ = setup(tmp_path)
    async def check():
        cancel = asyncio.Event()
        original = engine.journal.save
        def save(task_id, record):
            original(task_id, record)
            if record['state'] == 'COMMITTING':
                cancel.set()
        engine.journal.save = save
        receipt = await engine.run(task(), 'synthetic', cancel=cancel)
        assert handler.writes == 0
        assert receipt.state == 'PARTIAL'
    asyncio.run(check())


def test_no_agent_is_durable_blocked_receipt(tmp_path):
    engine, handler, _ = setup(tmp_path)
    engine.registry = Registry()
    receipt = asyncio.run(engine.run(task()))
    assert receipt.state == 'BLOCKED' and receipt.error_code == 'AGENT_UNAVAILABLE'
    assert not handler.writes
    assert engine.journal.load(task().task_id)['state'] == 'BLOCKED'


def test_structured_executor_errors_preserve_retry_and_blocking_semantics(tmp_path):
    from cognivault.orchestration.models import AdapterError
    for code, retryable, expected, attempts in [
        ('AUTH_REQUIRED', False, 'BLOCKED', 1),
        ('MODEL_REFUSED', False, 'FAILED', 1),
        ('NETWORK_ERROR', True, 'RETRYABLE_FAILURE', 2),
    ]:
        def fail(*args):
            raise AdapterError(code, retryable=retryable)
        engine, handler, _ = setup(tmp_path/code, callback=fail)
        receipt = asyncio.run(engine.run(task(), 'synthetic'))
        assert (receipt.state, receipt.error_code, receipt.attempts) == (expected, code, attempts)
        assert not handler.writes


def test_gateway_preparation_rejection_has_no_effects(tmp_path):
    from cognivault.contracts import GatewayError
    engine, _, _ = setup(tmp_path)
    async def denied(*args):
        raise GatewayError('PERMISSION_DENIED', 'Private backend detail')
    engine.handlers[task().kind].prepare = denied
    receipt = asyncio.run(engine.run(task(), 'synthetic'))
    assert receipt.state == 'FAILED' and receipt.error_code == 'PERMISSION_DENIED'
    assert 'Private backend' not in repr(receipt)


def test_permission_denial_after_write_keeps_partial_receipt(tmp_path):
    from cognivault.contracts import GatewayError
    engine, handler, _ = setup(tmp_path)
    async def denied(*args):
        raise GatewayError('PERMISSION_DENIED', 'Private backend detail')
    handler.verify = denied
    receipt = asyncio.run(engine.run(task(), 'synthetic'))
    assert handler.writes == 1
    assert receipt.state == 'PARTIAL' and receipt.error_code == 'PERMISSION_DENIED'
    assert not receipt.verified


def test_commit_rejection_does_not_assume_no_earlier_effects(tmp_path):
    from cognivault.contracts import GatewayError
    engine, handler, _ = setup(tmp_path)
    original = handler.commit
    async def partially_committed(*args):
        await original(*args)
        raise GatewayError('PERMISSION_DENIED', 'Later operation denied')
    handler.commit = partially_committed
    receipt = asyncio.run(engine.run(task(), 'synthetic'))
    assert handler.writes == 1 and receipt.state == 'PARTIAL' and not receipt.verified


def test_auth_preflight_does_not_exhaust_execution_budget(tmp_path):
    from cognivault.orchestration.models import AdapterError
    request = task(max_attempts=1)
    engine, handler, adapter = setup(tmp_path, request)
    async def unavailable():
        raise AdapterError('AUTH_REQUIRED')
    adapter.healthy = unavailable
    first = asyncio.run(engine.run(request, 'synthetic'))
    assert first.state == 'BLOCKED' and first.attempts == 0
    async def available():
        return True
    adapter.healthy = available
    second = asyncio.run(engine.run(request, 'synthetic'))
    assert second.state == 'COMPLETED' and handler.writes == 1


def test_blocked_execution_budget_never_stalls_at_authorized(tmp_path):
    from cognivault.orchestration.models import AdapterError
    request = task(max_attempts=1)
    def blocked(*args):
        raise AdapterError('AUTH_REQUIRED')
    engine, _, adapter = setup(tmp_path, request, blocked)
    assert asyncio.run(engine.run(request, 'synthetic')).state == 'BLOCKED'
    adapter.callback = lambda *_: None
    second = asyncio.run(engine.run(request, 'synthetic'))
    assert second.state == 'RETRYABLE_FAILURE' and second.error_code == 'ATTEMPTS_EXHAUSTED'


def test_disabled_retryable_agent_can_be_reenabled_without_invalid_transition(tmp_path):
    def unavailable(*args):
        raise ConnectionError()
    engine, _, _ = setup(tmp_path, callback=unavailable)
    assert asyncio.run(engine.run(task(), 'synthetic')).state == 'RETRYABLE_FAILURE'
    engine.registry.set_enabled('synthetic', False)
    assert asyncio.run(engine.run(task(), 'synthetic')).state == 'BLOCKED'
    engine.registry.set_enabled('synthetic', True)
    last = asyncio.run(engine.run(task(), 'synthetic'))
    assert last.state == 'RETRYABLE_FAILURE' and last.error_code == 'ATTEMPTS_EXHAUSTED'


def test_registry_routes_on_cost_latency_and_enabled_state(tmp_path):
    registry = Registry()
    for name, cost, latency in [('expensive',3,20), ('slow',1,200), ('fast',1,10)]:
        desc = Descriptor(name,'1',frozenset({'study.analyze'}),frozenset({'structured'}),'synthetic',
                          relative_cost=cost, latency_ms=latency)
        registry.register(SyntheticAdapter(desc, None))
    assert registry.select(task()) == 'fast'
    registry.set_enabled('fast', False)
    assert registry.describe('fast')['enabled'] is False
    assert registry.select(task()) == 'slow'


def test_plan_validates_dependencies_before_any_task_runs(tmp_path):
    from cognivault.orchestration.plans import PlanStep, run_plan
    engine, handler, _ = setup(tmp_path)
    one, two = task(), replace(task(), task_id='task-2')
    with pytest.raises(ValueError, match='INVALID_PLAN'):
        asyncio.run(run_plan(engine, [PlanStep(one, ('task-2',)), PlanStep(two, ('task-1',))]))
    assert handler.writes == 0


def test_plan_runs_independent_nodes_and_blocks_failed_dependency(tmp_path):
    from cognivault.orchestration.plans import PlanStep, run_plan
    engine, handler, _ = setup(tmp_path)
    first = replace(task(), task_id='first', grants=frozenset({'read'}))
    second, third = replace(task(), task_id='second'), replace(task(), task_id='third')
    receipt = asyncio.run(run_plan(engine, [PlanStep(first), PlanStep(second, ('first',)), PlanStep(third)]))
    assert receipt.state == 'PARTIAL'
    assert receipt.tasks['first'].state == 'FAILED'
    assert receipt.tasks['second'].state == 'BLOCKED'
    assert receipt.tasks['third'].state == 'COMPLETED'
    assert handler.writes == 1


def test_pre_v090_task_digest_is_unchanged():
    from cognivault.orchestration.models import fingerprint
    request = task()
    old = {**request.__dict__, 'grants':sorted(request.grants),
           'required_capabilities':sorted(request.required_capabilities)}
    assert request.digest() == fingerprint(old)


def test_plan_snapshots_inputs_before_first_await(tmp_path):
    from cognivault.orchestration.plans import PlanStep, run_plan
    from cognivault.orchestration import Result
    first, second = replace(task(), task_id='first'), replace(task(), task_id='second')
    observed = []
    def respond(request, prepared):
        if request.task_id == 'first':
            second.inputs['source_id'] = 'changed-after-validation'
        observed.append(request.inputs['source_id'])
        return Result(request.task_id, request.correlation_id, 'synthetic', '1',
                      {'answer':'PRIVATE_OUTPUT'}, .95, ('synthetic',))
    engine, _, _ = setup(tmp_path, callback=respond)
    asyncio.run(run_plan(engine, [PlanStep(first), PlanStep(second)]))
    assert observed == ['synthetic', 'synthetic']


def test_unassigned_routing_sentinel_is_not_a_registerable_agent():
    with pytest.raises(ValueError, match='RESERVED_AGENT_ID'):
        Registry().register(SyntheticAdapter(Descriptor('unassigned','1',frozenset({'study.analyze'}),
                             frozenset({'structured'}),'synthetic'), None))


def test_plan_propagates_partial_write_state(tmp_path):
    from cognivault.orchestration.plans import PlanStep, run_plan
    engine, handler, _ = setup(tmp_path)
    handler.fail_verify = True
    report = asyncio.run(run_plan(engine, [PlanStep(task())]))
    assert handler.writes == 1 and report.state == 'PARTIAL'


def test_plan_propagates_pending_business_review(tmp_path):
    from cognivault.orchestration.plans import PlanStep, run_plan
    engine, handler, _ = setup(tmp_path)
    original = handler.commit
    async def pending(*args):
        return {**await original(*args), 'requires_review':True}
    handler.commit = pending
    report = asyncio.run(run_plan(engine, [PlanStep(task())]))
    assert handler.writes == 1 and report.state == 'WAITING_FOR_REVIEW'
