from pathlib import Path


LOCAL_FUNCTION_PATH = (
    Path(__file__).resolve().parents[4] / 'tools' / 'openwebui' / 'functions' / 'agentscope_alias.py'
)


def _load_pipe_class():
    namespace = {}
    exec(
        compile(LOCAL_FUNCTION_PATH.read_text(), str(LOCAL_FUNCTION_PATH), 'exec'),
        namespace,
    )
    return namespace['Pipe']


def test_repo_managed_agentscope_alias_source_exists_and_compiles():
    assert LOCAL_FUNCTION_PATH.exists()
    pipe_cls = _load_pipe_class()
    assert pipe_cls.__name__ == 'Pipe'


def test_normalize_model_strips_manifold_prefix():
    pipe = _load_pipe_class()()
    assert (
        pipe._normalize_model('agentscope_alias.ZenMuxOAI/openai/gpt-5.4')
        == 'ZenMuxOAI/openai/gpt-5.4'
    )
    assert (
        pipe._normalize_model('alias/ZenMuxOAI/openai/gpt-5.4')
        == 'ZenMuxOAI/openai/gpt-5.4'
    )


def test_extract_text_from_responses_payload_supports_runtime_wrapped_shape():
    pipe = _load_pipe_class()()
    payload = {
        'type': 'response.completed',
        'response': {
            'output': [
                {
                    'type': 'message',
                    'content': [
                        {'type': 'output_text', 'text': 'ALIAS_OK'},
                    ],
                }
            ]
        },
    }
    assert pipe._extract_text_fragments(payload) == ['ALIAS_OK']


def test_extract_text_from_responses_payload_supports_output_text_top_level():
    pipe = _load_pipe_class()()
    payload = {
        'type': 'response.completed',
        'response': {
            'output_text': 'ALIAS_OK',
            'output': [],
        },
    }
    assert pipe._extract_text_fragments(payload) == ['ALIAS_OK']


def test_extract_text_from_responses_payload_supports_text_value_shape():
    pipe = _load_pipe_class()()
    payload = {
        'type': 'response.completed',
        'response': {
            'output': [
                {
                    'type': 'message',
                    'content': [
                        {
                            'type': 'output_text',
                            'text': {'value': 'ALIAS_OK'},
                        }
                    ],
                }
            ]
        },
    }
    assert pipe._extract_text_fragments(payload) == ['ALIAS_OK']


def test_collect_text_from_stream_aggregates_runtime_sse_deltas():
    pipe = _load_pipe_class()()

    class _Resp:
        def iter_lines(self, decode_unicode=True):
            yield 'data: {"type":"response.output_text.delta","delta":"ALIAS_"}'
            yield 'data: {"type":"response.output_text.delta","delta":"OK"}'
            yield 'data: [DONE]'

    assert pipe._collect_text_from_stream(_Resp()) == 'ALIAS_OK'


def test_collect_text_from_stream_does_not_duplicate_completed_payload_after_deltas():
    pipe = _load_pipe_class()()

    class _Resp:
        def iter_lines(self, decode_unicode=True):
            yield 'data: {"type":"response.output_text.delta","delta":"ALIAS_"}'
            yield 'data: {"type":"response.output_text.delta","delta":"OK"}'
            yield 'data: {"type":"response.completed","response":{"output_text":"ALIAS_OK"}}'
            yield 'data: [DONE]'

    assert pipe._collect_text_from_stream(_Resp()) == 'ALIAS_OK'
