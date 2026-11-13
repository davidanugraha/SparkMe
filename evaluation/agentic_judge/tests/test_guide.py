"""Guide requirements, conditional grounding, and whole-interview coverage."""
import json
from pathlib import Path
import tempfile
import unittest

from evaluation.agentic_judge.judging.turns import CHECKS, profile
from evaluation.agentic_judge.runner import metric_from
from evaluation.agentic_judge.judging.guide import load_guide
from evaluation.agentic_judge.backends.agent import Session


TOOL_CALL_BUDGET = 100


class GuideTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source = self.root / 'transcript.txt'
        self.source.write_text('Interviewer: Tell me about your work.\nParticipant: I manage a clinic.\n')
        self.guide_path = self.root / 'guide.json'
        self.data = {'title': 'Work guide', 'items': [
            {'id': 'role', 'description': 'Establish own work role', 'grounding': 'first_hand'},
            {'id': 'training', 'description': 'Establish professional training'},
            {'id': 'wishes', 'description': 'Future wishes', 'required': False}]}
        self.guide_path.write_text(json.dumps(self.data))
        self.guide = load_guide(self.guide_path)
        self.session = Session(self.source, profile('Understand work', guide=self.guide), self.root, 'review', TOOL_CALL_BUDGET)
        self.session.dispatch('read_lines', {'start': 1, 'end': 2})

    def payload(self):
        return {'turn_id': 'T001', 'checks': [
            {'check_id': name, 'finding': 'no_observed_violation', 'reason': 'Question fits the provided context.',
             'evidence': [{'start': 1, 'end': 1}],
             **({'guide_item_ids': ['role']} if name == 'experiential_grounding' else {})} for name in CHECKS],
            'utility': {'information_gain': 'adds_information', 'account': 'States their own role.',
                        'evidence': [{'start': 2, 'end': 2}], 'limitations': ''}}

    def coverage(self, item='role', status='covered', finding='no_observed_violation'):
        return {'item_id': item, 'coverage': status, 'finding': finding,
                'reason': 'Participant describes the role.' if status == 'covered' else 'This information was not established.',
                'evidence': [{'start': 2, 'end': 2}] if status == 'covered' else [],
                'missing_information': '' if status == 'covered' else 'Information about training.', 'limitations': ''}

    def test_guide_parser_defaults_and_invalid_input(self):
        self.assertTrue(self.guide['items'][0]['required'])
        self.assertEqual(self.guide['items'][1]['grounding'], 'unspecified')
        self.assertEqual(len(self.guide['sha256']), 64)
        for mutate in [lambda d: d['items'].append(d['items'][0]),
                       lambda d: d['items'][0].update(required='yes'),
                       lambda d: d['items'][0].update(grounding='anything'),
                       lambda d: d.update(items=[])]:
            data = json.loads(json.dumps(self.data))
            mutate(data)
            self.guide_path.write_text(json.dumps(data))
            with self.assertRaises(ValueError):
                load_guide(self.guide_path)

    def test_all_guide_items_required_before_submission(self):
        self.assertTrue(self.session.dispatch('save_turn_review', self.payload())['ok'])
        final = {'summary': 'Review', 'utility_summary': 'Role learned.', 'conduct_summary': 'Training omitted.', 'limitations': ''}
        result = self.session.dispatch('submit', final)
        self.assertFalse(result['ok'])
        self.assertIn('Missing guide reviews', result['error'])
        for payload in [self.coverage(), self.coverage('training', 'not_observed', 'violation'),
                        self.coverage('wishes', 'not_observed', 'no_observed_violation')]:
            self.assertTrue(self.session.dispatch('save_guide_review', payload)['ok'])
        self.assertTrue(self.session.dispatch('submit', final)['ok'])
        saved = json.loads((self.root / 'result.json').read_text())
        self.assertEqual(len(saved['guide_reviews']), 3)
        self.assertEqual(saved['guide_reviews'][0]['evidence'][0]['quote'], 'Participant: I manage a clinic.\n')
        self.assertIn('Interview guide coverage', (self.root / 'evidence.md').read_text())

    def test_no_guide_means_grounding_not_applicable(self):
        other = Session(self.source, profile('Understand work'), self.root, 'review', TOOL_CALL_BUDGET)
        other.dispatch('read_lines', {'start': 1, 'end': 2})
        payload = self.payload()
        check = next(c for c in payload['checks'] if c['check_id'] == 'experiential_grounding')
        check.pop('guide_item_ids')
        self.assertFalse(other.dispatch('save_turn_review', payload)['ok'])
        check['finding'] = 'not_applicable'
        self.assertTrue(other.dispatch('save_turn_review', payload)['ok'])
        self.assertNotIn('save_guide_review', other.tools)
        self.assertIn('Not assessed: no interview guide', (self.root / 'evidence.md').read_text())

    def test_grounding_requires_correct_requirement_reference(self):
        payload = self.payload()
        check = next(c for c in payload['checks'] if c['check_id'] == 'experiential_grounding')
        for ids in [[], ['training'], ['invented']]:
            check['guide_item_ids'] = ids
            self.assertFalse(self.session.dispatch('save_turn_review', payload)['ok'])
        check['guide_item_ids'] = ['role']
        self.assertTrue(self.session.dispatch('save_turn_review', payload)['ok'])

    def test_excerpt_missing_topic_cannot_be_whole_interview_violation(self):
        self.session.metric['transcript_scope'] = 'excerpt'
        payload = self.coverage('training', 'not_observed', 'violation')
        self.assertFalse(self.session.dispatch('save_guide_review', payload)['ok'])
        payload['finding'] = 'unclear'
        self.assertTrue(self.session.dispatch('save_guide_review', payload)['ok'])

    def test_questions_alone_do_not_establish_coverage(self):
        payload = self.coverage()
        payload['evidence'] = [{'start': 1, 'end': 1}]
        self.assertFalse(self.session.dispatch('save_guide_review', payload)['ok'])
        payload['evidence'] = []
        self.assertFalse(self.session.dispatch('save_guide_review', payload)['ok'])

    def test_optional_omission_is_not_violation(self):
        payload = self.coverage('wishes', 'not_observed', 'violation')
        self.assertFalse(self.session.dispatch('save_guide_review', payload)['ok'])
        payload['finding'] = 'no_observed_violation'
        self.assertTrue(self.session.dispatch('save_guide_review', payload)['ok'])

    def test_boundary_or_condition_exception_and_revision(self):
        payload = self.coverage('training', 'not_applicable', 'not_applicable')
        self.assertFalse(self.session.dispatch('save_guide_review', payload)['ok'])
        payload['evidence'] = [{'start': 2, 'end': 2}]
        payload['reason'] = 'Scripted applicability exception; interpretation needs review.'
        self.assertTrue(self.session.dispatch('save_guide_review', payload)['ok'])
        # Code validates provenance, not whether the cited text truly supports this exception.
        revised = self.coverage('training', 'not_observed', 'violation')
        self.assertTrue(self.session.dispatch('save_guide_review', revised)['ok'])
        self.assertEqual(len(self.session.guide_reviews), 1)
        self.assertEqual(self.session.guide_reviews['training']['finding'], 'violation')

    def test_global_coverage_requires_all_lines_read(self):
        self.session.read.remove(1)
        self.assertFalse(self.session.dispatch('save_guide_review', self.coverage())['ok'])

    def test_guide_cli_profile_is_versioned_and_review_only(self):
        metric = metric_from(None, 'review', 'Understand work', None, self.guide_path, 'excerpt')
        self.assertEqual(metric['id'], 'utility_and_conduct_v2')
        self.assertEqual(metric['transcript_scope'], 'excerpt')
        self.assertEqual(len(metric['checks']), 14)
        with self.assertRaisesRegex(ValueError, 'review mode only'):
            metric_from(None, 'extract', guide_path=self.guide_path)


if __name__ == '__main__':
    unittest.main()
