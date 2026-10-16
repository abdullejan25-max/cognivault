"""Invented mathematics must survive public DTOs without exempting paths."""
import json

import anyio
import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from cognivault.gateway import _redact_local_paths
from cognivault.transports.mcp_stdio import create_mcp_server
from test_wrong_answers import analysis, gateway_with_sources


@pytest.mark.parametrize('expression', ['(p+r)/(q+r)', '+(p+r)/(q+r)', '(p-r) / (q+r)'])
def test_explicit_parenthesized_algebra_ratio_is_preserved(expression):
    assert _redact_local_paths(expression) == expression


@pytest.mark.parametrize('path', [
    '/private/archive.tar.gz', ')/private/archive.tar.gz',
    '(p+r)/private/archive.tar.gz', r'C:\Users\fictional\private',
    'C:/Users/fictional/private', r'\\server\share\private', '//server/share/private',
])
def test_math_exception_does_not_exempt_real_absolute_paths(path):
    public = _redact_local_paths(path)
    assert 'private' not in public and '[local path redacted]' in public


@pytest.mark.parametrize('suffix', ['/private', '.txt', r'\private'])
def test_path_continuation_is_not_treated_as_complete_math_ratio(suffix):
    public = _redact_local_paths('(p+r)/(q+r)' + suffix)
    assert '(p+r)/(q+r)' not in public
    assert '[local path redacted]' in public


@pytest.mark.parametrize('prefix', ['/private/', 'C:/private/', r'\\server\private' + '\\',
                                    '/private/notes ', '/private/(other) ',
                                    '/private/[notes] ', '/private/{notes} ',
                                    '/private/(notes) and ', 'C:/private/(notes) and ',
                                    r'\\server\private\(notes) and '])
def test_ratio_shaped_path_suffix_is_not_exempted(prefix):
    public = _redact_local_paths(prefix + '(p+r)/(q+r)')
    assert '(p+r)/(q+r)' not in public
    assert 'private' not in public


def test_prose_after_real_path_does_not_restore_math_exception():
    public = _redact_local_paths('/private/archive and (p+r)/(q+r)')
    assert '(p+r)/(q+r)' not in public


def test_math_ratio_immediately_after_chinese_prose_is_preserved():
    assert _redact_local_paths('设比值(p+r)/(q+r)') == '设比值(p+r)/(q+r)'


@pytest.mark.parametrize('expression', [
    '[(r₂+r₅)-(r₁+r₄)]/(6U²)',
    '7(z₂+z₄-z₁-z₃)/(6U²)',
    '(18²-6²)/(3×4)=24',
    '(wₙ²-6²)/(3×4)',
    '(18.5-6)/5=2.5',
    'r(p-q)/(q(q+r))',
])
def test_bounded_arithmetic_ratios_with_numeric_coefficients_and_scripts(expression):
    assert _redact_local_paths(expression) == expression


@pytest.mark.parametrize('value', [
    '(private)/(p+r)', '(p+r)/(private)', '(p+r)/5/private',
    '(p+r)/(3×4)/private', '/private/notes [(r₂+r₅)-(r₁+r₄)]/(6U²)',
    'private(p-q)/(q(q+r))', 'r(p-q)/(private(q+r))',
    '/private/r(p-q)/(q(q+r))', r'C:\private\r(p-q)/(q(q+r))',
    r'\\server\private\r(p-q)/(q(q+r))',
])
def test_extended_arithmetic_grammar_still_rejects_paths_and_words(value):
    public = _redact_local_paths(value)
    assert public != value and '[local path redacted]' in public


@pytest.mark.parametrize('expression', ['(p+r)/(q+r)', 'r(p-q)/(q(q+r))'])
def test_math_source_and_analysis_survive_register_bundle_and_search_dtos(tmp_path, expression):
    g, _, asset_uri, _ = gateway_with_sources(tmp_path)
    question = 'Invented ratio ' + expression
    registered = g.register_wrong_answer_source(asset_uri, question, expression)['source']
    assert registered['question_text'] == question and registered['student_answer'] == expression
    assert g.register_wrong_answer_source(asset_uri, question, expression)['source'] == registered
    body = {field: ([expression] if field == 'knowledge_points' else expression) for field in analysis()}
    saved = g.save_wrong_answer_analysis(registered['source_id'], body, [asset_uri], [], 'invented-ratio')['analysis']
    for field, expected in body.items():
        assert saved[field] == expected
    bundle = g.get_wrong_answer_bundle(registered['source_id'])
    assert bundle['source'] == registered
    for field, expected in body.items():
        assert bundle['analyses'][0][field] == expected
    assert g.search_wrong_answers('Invented ratio')['results'][0]['question_text'] == question


@pytest.mark.parametrize('expression', ['(p+r)/(q+r)', 'r(p-q)/(q(q+r))'])
def test_native_ratio_readback_preserves_math_and_still_redacts_adjacent_path(tmp_path, expression):
    g, _, asset_uri, _ = gateway_with_sources(tmp_path)
    async def run():
        async with create_connected_server_and_client_session(create_mcp_server(g)) as client:
            registered = (await client.call_tool('register_wrong_answer_source', {
                'source_uri': asset_uri, 'question_text': expression, 'student_answer': expression,
            })).structuredContent['source']
            assert registered['question_text'] == expression
            body = {**analysis(), 'reasoning': expression + ' and /private/archive.tar.gz'}
            saved = (await client.call_tool('save_wrong_answer_analysis', {
                'source_id': registered['source_id'], 'analysis': body,
                'source_refs': [asset_uri], 'study_relations': [], 'idempotency_key': 'native-invented-ratio',
            })).structuredContent['analysis']
            assert saved['reasoning'] == expression + ' and [local path redacted]'
            bundle = (await client.call_tool('get_wrong_answer_bundle', {
                'source_id': registered['source_id'],
            })).structuredContent['bundle']
            assert bundle['source']['question_text'] == expression
            assert bundle['analyses'][0]['reasoning'] == saved['reasoning']
            assert '/private' not in json.dumps(bundle)
    anyio.run(run)
