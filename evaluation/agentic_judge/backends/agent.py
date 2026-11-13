"""Transcript-scoped tools and durable evidence state; no arbitrary shell access."""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from ..judging import turns
from . import tools


JSON_INDENT = 2


def atomic_json(path: Path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=JSON_INDENT) + '\n', encoding='utf-8')
    temporary.replace(path)


def write_report(output: Path, state: dict):
    report = [f'# Transcript evidence ({state["status"]})', '',
              f'Mode: {state["mode"]}', '', state['metric']['description'], '']
    if state['mode'] == 'review':
        report.extend(turns.report_lines(state))
    for item in state['evidence']:
        report.extend([f'## {item["evidence_id"]}: {item["kind"]} (lines {item["start"]}–{item["end"]})', '',
                       *['> ' + line for line in item['quote'].splitlines()], '', item['interpretation'], ''])
    if 'submission' in state:
        final = state['submission']
        report.extend(['## Submission', '', final['summary'], '', 'Limitations: ' + final['limitations'], ''])
        if 'score' in final:
            report.append(f'Score: {final["score"]}')
    if 'error' in state:
        report.extend(['', 'Error: ' + state['error']])
    (output / 'evidence.md').write_text('\n'.join(report) + '\n', encoding='utf-8')


class Session:
    def __init__(self, transcript: Path, metric: dict, output: Path, mode: str, max_tools: int, turn_id=None):
        raw = transcript.read_bytes()
        self.lines = raw.decode('utf-8').splitlines(keepends=True)
        if not self.lines:
            raise ValueError('Transcript is empty')
        self.metric, self.output, self.mode = metric, output, mode
        self.review_stage = 'turn' if turn_id else 'full'
        self.max_tools, self.calls = max_tools, 0
        self.read = set()
        self.evidence = {}
        self.reviews = {}
        self.review_reads = set()
        self.guide_reviews = {}
        self.next_id = 1
        self.done = False
        self.state = {'status': 'running', 'mode': mode, 'transcript_sha256': hashlib.sha256(raw).hexdigest(),
                      'line_count': len(self.lines), 'metric': metric, 'evidence': [], 'read_ranges': []}
        available_tools = [tools.ReadLines(session=self), tools.Search(session=self)]
        if mode == 'review':
            self.state['interviewer_turns'] = turns.segment(self.lines, metric['interviewer_labels'])
            if turn_id:
                assigned = [t for t in self.state['interviewer_turns'] if t['turn_id'] == turn_id]
                if not assigned:
                    raise ValueError('Unknown assigned turn ID')
                turn = assigned[0]
                self.state['source_line_count'] = len(self.lines)
                self.lines = self.lines[:(turn['response'] or turn)['end']]
                self.state.update(interviewer_turns=assigned, line_count=len(self.lines), review_stage='turn')
            self.state['turn_reviews'] = []
            self.state['guide_reviews'] = []
            available_tools.extend([tools.ReviewOverview(session=self), tools.ReadTurnReview(session=self), tools.SaveTurnReview(session=self)])
            if metric.get('guide') and not turn_id:
                available_tools.append(tools.SaveGuideReview(session=self))
            available_tools.append(tools.SubmitReview(session=self))
        else:
            available_tools.extend([tools.SaveEvidence(session=self), tools.ListEvidence(session=self), tools.RemoveEvidence(session=self), tools.Submit(session=self)])
        self.tools = {tool.name: tool for tool in available_tools}
        self.persist()

    def persist(self):
        ranges = []
        for n in sorted(self.read):
            if ranges and ranges[-1][1] == n - 1:
                ranges[-1][1] = n
            else:
                ranges.append([n, n])
        self.state.update(evidence=list(self.evidence.values()), read_ranges=ranges, tool_calls=self.calls)
        if self.mode == 'review':
            self.state['turn_reviews'] = [self.reviews[t['turn_id']] for t in self.state['interviewer_turns'] if t['turn_id'] in self.reviews]
            self.state['guide_reviews'] = [self.guide_reviews[item['id']] for item in (self.metric.get('guide') or {}).get('items', []) if item['id'] in self.guide_reviews]
            if self.review_stage == 'synthesis':
                self.state['read_review_ids'] = sorted(self.review_reads)
        atomic_json(self.output / 'result.json', self.state)
        write_report(self.output, self.state)

    def trace(self, event):
        with (self.output / 'trace.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps({'time': time.time(), **event}, ensure_ascii=False) + '\n')

    def range(self, start, end):
        if not 1 <= start <= end <= len(self.lines):
            raise ValueError(f'Expected 1 <= start <= end <= {len(self.lines)}')
        if end - start + 1 > tools.MAX_READ_LINES:
            raise ValueError(f'Read or cite at most {tools.MAX_READ_LINES} lines at a time')
        text = ''.join(self.lines[start - 1:end])
        if len(text) > tools.MAX_READ_CHARACTERS:
            raise ValueError(f'Range exceeds {tools.MAX_READ_CHARACTERS} characters; request a smaller range')
        return text

    def dispatch(self, name, args):
        if self.done:
            return {'ok': False, 'error': 'Already submitted'}
        self.calls += 1
        if self.calls > self.max_tools:
            raise RuntimeError('Tool-call budget exhausted')
        try:
            tool = self.tools.get(name)
            if tool is None:
                raise ValueError(f'Unknown tool: {name}')
            data = tool.call(args)
            result = {'ok': True, 'data': data, 'remaining_tool_calls': self.max_tools - self.calls}
        except (ValueError, KeyError) as exc:
            result = {'ok': False, 'error': str(exc)}
        self.persist()
        self.trace({'tool': name, 'arguments': args, 'result': result})
        return result
