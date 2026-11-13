"""Isolation, cache-friendly prefixes, real process concurrency and merge failures."""
import importlib.util
import json
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import textwrap
import time
import unittest

from evaluation.agentic_judge.judging.turns import CHECKS, profile
from evaluation.agentic_judge.runner import parse_arguments
from evaluation.agentic_judge.judging.prompts import user_prompt
from evaluation.agentic_judge.backends.agent import Session


TEST_TASK_TIMEOUT_SECONDS = 15
SUBPROCESS_TIMEOUT_SECONDS = 25
SHUTDOWN_TIMEOUT_SECONDS = 15
STARTUP_POLL_ATTEMPTS = 100
STARTUP_POLL_INTERVAL_SECONDS = 0.05
QWEN_TASK_TIMEOUT_SECONDS = 40
QWEN_SUBPROCESS_TIMEOUT_SECONDS = 50
QWEN_RESPONSE_DELAY_SECONDS = 0.15
VALIDATION_TIMEOUT_SECONDS = 10
EXECUTABLE_MODE = 0o700


def command(options):
    argv = [sys.executable, '-m', 'evaluation.agentic_judge.runner']
    for key, value in vars(options).items():
        if value is None or value is False:
            continue
        flag = '--' + key.replace('_', '-')
        if value is True:
            argv.append(flag)
        elif isinstance(value, list):
            argv.append(flag)
            argv.extend(map(str, value))
        else:
            argv.extend([flag, str(value)])
    return argv


class ParallelTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / 'interview.txt'
        self.source.write_text('Interviewer: What is your role?\nParticipant: I manage a clinic.\n'
                               'Interviewer: What do you verify?\nParticipant: I check the dates.\n')
        self.guide = self.root / 'guide.json'
        self.guide.write_text(json.dumps({'title': 'Work', 'items': [{'id': 'role', 'description': 'Participant role'}]}))

    def options(self, output=None):
        return parse_arguments(['--backend', 'codex', '--mode', 'review', '--purpose', 'Understand work',
            '--transcripts', str(self.source), '--output', str(output or self.root / 'run'), '--workers', '2',
            '--timeout', str(TEST_TASK_TIMEOUT_SECONDS), '--guide', str(self.guide)])

    def fake_codex(self):
        script = self.root / 'fake-codex'
        script.write_text('#!' + sys.executable + '\n' + textwrap.dedent(r'''
import json,os,sys,time

def send(m):
 print(json.dumps(m),flush=True)

def call(name,args):
 global rid
 rid+=1
 send({'id':rid,'method':'item/tool/call','params':{'tool':name,'arguments':args}})
 result=json.loads(sys.stdin.readline())['result']
 payload=json.loads(result['contentItems'][0]['text'])
 assert payload['ok'],payload
 return payload['data']

def event(stage,kind,prompt=''):
 path=os.environ.get('SPARKME_TEST_EVENTS')
 if path:
  fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600)
  os.write(fd,(json.dumps({'stage':stage,'kind':kind,'time':time.time(),'pid':os.getpid(),'prompt':prompt})+'\n').encode())
  os.close(fd)

rid=100
for raw in sys.stdin:
 m=json.loads(raw)
 if m.get('method')=='initialize':send({'id':m['id'],'result':{}})
 elif m.get('method')=='thread/start':
  instructions=m['params']['developerInstructions']
  tools=m['params']['dynamicTools']
  turn=next(t for t in tools if t['name']=='save_turn_review') if 'SYNTHESIS TASK OVERRIDE' not in instructions else None
  checks=turn['inputSchema']['properties']['checks']['items']['properties']['check_id']['enum'] if turn else []
  stage='turn' if 'TURN TASK OVERRIDE' in instructions else 'synthesis' if 'SYNTHESIS TASK OVERRIDE' in instructions else 'full'
  send({'id':m['id'],'result':{'thread':{'id':'test'},'model':'scripted'}})
 elif m.get('method')=='turn/start':
  prompt=m['params']['input'][0]['text']
  send({'id':m['id'],'result':{'turn':{'id':'test'}}})
  event(stage,'start',prompt)
  overview=call('review_overview',{})
  if stage=='turn':time.sleep(float(os.environ.get('SPARKME_TEST_DELAY','0.3')))
  if stage!='turn':call('read_lines',{'start':1,'end':4})
  if stage=='synthesis':
   for key in overview['reviewed_turn_ids']:call('read_turn_review',{'turn_id':key})
  if stage!='synthesis':
   for t in overview['turns']:
    if os.environ.get('SPARKME_TEST_FAIL_TURN')==t['turn_id'] and os.environ.get('SPARKME_TEST_FAIL_TEXT','') in prompt:raise SystemExit(3)
    call('save_turn_review',{'turn_id':t['turn_id'],'checks':[
     {'check_id':name,'finding':'not_applicable' if name=='experiential_grounding' else 'no_observed_violation',
      'reason':'Scripted wording judgment.','evidence':[{'start':t['start'],'end':t['end']}]} for name in checks],
     'utility':{'information_gain':'adds_information','account':'Relevant detail.','evidence':[t['response']],'limitations':'Scripted.'}})
  if stage!='turn' and overview.get('guide'):
   for item in overview['guide']['items']:
    call('save_guide_review',{'item_id':item['id'],'coverage':'covered','finding':'no_observed_violation',
     'reason':'Participant supplies role.','evidence':[{'start':2,'end':2}],'missing_information':'','limitations':'Scripted.'})
  event(stage,'end')
  call('submit',{'summary':'Saved all assigned work.','utility_summary':'Relevant information.',
    'conduct_summary':'Scripted conduct findings.','limitations':'Synthetic test.'})
'''))
        script.chmod(EXECUTABLE_MODE)
        return script

    def test_turn_prefix_hides_future_and_shares_initial_input(self):
        a, b = self.root / 'a', self.root / 'b'
        a.mkdir(); b.mkdir()
        metric = profile('Understand work')
        early = Session(self.source, metric, a, 'review', 20, 'T001')
        late = Session(self.source, metric, b, 'review', 20, 'T002')
        self.assertEqual(early.state['transcript_sha256'], late.state['transcript_sha256'])
        self.assertEqual([t['turn_id'] for t in early.state['interviewer_turns']], ['T001'])
        prefix = user_prompt(early).split('\n\nAssigned turn')[0]
        self.assertTrue(user_prompt(late).startswith(prefix))
        self.assertNotIn('What do you verify?', user_prompt(early))
        self.assertEqual(early.read, {1, 2})
        self.assertFalse(early.dispatch('read_lines', {'start': 3, 'end': 3})['ok'])
        self.assertEqual(early.dispatch('search', {'text': 'verify'})['data']['matching_lines'], [])
        self.assertNotIn('save_guide_review', early.tools)

    def test_one_queue_handles_multiple_transcripts_with_global_worker_limit(self):
        options = self.options()
        options.codex_executable = str(self.fake_codex())
        events = self.root / 'events.jsonl'
        options.transcripts = [self.source, self.source]
        result = subprocess.run(command(options),
            env={**os.environ, 'SPARKME_TEST_EVENTS': str(events)}, capture_output=True, timeout=SUBPROCESS_TIMEOUT_SECONDS)
        self.assertEqual(result.returncode, 0, result.stdout.decode()+result.stderr.decode())
        manifest = json.loads((options.output / 'run.json').read_text())
        self.assertEqual(manifest['status'], 'completed')
        self.assertEqual(len(manifest['transcripts']), 2)
        for record in manifest['transcripts']:
            state = json.loads((Path(record['output']) / 'result.json').read_text())
            self.assertEqual([t['turn_id'] for t in state['turn_reviews']], ['T001', 'T002'])
            self.assertEqual(len(state['guide_reviews']), 1)
            self.assertEqual(state['review_stage'], 'synthesis')
            self.assertEqual(state['status'], 'completed')
            self.assertEqual(state['turn_reviews'][0]['utility']['evidence'][0]['quote'], 'Participant: I manage a clinic.\n')
        logs = [json.loads(line) for line in events.read_text().splitlines()]
        turn_starts = [e for e in logs if e['stage']=='turn' and e['kind']=='start']
        turn_ends = [e for e in logs if e['stage']=='turn' and e['kind']=='end']
        self.assertEqual(len(turn_starts), 4)
        self.assertGreaterEqual(sum(e['time'] < min(x['time'] for x in turn_ends) for e in turn_starts), 2)
        self.assertEqual(len([e for e in logs if e['stage']=='synthesis' and e['kind']=='start']), 2)
        current, peak = 0, 0
        for event in sorted(logs, key=lambda e: e['time']):
            current += 1 if event['kind'] == 'start' else -1
            peak = max(peak, current)
        self.assertEqual(peak, options.workers)
        self.assertEqual(current, 0)

    def test_failed_turn_retains_other_reviews_and_blocks_synthesis(self):
        options = self.options()
        options.codex_executable = str(self.fake_codex())
        result = subprocess.run(command(options), env={**os.environ, 'SPARKME_TEST_FAIL_TURN': 'T001'},
                                capture_output=True, timeout=SUBPROCESS_TIMEOUT_SECONDS)
        self.assertEqual(result.returncode, 1)
        state = json.loads((options.output / '0001-interview/result.json').read_text())
        self.assertEqual(state['status'], 'incomplete')
        self.assertEqual([t['turn_id'] for t in state['turn_reviews']], ['T002'])
        self.assertNotIn('submission', state)
        self.assertIn('T001', state['error'])

    def test_termination_cancels_workers_and_preserves_partial_results(self):
        options = self.options()
        options.codex_executable = str(self.fake_codex())
        events = self.root / 'events.jsonl'
        process = subprocess.Popen(command(options), env={**os.environ, 'SPARKME_TEST_DELAY': '60',
            'SPARKME_TEST_EVENTS': str(events)}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: process.kill() if process.poll() is None else None)
        for _ in range(STARTUP_POLL_ATTEMPTS):
            if events.exists() and len(events.read_text().splitlines()) >= 2:
                break
            time.sleep(STARTUP_POLL_INTERVAL_SECONDS)
        else:
            self.fail('Turn workers never started')
        pids = [json.loads(line)['pid'] for line in events.read_text().splitlines()]
        process.terminate()
        self.assertEqual(process.wait(timeout=SHUTDOWN_TIMEOUT_SECONDS), 1)
        self.assertEqual(json.loads((options.output / '0001-interview/result.json').read_text())['status'], 'incomplete')
        for pid in pids:
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)

    def test_failed_input_does_not_block_other_transcripts(self):
        bad = self.root / 'bad.txt'
        bad.write_text(self.source.read_text().replace('What is your role?', 'What is your role? fail_this'))
        options = self.options()
        options.transcripts = [bad, self.source]
        options.codex_executable = str(self.fake_codex())
        result = subprocess.run(command(options), env={**os.environ, 'SPARKME_TEST_FAIL_TURN':'T001',
            'SPARKME_TEST_FAIL_TEXT':'fail_this'}, capture_output=True, timeout=SUBPROCESS_TIMEOUT_SECONDS)
        self.assertEqual(result.returncode, 1)
        manifest = json.loads((options.output/'run.json').read_text())
        self.assertEqual([r['status'] for r in manifest['transcripts']], ['incomplete', 'completed'])

    def test_one_worker_and_no_guide_use_the_same_queue(self):
        options = self.options()
        options.workers = 1
        options.guide = None
        options.codex_executable = str(self.fake_codex())
        result = subprocess.run(command(options), capture_output=True, timeout=SUBPROCESS_TIMEOUT_SECONDS)
        self.assertEqual(result.returncode, 0, result.stdout.decode()+result.stderr.decode())
        state = json.loads((options.output/'0001-interview/result.json').read_text())
        self.assertEqual(len(state['turn_reviews']), 2)
        self.assertEqual(state['guide_reviews'], [])
        self.assertEqual(state['status'], 'completed')

    @unittest.skipUnless(importlib.util.find_spec('qwen_agent'), 'Install Qwen dependencies')
    def test_real_qwen_sdk_parallel_requests_and_global_synthesis(self):
        options = self.options()
        options.backend = 'qwen'
        options.model = 'scripted-qwen'
        options.qwen_native_tools = True
        options.qwen_thinking = 'off'
        options.timeout = QWEN_TASK_TIMEOUT_SECONDS
        lock = threading.Lock()
        active, peak = 0, 0
        checked_requests = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                nonlocal active, peak
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                with lock:
                    active += 1
                    peak = max(peak, active)
                try:
                    time.sleep(QWEN_RESPONSE_DELAY_SECONDS)
                    names = [tool['function']['name'] for tool in body['tools']]
                    user = next(m['content'] for m in body['messages'] if m['role'] == 'user')
                    step = len([m for m in body['messages'] if m['role'] in ('tool', 'function')])
                    final = ('submit', {'summary': 'Assigned work saved.', 'utility_summary': 'Relevant information.',
                        'conduct_summary': 'Scripted findings.', 'limitations': 'Deterministic HTTP test.'})
                    if 'save_turn_review' in names:
                        turn = json.loads(user.split('Assigned turn (later transcript lines are unavailable):\n')[1].splitlines()[0])
                        checked_requests.append(turn['turn_id'])
                        checks = [{'check_id':name, 'finding':'not_applicable' if name=='experiential_grounding' else 'no_observed_violation',
                            'reason':'Scripted check.', 'evidence':[{'start':turn['start'], 'end':turn['end']}]} for name in CHECKS]
                        tasks = [('save_turn_review', {'turn_id':turn['turn_id'], 'checks':checks,
                            'utility':{'information_gain':'adds_information', 'account':'Relevant detail.',
                                'evidence':[turn['response']], 'limitations':'Scripted.'}}), final]
                    else:
                        checked_requests.append('synthesis')
                        tasks = [('read_lines', {'start':1, 'end':4}),
                            ('read_turn_review', {'turn_id':'T001'}),
                            ('read_turn_review', {'turn_id':'T002'}),
                            ('save_guide_review', {'item_id':'role', 'coverage':'covered', 'finding':'no_observed_violation',
                                'reason':'Participant states role.', 'evidence':[{'start':2, 'end':2}],
                                'missing_information':'', 'limitations':'Scripted.'}), final]
                    name, arguments = tasks[step]
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/event-stream')
                    self.end_headers()
                    delta = {'role':'assistant', 'tool_calls':[{'index':0, 'id':f'call_{step}', 'type':'function',
                        'function':{'name':name, 'arguments':json.dumps(arguments)}}]}
                    for part, finish in ((delta,None), ({},'tool_calls')):
                        event = {'id':'scripted', 'object':'chat.completion.chunk', 'created':1, 'model':'scripted-qwen',
                            'choices':[{'index':0, 'delta':part, 'finish_reason':finish}]}
                        self.wfile.write(('data: '+json.dumps(event)+'\n\n').encode())
                    self.wfile.write(b'data: [DONE]\n\n')
                    self.wfile.flush()
                finally:
                    with lock:
                        active -= 1

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        options.base_url = f'http://127.0.0.1:{server.server_port}/v1'
        result = subprocess.run(command(options), capture_output=True, timeout=QWEN_SUBPROCESS_TIMEOUT_SECONDS)
        self.assertEqual(result.returncode, 0, result.stdout.decode()+result.stderr.decode()+
                         (options.output/'0001-interview/backend.log').read_text())
        state = json.loads((options.output/'0001-interview/result.json').read_text())
        self.assertEqual(state['status'], 'completed')
        self.assertEqual(len(state['turn_reviews']), 2)
        self.assertEqual(len(state['guide_reviews']), 1)
        self.assertGreaterEqual(peak, 2)
        self.assertIn('synthesis', checked_requests)

    def test_runner_rejects_zero_workers_before_creating_outputs(self):
        options = self.options()
        options.workers = 0
        result = subprocess.run(command(options), capture_output=True, timeout=VALIDATION_TIMEOUT_SECONDS)
        self.assertEqual(result.returncode, 2)
        self.assertIn('positive', result.stderr.decode())
        self.assertFalse(options.output.exists())

    @unittest.skipUnless(importlib.util.find_spec('qwen_agent'), 'Install Qwen dependencies')
    def test_parallel_context_budget_fails_instead_of_truncating_history(self):
        from qwen_agent.llm.schema import Message
        from evaluation.agentic_judge.backends.qwen_agent import _check_context
        messages = [Message(role='user', content='Earlier participant correction. ' * 100)]
        with self.assertRaisesRegex(RuntimeError, 'not silently truncated'):
            _check_context(messages, [], 10)
        _check_context(messages, [], 10000)


if __name__ == '__main__':
    unittest.main()
