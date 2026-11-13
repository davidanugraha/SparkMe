"""Qwen-Agent adapter for a configured OpenAI-compatible model server."""
import json
import os

from ..judging.prompts import system_prompt, user_prompt


TEMPERATURE = 0
MAX_RETRIES = 0
DISABLE_INPUT_TRUNCATION = -1
UNAUTHENTICATED_API_KEY = 'EMPTY'


def run(session, options):
    try:
        from qwen_agent.agents import Assistant
        from qwen_agent.agents import fncall_agent
        from qwen_agent.tools.base import BaseTool
    except ImportError as exc:
        raise RuntimeError('Install requirements-qwen.txt in the environment running this command') from exc

    class QwenTool(BaseTool):
        def __init__(self, tool):
            self.name = tool.name
            self.description = tool.description
            # Qwen-Agent 0.0.34 requires exactly type/properties/required at the root.
            # Host-side validation still rejects extra arguments.
            self.parameters = {key: value for key, value in tool.parameters.items() if key != 'additionalProperties'}
            super().__init__()

        def call(self, params, **kwargs):
            try:
                # Native XML-style calls can represent a zero-argument tool with
                # empty argument text. Required fields are still checked by the host.
                args = {} if params is None or (isinstance(params, str) and not params.strip()) else (
                    json.loads(params) if isinstance(params, str) else params)
            except json.JSONDecodeError:
                args = None
            return json.dumps(session.dispatch(self.name, args), ensure_ascii=False)

    class TranscriptAssistant(Assistant):
        calls = 0

        def _call_llm(self, *args, **kwargs):
            if session.done:
                return
            if self.calls >= options.max_model_calls:
                raise RuntimeError('Model-call budget exhausted')
            if session.review_stage in ('turn', 'synthesis'):
                _check_context(kwargs.get('messages', args[0] if args else []),
                               kwargs.get('functions'), options.max_input_tokens)
            self.calls += 1
            yield from super()._call_llm(*args, **kwargs)

    generation = {'temperature': TEMPERATURE, 'max_retries': MAX_RETRIES, 'max_input_tokens': options.max_input_tokens,
                  'max_tokens': options.max_output_tokens}
    if session.review_stage in ('turn', 'synthesis'):
        # The SDK otherwise silently truncates history to its input limit.
        # Prefix tasks must retain the supplied history; fail the budget check instead.
        generation['max_input_tokens'] = DISABLE_INPUT_TRUNCATION
    if getattr(options, 'qwen_native_tools', False):
        generation['use_raw_api'] = True
    thinking = getattr(options, 'qwen_thinking', 'model')
    if thinking != 'model':
        generation['extra_body'] = {'chat_template_kwargs': {'enable_thinking': thinking == 'on'}}
    session.state['qwen_config'] = {'native_tools': getattr(options, 'qwen_native_tools', False), 'thinking': thinking,
                                    'base_url': options.base_url}
    session.persist()
    config = {'model': options.model, 'model_type': 'oai', 'model_server': options.base_url,
             'api_key': os.environ.get(options.api_key_env, UNAUTHENTICATED_API_KEY),
             'generate_cfg': generation}
    model = config
    if getattr(options, 'qwen_native_tools', False):
        model = _native_model(config)
    bot = TranscriptAssistant(
        llm=model,
        function_list=[QwenTool(tool) for tool in session.tools.values()], system_message=system_prompt(session))
    # The package defaults to 20 calls independently of our budget. This backend
    # runs in one dedicated worker; scope and restore its loop cap for this run.
    previous_cap = fncall_agent.MAX_LLM_CALL_PER_RUN
    fncall_agent.MAX_LLM_CALL_PER_RUN = options.max_model_calls
    try:
        for _ in bot.run(messages=[{'role': 'user', 'content': user_prompt(session)}]):
            if session.done:
                break
    finally:
        fncall_agent.MAX_LLM_CALL_PER_RUN = previous_cap
    if not session.done:
        raise RuntimeError('Qwen-Agent ended without a validated submit')


def _check_context(messages, functions, limit):
    from qwen_agent.utils.tokenization_qwen import tokenizer
    from qwen_agent.utils.utils import extract_text_from_message
    tokens = sum(tokenizer.count_tokens(str(m.function_call) if m.function_call else extract_text_from_message(m, add_upload_info=True))
                 for m in messages)
    tokens += tokenizer.count_tokens(json.dumps(functions or [], ensure_ascii=False))
    if tokens > limit:
        raise RuntimeError(f'Full review context exceeds --max-input-tokens ({tokens} estimated > {limit}); '
                           'increase the client/server context budget. History was not silently truncated.')


def _native_model(config):
    """Assemble native streams, including nullable and interleaved tool deltas.

    Qwen-Agent 0.0.34 assumes argument text is present in the first delta and
    accumulates only the last call. vLLM may send a name with null arguments
    first, and parallel calls may interleave by index. Adapt the transport
    while retaining Qwen-Agent's model, prompt, and tool loop.
    """
    from qwen_agent.llm.oai import TextChatAtOAI
    from qwen_agent.llm.schema import ASSISTANT, FunctionCall, Message

    class NativeToolModel(TextChatAtOAI):
        def _chat_stream(self, messages, delta_stream, generate_cfg):
            if delta_stream:
                raise ValueError('Native tool adapter requires accumulated streaming output')
            response = self._chat_complete_create(model=self.model,
                messages=self.convert_messages_to_dicts(messages), stream=True, **generate_cfg)
            content, reasoning, calls = '', '', {}
            try:
                for chunk in response:
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta
                    content += delta.content or ''
                    reasoning += getattr(delta, 'reasoning_content', None) or getattr(delta, 'reasoning', None) or ''
                    for fragment in delta.tool_calls or []:
                        index = fragment.index
                        call = calls.setdefault(index, {'id': '', 'name': '', 'arguments': ''})
                        if fragment.id:
                            call['id'] = fragment.id
                        if fragment.function is not None:
                            call['name'] += fragment.function.name or ''
                            call['arguments'] += fragment.function.arguments or ''
                    output = []
                    if reasoning:
                        output.append(Message(role=ASSISTANT, content='', reasoning_content=reasoning))
                    if content:
                        output.append(Message(role=ASSISTANT, content=content))
                    for index in sorted(calls):
                        call = calls[index]
                        output.append(Message(role=ASSISTANT, content='',
                            function_call=FunctionCall(name=call['name'], arguments=call['arguments']),
                            extra={'function_id': call['id'] or f'call_{index}'}))
                    yield output
            finally:
                close = getattr(response, 'close', None)
                if callable(close):
                    close()

    return NativeToolModel(config)
