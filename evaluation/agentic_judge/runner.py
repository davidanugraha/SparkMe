"""One queue of agent tasks, sharing one configured inference endpoint."""
import argparse
from collections import deque
from copy import deepcopy
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from .backends.agent import Session, atomic_json, write_report
from .judging.turns import profile, segment
from .judging.guide import load_guide


# Command-line defaults.
DEFAULT_WORKERS = 4
DEFAULT_BACKEND = 'qwen'
DEFAULT_MODE = 'review'
DEFAULT_TRANSCRIPT_SCOPE = 'complete'
DEFAULT_API_KEY_ENV = 'VLLM_API_KEY'
DEFAULT_QWEN_THINKING = 'model'
DEFAULT_CODEX_EXECUTABLE = 'codex'
DEFAULT_TASK_TIMEOUT_SECONDS = 300
DEFAULT_MAX_TOOL_CALLS = 80
DEFAULT_MAX_MODEL_CALLS = 30
DEFAULT_MAX_INPUT_TOKENS = 56_000
DEFAULT_MAX_OUTPUT_TOKENS = 4_096

# Process and output settings.
PROCESS_STOP_TIMEOUT_SECONDS = 5
QUEUE_POLL_INTERVAL_SECONDS = 0.05
OUTPUT_DIRECTORY_MODE = 0o700
TRANSCRIPT_ID_WIDTH = 4

# Process exit codes.
EXIT_SUCCESS = 0
EXIT_INCOMPLETE = 1
EXIT_INVALID_INPUT = 2


def main(argv=None):
    """Read the command, then either run the queue or execute one queued agent."""
    options = parse_arguments(argv)
    previous_handler = signal.signal(signal.SIGTERM, handle_interrupt)
    try:
        # Child processes use this same script to execute a single saved task.
        if options._task:
            task = json.loads(options._task.read_text())
            return run_agent(task)

        metric = metric_from(options.metric, options.mode, options.purpose,
                             options.interviewer_label, options.guide, options.transcript_scope)
        validate_inputs(options, metric)
        return run_evaluations(options, metric)
    except (ValueError, OSError) as error:
        print(f'Error: {error}', file=sys.stderr)
        return EXIT_INVALID_INPUT
    finally:
        signal.signal(signal.SIGTERM, previous_handler)


def handle_interrupt(signal_number, frame):
    """Let the queue's cleanup block handle SIGTERM like Ctrl-C."""
    raise KeyboardInterrupt


def parse_arguments(argv=None):
    """Convert command-line flags into an options object."""
    argument_parser = argparse.ArgumentParser(prog='python -m evaluation.agentic_judge.runner', description=__doc__)
    argument_parser.add_argument('--transcripts', '--transcript', type=Path, nargs='+', help='One or more transcript files')
    argument_parser.add_argument('--output', type=Path, help='New run directory')
    argument_parser.add_argument('--workers', type=int, default=DEFAULT_WORKERS, help='Maximum active agent tasks across all inputs (default: %(default)s)')
    argument_parser.add_argument('--backend', choices=['codex', 'qwen'], default=DEFAULT_BACKEND)
    argument_parser.add_argument('--mode', choices=['review', 'extract', 'evaluate'], default=DEFAULT_MODE)
    argument_parser.add_argument('--purpose', help='Interview objective; required for review')
    argument_parser.add_argument('--guide', type=Path, help='Optional JSON interview requirements')
    argument_parser.add_argument('--metric', type=Path, help='JSON metric for extract/evaluate')
    argument_parser.add_argument('--transcript-scope', choices=['complete', 'excerpt'], default=DEFAULT_TRANSCRIPT_SCOPE)
    argument_parser.add_argument('--interviewer-label', action='append', help='Repeat for multiple interviewers; default Interviewer')
    argument_parser.add_argument('--model', help='Served model name for Qwen; optional for Codex')
    argument_parser.add_argument('--base-url', help='The shared vLLM endpoint, e.g. http://127.0.0.1:8000/v1')
    argument_parser.add_argument('--api-key-env', default=DEFAULT_API_KEY_ENV)
    argument_parser.add_argument('--qwen-native-tools', action='store_true')
    argument_parser.add_argument('--qwen-thinking', choices=['model', 'on', 'off'], default=DEFAULT_QWEN_THINKING)
    argument_parser.add_argument('--codex-executable', default=DEFAULT_CODEX_EXECUTABLE)
    argument_parser.add_argument('--timeout', type=float, default=DEFAULT_TASK_TIMEOUT_SECONDS, help='Deadline in seconds per agent task')
    argument_parser.add_argument('--max-tools', type=int, default=DEFAULT_MAX_TOOL_CALLS)
    argument_parser.add_argument('--max-model-calls', type=int, default=DEFAULT_MAX_MODEL_CALLS)
    argument_parser.add_argument('--max-input-tokens', type=int, default=DEFAULT_MAX_INPUT_TOKENS)
    argument_parser.add_argument('--max-output-tokens', type=int, default=DEFAULT_MAX_OUTPUT_TOKENS)
    argument_parser.add_argument('--_task', type=Path, help=argparse.SUPPRESS)
    return argument_parser.parse_args(argv)


