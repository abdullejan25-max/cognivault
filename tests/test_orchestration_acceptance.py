import asyncio
import json
import re

import pytest


def test_default_acceptance_real_gateway_synthetic_executor(capsys):
    from cognivault.orchestration.acceptance import main
    assert main([]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report['status'] == 'PASS'
    assert report['execution_scope'] == 'synthetic'
    assert report['gateway_scope'] == 'isolated-sdk-gateway'
    assert report['desktop_host_evidence'] is False
    assert {r['kind'] for r in report['domains']} == {'study.analyze', 'history.summarize', 'memory.propose', 'evidence.retrieve'}
    assert all(r['verified'] for r in report['domains'])
    assert next(r for r in report['domains'] if r['kind'] == 'memory.propose')['state'] == 'WAITING_FOR_REVIEW'
    assert 'Synthetic original' not in json.dumps(report)


def test_live_without_credentials_is_local_blocked(monkeypatch, capsys):
    from cognivault.orchestration import acceptance
    monkeypatch.delenv('DEEPSEEK_API_KEY', raising=False)
    def forbidden_fixture(*args):
        pytest.fail('Missing credentials must stop before creating fixture or network work')
    monkeypatch.setattr(acceptance, '_fixture', forbidden_fixture)
    assert acceptance.main(['--live']) == 2
    report = json.loads(capsys.readouterr().out)
    assert report['status'] == 'BLOCKED'
    assert report['code'] == 'AUTH_REQUIRED'
    assert report['model_invocation_verified'] is False
    assert report['network_attempted'] is False


def test_acceptance_independent_readbacks_and_no_sensitive_output(monkeypatch, capsys):
    from cognivault.orchestration import acceptance
    calls = []
    original = acceptance.InProcessGatewayPort
    class RecordingPort(original):
        async def call(self, name, arguments):
            calls.append(name)
            return await super().call(name, arguments)
    monkeypatch.setattr(acceptance, 'InProcessGatewayPort', RecordingPort)
    callback = acceptance._synthetic
    def inspect_prepared(task, prepared):
        serialized = json.dumps(prepared)
        assert not re.search(r'[A-Za-z]:\\', serialized)
        assert 'Users' not in serialized
        assert 'cognivault-synthetic-acceptance-' not in serialized
        assert 'task_guidance' in prepared and 'expected_output_shape' in prepared
        if task.kind == 'study.analyze':
            assert 'write_provenance' not in serialized
            assert 'text_layer_status' not in serialized
            assert prepared['bundle']['source']['source_uri'].startswith('document://sha256/')
            assert prepared['material']['page']['text_origin']
            assert 'kinetic friction is 2 N left' in prepared['bundle']['source']['question_text']
        return callback(task, prepared)
    monkeypatch.setattr(acceptance, '_synthetic', inspect_prepared)
    assert acceptance.main([]) == 0
    report_text = capsys.readouterr().out
    assert calls.count('get_wrong_answer_bundle') >= 3
    assert calls.count('fetch_canonical_message') >= 6
    assert calls.count('fetch_memory_candidate') == 1
    assert calls.count('retrieve_evidence') >= 3
    for forbidden in ('My synthetic physics goal', 'force diagrams', '10 N', 'C:\\', 'Users', 'content_base64'):
        assert forbidden not in report_text


def test_domain_failure_does_not_skip_other_independent_domains(monkeypatch, capsys):
    from cognivault.orchestration import acceptance
    from cognivault.orchestration.models import AdapterError
    original = acceptance._synthetic
    def callback(task, prepared):
        if task.kind == 'study.analyze':
            raise AdapterError('INVALID_RESPONSE')
        return original(task, prepared)
    monkeypatch.setattr(acceptance, '_synthetic', callback)
    assert acceptance.main([]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report['status'] == 'FAIL'
    assert report['domains'][0]['state'] == 'FAILED'
    assert all(row['verified'] for row in report['domains'][1:])


def test_live_cannot_pass_without_external_execution_evidence(monkeypatch, capsys):
    from cognivault.orchestration import acceptance
    from cognivault.orchestration.adapters import SyntheticAdapter
    from cognivault.orchestration.models import Descriptor
    def fake_adapter(**kwargs):
        return SyntheticAdapter(Descriptor('synthetic-acceptance', '1', frozenset(acceptance.KINDS),
            frozenset({'structured','text'}), 'synthetic'), acceptance._synthetic)
    monkeypatch.setattr(acceptance, 'HttpsChatAdapter', fake_adapter)
    assert acceptance.main(['--live']) == 1
    report = json.loads(capsys.readouterr().out)
    assert report['status'] == 'FAIL'
    assert report['model_invocation_verified'] is False


def test_harness_requires_explicit_trusted_locations(capsys):
    from cognivault.orchestration.acceptance import main
    assert main(['--executor', 'harness']) == 2
    report = json.loads(capsys.readouterr().out)
    assert report['status'] == 'BLOCKED'
    assert report['code'] == 'EXECUTOR_BLOCKED'
    assert report['network_attempted'] is False


def test_private_gateway_config_argument_is_rejected():
    from cognivault.orchestration.acceptance import main
    with pytest.raises(SystemExit) as error:
        main(['--config', 'private.toml'])
    assert error.value.code == 2


def test_study_independent_readback_accepts_gateway_text_normalization(tmp_path):
    from cognivault.orchestration import acceptance
    from cognivault.orchestration.models import Task
    gateway, _, source, _ = acceptance._fixture(tmp_path)
    handler = acceptance.StudyAnalysisHandler(acceptance.InProcessGatewayPort(gateway))
    task = Task('multiline-study', 'multiline-correlation', 'study.analyze',
                {'source_id':source}, frozenset({'read','write'}))
    async def check():
        prepared = await handler.prepare(task)
        result = acceptance._synthetic(task, prepared)
        result.output['analysis']['reasoning'] = 'Compute net force.\n\nSubtract friction.  Then divide by mass.'
        receipt = await handler.commit(task, result, 'multiline-analysis')
        assert '\n' not in receipt['analysis']['reasoning']
        assert await handler.verify(task, result, receipt)
        receipt['analysis']['reasoning'] = 'Different reasoning'
        assert not await handler.verify(task, result, receipt)
    asyncio.run(check())
