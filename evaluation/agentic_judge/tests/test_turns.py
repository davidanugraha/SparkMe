"""Review coverage, temporal evidence boundaries, and utility/conduct separation."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from evaluation.agentic_judge.judging.turns import CHECKS, profile, segment
from evaluation.agentic_judge.runner import metric_from
from evaluation.agentic_judge.backends.agent import Session


TOOL_CALL_BUDGET = 100


class AssessmentTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / 'transcript.txt'
        self.source.write_text('2025-12-25 19:00:00,000 - INFO - Researcher: Tell me about work.\n'
                               '2025-12-25 19:00:01,000 - INFO - Worker: I work in orthopedics.\n'
                               '2025-12-25 19:00:02,000 - INFO - Researcher: You hate it, right?\n'
                               '2025-12-25 19:00:03,000 - INFO - Worker: No, I enjoy the teamwork.\n')
        self.session = Session(self.source, profile('Understand work experiences', ['Researcher']), self.root, 'review', TOOL_CALL_BUDGET)
        self.session.dispatch('read_lines', {'start': 1, 'end': 4})

    def payload(self, turn_id='T001'):
        turn = next(t for t in self.session.state['interviewer_turns'] if t['turn_id'] == turn_id)
        return {'turn_id': turn_id,
                'checks': [{'check_id': name, 'finding': 'not_applicable' if name == 'experiential_grounding' else 'no_observed_violation', 'reason': 'Supported by available wording.',
                            'evidence': [{'start': turn['start'], 'end': turn['end']}]} for name in CHECKS],
                'utility': {'information_gain': 'adds_information', 'account': 'Adds a relevant personal work detail.',
                            'evidence': [turn['response']], 'limitations': 'Text only.'}}

    def save(self, payload):
        return self.session.dispatch('save_turn_review', payload)

    def test_logged_labels_multiline_and_missing_label(self):
        turns = self.session.state['interviewer_turns']
        self.assertEqual([t['start'] for t in turns], [1, 3])
        self.assertEqual(turns[0]['response'], {'start': 2, 'end': 2})
        multiline = ['Guide: First question\n', 'continued question\n', 'Person: Answer\n', 'Guide: Last question\n']
        result = segment(multiline, ['Guide'])
        self.assertEqual(result[0]['end'], 2)
        self.assertEqual(result[-1]['response'], None)
        with self.assertRaisesRegex(ValueError, 'interviewer-label'):
            segment(multiline, ['Wrong'])

    def test_every_turn_and_every_check_required(self):
        final = {'summary': 'Review', 'utility_summary': 'Information gained', 'conduct_summary': 'Conduct', 'limitations': ''}
        self.assertFalse(self.session.dispatch('submit', final)['ok'])
        payload = self.payload()
        payload['checks'][-1] = copy.deepcopy(payload['checks'][0])
        self.assertFalse(self.save(payload)['ok'])
        self.assertTrue(self.save(self.payload())['ok'])
        self.assertFalse(self.session.dispatch('submit', final)['ok'])
        self.assertTrue(self.save(self.payload('T002'))['ok'])
        self.assertTrue(self.session.dispatch('submit', final)['ok'])
        saved = json.loads((self.root / 'result.json').read_text())
        self.assertEqual(len(saved['turn_reviews']), 2)
        self.assertNotIn('score', saved['submission'])
        self.assertIn('Utility synthesis', (self.root / 'evidence.md').read_text())

    def test_conduct_cannot_cite_future_response(self):
        payload = self.payload()
        payload['checks'][0]['evidence'] = [{'start': 2, 'end': 2}]
        result = self.save(payload)
        self.assertFalse(result['ok'])
        self.assertIn('after the interviewer turn', result['error'])
        self.assertEqual(self.session.reviews, {})

    def test_synthesis_must_inspect_all_worker_findings(self):
        self.save(self.payload())
        self.save(self.payload('T002'))
        self.session.review_stage = 'synthesis'
        final = {'summary': 'Review', 'utility_summary': 'Information gained',
                 'conduct_summary': 'Conduct', 'limitations': ''}
        self.assertIn('Read every saved', self.session.dispatch('submit', final)['error'])
        self.session.dispatch('read_turn_review', {'turn_id': 'T001'})
        self.assertFalse(self.session.dispatch('submit', final)['ok'])
        self.session.dispatch('read_turn_review', {'turn_id': 'T002'})
        self.assertTrue(self.session.dispatch('submit', final)['ok'])

    def test_utility_cannot_use_unrelated_or_interviewer_lines(self):
        for line in [1, 3, 4]:
            payload = self.payload()
            payload['utility']['evidence'] = [{'start': line, 'end': line}]
            self.assertFalse(self.save(payload)['ok'])
        payload = self.payload()
        payload['utility']['evidence'] = []
        self.assertFalse(self.save(payload)['ok'])

    def test_unread_and_malformed_nested_evidence_is_rejected(self):
        payload = self.payload()
        self.session.read.remove(2)
        self.assertFalse(self.save(payload)['ok'])
        self.session.read.add(2)
        payload['checks'][0]['evidence'][0]['start'] = True
        self.assertFalse(self.save(payload)['ok'])
        payload = self.payload()
        payload['checks'][0]['extra'] = 'Unexpected'
        self.assertFalse(self.save(payload)['ok'])

    def test_revision_keeps_exact_quotes_and_one_record(self):
        payload = self.payload()
        self.assertTrue(self.save(payload)['ok'])
        payload['checks'][0]['finding'] = 'unclear'
        payload['checks'][0]['reason'] = 'Needs human interpretation.'
        self.assertTrue(self.save(payload)['ok'])
        result = self.session.dispatch('read_turn_review', {'turn_id': 'T001'})['data']
        self.assertEqual(result['checks'][0]['finding'], 'unclear')
        self.assertEqual(len(self.session.reviews), 1)
        self.assertEqual(result['utility']['evidence'][0]['quote'], self.session.lines[1])

    def test_review_profile_requires_purpose_and_does_not_accept_likert(self):
        with self.assertRaisesRegex(ValueError, 'purpose'):
            metric_from(None, 'review')
        with self.assertRaisesRegex(ValueError, 'omit --metric'):
            metric_from(Path('unused'), 'review', 'Understand work')
        self.assertFalse(self.session.dispatch('submit', {'summary': 'x', 'utility_summary': 'x',
                              'conduct_summary': 'x', 'limitations': '', 'score': 5})['ok'])


if __name__ == '__main__':
    unittest.main()