def validate_inputs(options, metric):
    """Check all inputs before creating output files or launching agents."""
    if not options.transcripts or not options.output:
        raise ValueError('--transcripts and --output are required')
    budgets = [options.workers, options.max_tools, options.max_model_calls,
               options.max_input_tokens, options.max_output_tokens]
    if not math.isfinite(options.timeout) or options.timeout <= 0 or min(budgets) <= 0:
        raise ValueError('Worker count and budgets must be positive and finite')
    if options.backend == 'qwen' and (not options.model or not options.base_url):
        raise ValueError('Qwen requires --model and --base-url')
    for transcript in options.transcripts:
        lines = transcript.read_bytes().decode('utf-8').splitlines(keepends=True)
        if not ''.join(lines).strip():
            raise ValueError(f'Transcript is empty: {transcript}')
        if options.mode == 'review':
            segment(lines, metric['interviewer_labels'])


def metric_from(path, mode, purpose=None, interviewer_labels=None, guide_path=None, transcript_scope=DEFAULT_TRANSCRIPT_SCOPE):
    if mode == 'review':
        if path is not None:
            raise ValueError('Review uses the built-in utility/conduct profile; omit --metric')
        if not purpose or not purpose.strip():
            raise ValueError('Review requires --purpose describing the interview objective')
        return profile(purpose, interviewer_labels, load_guide(guide_path) if guide_path else None, transcript_scope)
    if guide_path is not None or transcript_scope != 'complete':
        raise ValueError('--guide and --transcript-scope are supported in review mode only')
    if path is None:
        raise ValueError('Extraction and evaluation require --metric')
    metric = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(metric, dict) or not isinstance(metric.get('description'), str) or not metric['description'].strip():
        raise ValueError('Metric must be a JSON object with a nonempty description')
    if mode == 'evaluate':
        rubric = metric.get('rubric', {})
        lo, hi = rubric.get('min_score'), rubric.get('max_score')
        if not all(type(x) in (int, float) and math.isfinite(x) for x in (lo, hi)) or lo >= hi or not rubric.get('description'):
            raise ValueError('Evaluation requires rubric.description and finite min_score < max_score')
    return metric


def make_task(session, progress, config, stage, turn_id=None):
    """Describe one agent job; every job uses the same inference configuration."""
    output = session.output / 'turns' / turn_id if turn_id else session.output
    return {
        'key': progress['id'],
        'stage': stage,
        'turn_id': turn_id,
        'output': str(output),
        'transcript': progress['transcript'],
        'source_hash': session.state['transcript_sha256'],
        'metric': session.metric,
        'options': config,
        'reviews': list(session.reviews.values()) if stage == 'synthesis' else [],
    }


