"""Offline invariants and actual Qwen-Agent integration against scripted HTTP."""
import importlib.util
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from evaluation.agentic_judge.backends.agent import Session
from evaluation.agentic_judge.backends.codex import run as codex_run

ROOT = Path(__file__).resolve().parents[1]
METRIC = json.loads((ROOT / 'examples/grounded_follow_up.json').read_text())


TOOL_CALL_BUDGET = 30
GUIDED_TOOL_CALL_BUDGET = 50
EXECUTABLE_MODE = 0o700
TASK_DEADLINE_SECONDS = 0.5
SUBPROCESS_TIMEOUT_SECONDS = 10
QWEN_INPUT_TOKEN_BUDGET = 12_000
QWEN_OUTPUT_TOKEN_BUDGET = 1_000


class HarnessTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.transcript = self.root / 'input.txt'
        self.transcript.write_bytes(b'Interviewer: What happened?\r\nAlice: A date was invented.\r\nResearcher: What date?\r\n')
        self.output = self.root / 'run'
        self.output.mkdir()

    def session(self, mode='extract', budget=TOOL_CALL_BUDGET):
        return Session(self.transcript, METRIC, self.output, mode, budget)

    def test_evidence_provenance_revision_and_persistence(self):
        s = self.session()
        args = dict(start=2, end=3, interpretation='Grounded in preceding answer', kind='supporting')
        self.assertFalse(s.dispatch('save_evidence', args)['ok'])
        s.dispatch('read_lines', dict(start=1, end=3))
        evidence = s.dispatch('save_evidence', args)['data']
        self.assertEqual(evidence['quote'].encode(), b'Alice: A date was invented.\r\nResearcher: What date?\r\n')
        s.dispatch('save_evidence', {**args, 'evidence_id': 'E1', 'kind': 'uncertain'})
        saved = json.loads((self.output / 'result.json').read_text())
        self.assertEqual(len(saved['evidence']), 1)
        self.assertEqual(saved['evidence'][0]['kind'], 'uncertain')
        self.assertEqual(saved['status'], 'running')
        s.dispatch('remove_evidence', {'evidence_id': 'E1'})
        self.assertEqual(s.evidence, {})

    def test_evaluation_requires_full_read_and_valid_score(self):
        s = self.session('evaluate')
        final = {'summary': 'Mixed quality', 'limitations': '', 'score': 3}
        self.assertFalse(s.dispatch('submit', final)['ok'])
        s.dispatch('search', {'text': 'date'})
        self.assertFalse(s.dispatch('submit', final)['ok'])
        s.dispatch('read_lines', {'start': 1, 'end': 3})
        for score in [99, float('nan')]:
            self.assertFalse(s.dispatch('submit', {**final, 'score': score})['ok'])
        self.assertTrue(s.dispatch('submit', final)['ok'])
        self.assertEqual(json.loads((self.output / 'result.json').read_text())['status'], 'completed')
        self.assertFalse(s.dispatch('remove_evidence', {'evidence_id': 'E1'})['ok'])

    def test_invalid_tools_and_budget(self):
        s = self.session(budget=4)
        for name, args in [('bash', {'command': 'ls'}), ('read_lines', {'start': True, 'end': 2}),
                           ('read_lines', {'start': 0, 'end': 2}), ('search', 'not an object')]:
            self.assertFalse(s.dispatch(name, args)['ok'])
        with self.assertRaisesRegex(RuntimeError, 'budget'):
            s.dispatch('list_evidence', {})

    def test_backend_return_without_submission_is_incomplete(self):
        from evaluation.agentic_judge.runner import run_agent
        metric = self.root / 'metric.json'
        metric.write_text(json.dumps(METRIC))
        options = SimpleNamespace(transcript=self.transcript, metric=metric, output=self.output,
                                  mode='extract', max_tools=10, backend='codex', model=None)
        with patch('evaluation.agentic_judge.backends.codex.run', return_value=None):
            self.assertEqual(run_agent({'options': vars(options), 'transcript': str(self.transcript), 'metric': METRIC,
                                     'output': str(self.output), 'source_hash': hashlib.sha256(self.transcript.read_bytes()).hexdigest(),
                                     'stage': 'full'}), 1)
        saved = json.loads((self.output / 'result.json').read_text())
        self.assertEqual(saved['status'], 'incomplete')
        self.assertNotIn('submission', saved)

    def fake_codex(self, body):
        path = self.root / 'fake-codex'
        path.write_text('#!' + sys.executable + '\n' + body)
        path.chmod(EXECUTABLE_MODE)
        return str(path)

    def test_codex_protocol_and_validated_submission(self):
        executable = self.fake_codex('''import json, sys
calls = iter([('read_lines', {'start':1,'end':3}), ('save_evidence', {'start':2,'end':3,'kind':'supporting','interpretation':'Grounded follow-up'}), ('submit', {'summary':'See E1','limitations':''})])
def send(value):
 print(json.dumps(value), flush=True)
def next_call():
 try:
  name, args = next(calls)
  send({'id':100,'method':'item/tool/call','params':{'tool':name,'arguments':args}})
 except StopIteration:
  pass
for line in sys.stdin:
 msg=json.loads(line)
 if msg.get('method') == 'initialize':
  send({'id':msg['id'],'result':{}})
 elif msg.get('method') == 'thread/start':
  assert len(msg['params']['dynamicTools']) == 6
  send({'id':msg['id'],'result':{'thread':{'id':'test'}}})
 elif msg.get('method') == 'turn/start':
  send({'id':msg['id'],'result':{'turn':{'id':'turn'}}})
  next_call()
 elif msg.get('id') == 100:
  assert msg['result']['success']
  next_call()
''')
        s = self.session()
        codex_run(s, SimpleNamespace(codex_executable=executable, model=None))
        self.assertTrue(s.done)
        self.assertEqual(len(s.evidence), 1)

    def test_cli_timeout_retains_incomplete_result(self):
        executable = self.fake_codex('import time\ntime.sleep(60)\n')
        metric = self.root / 'metric.json'
        metric.write_text(json.dumps(METRIC))
        output = self.root / 'timed-out'
        result = subprocess.run([sys.executable, '-m', 'evaluation.agentic_judge.runner', '--mode', 'extract',
                                 '--backend', 'codex', '--codex-executable', executable,
                                 '--transcript', str(self.transcript), '--metric', str(metric),
                                 '--output', str(output), '--timeout', str(TASK_DEADLINE_SECONDS)], capture_output=True, timeout=SUBPROCESS_TIMEOUT_SECONDS)
        self.assertEqual(result.returncode, 1)
        saved = json.loads((output / '0001-input/result.json').read_text())
        self.assertEqual(saved['status'], 'incomplete')
        self.assertIn('deadline', saved['error'])
        self.assertIn('(incomplete)', (output / '0001-input/evidence.md').read_text())

    @unittest.skipUnless(importlib.util.find_spec('qwen_agent'), 'Install requirements-qwen.txt for Qwen integration')
    def test_actual_qwen_agent_with_scripted_http_server(self):
        self.run_qwen_scenario('evaluate')

    @unittest.skipUnless(importlib.util.find_spec('qwen_agent'), 'Install requirements-qwen.txt for Qwen integration')
    def test_actual_qwen_review_with_scripted_http_server(self):
        self.run_qwen_scenario('review')

    @unittest.skipUnless(importlib.util.find_spec('qwen_agent'), 'Install requirements-qwen.txt for Qwen integration')
    def test_qwen_review_can_exceed_library_default_twenty_calls(self):
        self.run_qwen_scenario('review_long')

    @unittest.skipUnless(importlib.util.find_spec('qwen_agent'), 'Install requirements-qwen.txt for Qwen integration')
    def test_qwen_review_native_server_tools(self):
        self.run_qwen_scenario('review_native')

    @unittest.skipUnless(importlib.util.find_spec('qwen_agent'), 'Install requirements-qwen.txt for Qwen integration')
    def test_qwen_review_guide_native_server_tools(self):
        self.run_qwen_scenario('review_guide_native')

    def run_qwen_scenario(self, mode):
        from evaluation.agentic_judge.backends.qwen_agent import run
        calls = [('read_lines', {'start': 1, 'end': 3}), ('search', {'text': 'date'}),
                 ('save_evidence', {'start': 2, 'end': 3, 'kind': 'supporting', 'interpretation': 'Specific follow-up'}),
                 ('submit', {'summary': 'See E1', 'limitations': '', 'score': 4})]
        if mode.startswith('review'):
            from evaluation.agentic_judge.judging.turns import CHECKS, profile
            guide = {'title': 'Work information', 'items': [
                {'id': 'role', 'description': 'Participant work', 'required': True, 'grounding': 'first_hand'}
            ]} if mode == 'review_guide_native' else None
            s = Session(self.transcript, profile('Understand work', ['Interviewer', 'Researcher'], guide), self.output, 'review', GUIDED_TOOL_CALL_BUDGET)
            calls = [('review_overview', {}), ('read_lines', {'start': 1, 'end': 3})]
            for turn in s.state['interviewer_turns']:
                response = turn['response']
                calls.append(('save_turn_review', {
                    'turn_id': turn['turn_id'],
                    'checks': [{'check_id': name, 'finding': 'not_applicable' if name == 'experiential_grounding' and not guide else 'no_observed_violation',
                                'guide_item_ids': ['role'] if name == 'experiential_grounding' and guide else [],
                                'reason': 'No breach in this wording.', 'evidence': [{'start': turn['start'], 'end': turn['end']}]} for name in CHECKS],
                    'utility': {'information_gain': 'adds_information' if response else 'no_substantive_response',
                                'account': 'Relevant work detail.' if response else 'No answer follows.',
                                'evidence': [response] if response else [], 'limitations': 'Text only.'}}))
            if guide:
                calls.append(('save_guide_review', {'item_id': 'role', 'coverage': 'covered',
                    'finding': 'no_observed_violation', 'reason': 'Participant gives work information.',
                    'evidence': [{'start': 2, 'end': 2}], 'missing_information': '', 'limitations': 'Scripted test.'}))
            calls.append(('submit', {'summary': 'Reviewed both turns', 'utility_summary': 'One response adds detail.',
                                    'conduct_summary': 'No observed violations in the scripted findings.', 'limitations': 'Scripted test.'}))
        else:
            s = self.session('evaluate')
        if mode == 'review_long':
            calls = [('read_lines', {'start': 1, 'end': 1})] * 21 + calls
        received = []
        native = mode in ('review_native', 'review_guide_native')

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                received.append(request)
                name, args = calls[len(received) - 1]
                content = '<tool_call>\n' + json.dumps({'name': name, 'arguments': args}) + '\n</tool_call>'
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.end_headers()
                delta = {'role': 'assistant', 'content': content}
                finish = 'stop'
                if native:
                    assert request.get('tools'), request
                    assert request.get('chat_template_kwargs') == {'enable_thinking': False}
                    delta = {'role': 'assistant', 'tool_calls': [{'index': 0, 'id': f'call_{len(received)}',
                             'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args)}}]}
                    finish = 'tool_calls'
                deltas = [(delta, None), ({}, finish)]
                if native:
                    # Real vLLM can send the name before any argument text.
                    header = {'role': 'assistant', 'tool_calls': [{'index': 0, 'id': f'call_{len(received)}',
                              'type': 'function', 'function': {'name': name, 'arguments': None}}]}
                    arguments = {'tool_calls': [{'index': 0, 'id': None, 'type': 'function',
                                 'function': {'name': None, 'arguments': json.dumps(args) if args else None}}]}
                    deltas = [(header, None), (arguments, None), ({}, finish)]
                for delta, finish in deltas:
                    event = {'id': 'test', 'object': 'chat.completion.chunk', 'created': 1, 'model': 'qwen-test',
                             'choices': [{'index': 0, 'delta': delta, 'finish_reason': finish}]}
                    self.wfile.write(('data: ' + json.dumps(event) + '\n\n').encode())
                self.wfile.write(b'data: [DONE]\n\n')
                self.wfile.flush()

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            run(s, SimpleNamespace(model='qwen-test', base_url=f'http://127.0.0.1:{server.server_port}/v1',
                                   api_key_env='SPARKME_TEST_UNUSED_KEY', max_model_calls=len(calls) + 2,
                                   qwen_native_tools=native, qwen_thinking='off' if native else 'model',
                                   max_input_tokens=QWEN_INPUT_TOKEN_BUDGET, max_output_tokens=QWEN_OUTPUT_TOKEN_BUDGET))
            self.assertTrue(s.done)
            if mode == 'evaluate':
                self.assertEqual(s.state['submission']['score'], 4)
            else:
                self.assertEqual(len(s.state['turn_reviews']), 2)
                self.assertNotIn('score', s.state['submission'])
                if mode == 'review_guide_native':
                    self.assertEqual(len(s.state['guide_reviews']), 1)
            self.assertEqual(len(received), len(calls))
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    unittest.main()