def collect_task_result(task, code, session, record, error=None):
    """Merge saved findings; return True when this transcript is ready for synthesis."""
    key = task['key']
    path = Path(task['output']) / 'result.json'
    saved = json.loads(path.read_text()) if path.exists() else {}
    try:
        if saved.get('transcript_sha256') != session.state['transcript_sha256'] or saved.get('metric') != session.metric:
            raise ValueError('Worker source hash/profile mismatch')
        if task['stage'] == 'turn':
            reviews = saved.get('turn_reviews', [])
            if any(r['turn_id'] != task['turn_id'] for r in reviews):
                raise ValueError('Worker returned an unassigned turn')
            import_reviews(session, reviews)
        if error or code or saved.get('status') != 'completed':
            raise ValueError(error or saved.get('error', f'Agent exited with code {code}'))
        if task['stage'] == 'turn' and task['turn_id'] not in session.reviews:
            raise ValueError('Missing assigned turn review')
    except (ValueError, KeyError) as exc:
        record['failed_tasks'].append({'task': task['turn_id'] or task['stage'], 'error': str(exc)})
    if task['stage'] == 'turn':
        record['completed_turns'] = len(session.reviews)
        record['remaining_turns'] -= 1
        session.persist()
        if record['remaining_turns'] == 0:
            if record['failed_tasks']:
                session.state.update(status='incomplete', error='Failed turn tasks: ' + ', '.join(f['task'] for f in record['failed_tasks']))
                session.persist()
                record['status'] = 'incomplete'
            else:
                return True
    else:
        if saved:
            session.state = saved
        if record['failed_tasks']:
            session.state.update(status='incomplete', error=record['failed_tasks'][-1]['error'])
        atomic_json(session.output / 'result.json', session.state)
        write_report(session.output, session.state)
        record['status'] = session.state['status']
        print(f'{key}: {record["status"]}', flush=True)
    return False


def import_reviews(session, records):
    """Validate worker records against this exact source before synthesis."""
    session.read = set(range(1, len(session.lines) + 1))
    tool = session.tools['save_turn_review']
    try:
        for review in records:
            payload = deepcopy({key: review[key] for key in ('turn_id', 'checks', 'utility')})
            for field in [*payload['checks'], payload['utility']]:
                field['evidence'] = [{k: span[k] for k in ('start', 'end')} for span in field['evidence']]
            tool.call(payload)
    finally:
        session.read.clear()


def run_agent(task):
    """One isolated agent process. It never spawns another runner."""
    options = argparse.Namespace(**task['options'])
    session = Session(Path(task['transcript']), task['metric'], Path(task['output']), options.mode,
                      options.max_tools, task.get('turn_id'))
    session.state.update(backend=options.backend, model=options.model)
    try:
        if session.state['transcript_sha256'] != task['source_hash']:
            raise ValueError('Transcript changed since the run was started')
        if task['stage'] == 'synthesis':
            import_reviews(session, task['reviews'])
            session.review_stage = 'synthesis'
            session.state['review_stage'] = 'synthesis'
            del session.tools['save_turn_review']
        session.persist()
        if options.backend == 'qwen':
            from .backends.qwen_agent import run
        else:
            from .backends.codex import run
        run(session, options)
        if not session.done:
            raise RuntimeError('Agent ended without a validated submission')
    except (Exception, KeyboardInterrupt) as exc:
        session.state.update(status='incomplete', error=f'{type(exc).__name__}: {exc}')
        session.persist()
        return EXIT_INCOMPLETE
    return EXIT_SUCCESS


def stop_agent(process):
    """Terminate an agent process group, escalating only if it does not stop."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=PROCESS_STOP_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()


def run_evaluations(options, metric):
    """Prepare all inputs, run at most --workers agents, and collect their results."""
    options.output.mkdir(parents=True, exist_ok=False, mode=OUTPUT_DIRECTORY_MODE)
    manifest = {'status': 'running', 'workers': options.workers, 'backend': options.backend,
                'model': options.model, 'base_url': options.base_url, 'transcripts': []}
    run_file = options.output / 'run.json'
    sessions = {}
    progress_by_id = {}
    task_groups = []
    config = {key: str(value) if isinstance(value, Path) else value
              for key, value in vars(options).items()
              if key not in ('transcripts', 'output', '_task')}

    # 1. Create each transcript's report and its independent agent tasks.
    for number, transcript in enumerate(options.transcripts, 1):
        key = f'{number:0{TRANSCRIPT_ID_WIDTH}d}-{transcript.stem}'
        output = options.output / key
        output.mkdir(mode=OUTPUT_DIRECTORY_MODE)
        session = Session(transcript, metric, output, options.mode, options.max_tools)
        session.state.update(backend=options.backend, model=options.model)
        session.persist()
        sessions[key] = session
        progress = {'id': key, 'transcript': str(transcript.resolve()), 'output': str(output.resolve()),
                    'status': 'queued', 'completed_turns': 0, 'failed_tasks': []}
        manifest['transcripts'].append(progress)
        progress_by_id[key] = progress

        if options.mode == 'review':
            # Longest history first gives the shared endpoint a chance to cache it.
            turn_ids = [turn['turn_id'] for turn in reversed(session.state['interviewer_turns'])]
            progress['remaining_turns'] = len(turn_ids)
            tasks = [make_task(session, progress, config, 'turn', turn_id) for turn_id in turn_ids]
        else:
            tasks = [make_task(session, progress, config, 'full')]
        task_groups.append(deque(tasks))

    # Interleave transcripts so one long interview does not monopolize the queue.
    pending = deque()
    while any(task_groups):
        for group in task_groups:
            if group:
                pending.append(group.popleft())
    active = []
    atomic_json(run_file, manifest)

    try:
        while pending or active:
            finished = []

            # 2. Fill free worker slots. Each child executes exactly one agent task.
            while pending and len(active) < options.workers:
                task = pending.popleft()
                output = Path(task['output'])
                output.mkdir(parents=True, exist_ok=True, mode=OUTPUT_DIRECTORY_MODE)
                tasks_dir = sessions[task['key']].output / 'tasks'
                tasks_dir.mkdir(exist_ok=True, mode=OUTPUT_DIRECTORY_MODE)
                task_file = tasks_dir / ((task['turn_id'] or task['stage']) + '.json')
                atomic_json(task_file, task)
                log = (output / 'backend.log').open('w', encoding='utf-8')
                command = [sys.executable, '-m', 'evaluation.agentic_judge.runner', '--_task', str(task_file)]
                try:
                    process = subprocess.Popen(command, stdout=log, stderr=log, start_new_session=True)
                except OSError as error:
                    log.close()
                    finished.append((task, EXIT_INCOMPLETE, str(error)))
                    continue
                active.append((process, task, time.monotonic(), log))
                progress_by_id[task['key']]['status'] = 'running'

            # 3. Collect finished tasks and stop any that exceeded their deadline.
            for item in active[:]:
                process, task, started, log = item
                expired = time.monotonic() - started > options.timeout
                if process.poll() is None and not expired:
                    continue
                if expired:
                    stop_agent(process)
                log.close()
                active.remove(item)
                error = 'Agent task deadline exceeded' if expired else None
                finished.append((task, process.wait(), error))

            # 4. Merge results. Queue synthesis only after that transcript's turns succeed.
            for task, exit_code, error in finished:
                session = sessions[task['key']]
                progress = progress_by_id[task['key']]
                ready_for_synthesis = collect_task_result(task, exit_code, session, progress, error)
                if ready_for_synthesis:
                    pending.appendleft(make_task(session, progress, config, 'synthesis'))
            atomic_json(run_file, manifest)
            time.sleep(QUEUE_POLL_INTERVAL_SECONDS)

    except KeyboardInterrupt:
        manifest['error'] = 'Interrupted; active tasks cancelled and partial findings retained'
    finally:
        # 5. Always stop remaining children and retain their partial evidence.
        for process, task, started, log in active:
            stop_agent(process)
            log.close()
            collect_task_result(task, process.wait(), sessions[task['key']],
                                progress_by_id[task['key']], 'Interrupted')
        for key, session in sessions.items():
            progress = progress_by_id[key]
            if progress['status'] != 'completed':
                state = json.loads((session.output / 'result.json').read_text())
                state.update(status='incomplete', error=state.get('error') or manifest.get('error', 'Unfinished tasks'))
                atomic_json(session.output / 'result.json', state)
                write_report(session.output, state)
                progress['status'] = 'incomplete'
        manifest['status'] = 'completed' if all(record['status'] == 'completed' for record in manifest['transcripts']) else 'incomplete'
        atomic_json(run_file, manifest)

    print(f'{manifest["status"]}: {run_file}')
    return EXIT_SUCCESS if manifest['status'] == 'completed' else EXIT_INCOMPLETE


# Report main()'s exit code to the shell.
if __name__ == '__main__':
    sys.exit(main())
