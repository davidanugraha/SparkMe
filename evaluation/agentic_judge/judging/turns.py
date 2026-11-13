"""Contextual per-turn conduct checks and qualitative interview information gains."""
from __future__ import annotations

from collections import Counter
import re

from . import guide as guide_review

TURN_ID_WIDTH = 3
MAX_SPEAKER_LABEL_LENGTH = 80
LOG_PREFIX = re.compile(r'^\d{4}-\d{2}-\d{2}[ T].*? - (?:INFO|DEBUG|WARNING|ERROR) - ')
SPEAKER_PREFIX = re.compile(rf'^\s*([^:\n]{{1,{MAX_SPEAKER_LABEL_LENGTH}}}):\s*')

CHECKS = {
    'meaning': ('Accurate interpretation and adaptation', 'Does the turn misrepresent an answer or discard an important qualification?'),
    'correction': ('Accurate interpretation and adaptation', 'Does it retain an interpretation the participant explicitly corrected or rejected?'),
    'incomplete_answer': ('Accurate interpretation and adaptation', 'Does it treat a visibly unfinished or absent answer as though the requested information was supplied?'),
    'repair': ('Accurate interpretation and adaptation', 'After explicit confusion or a question-fit problem, does it repeat the unsuccessful framing without addressing that signal?'),
    'premise': ('Neutral elicitation', 'Does it introduce an unsupported premise or presume a particular answer?'),
    'pressure': ('Neutral elicitation', 'Does it pressure agreement or prevent a suggested interpretation from being rejected?'),
    'judgment': ('Neutral elicitation', 'Does approval, disapproval, or moral framing privilege a particular account or answer?'),
    'boundary': ('Boundaries and conversational pace', 'Does it disregard a refusal or excluded topic, pressure an unwilling participant, or reopen that topic without renewed permission?'),
    'pause': ('Boundaries and conversational pace', 'Does it disregard an explicit request to pause, skip, or stop?'),
    'dismissal': ('Handling emotion and difficulty', 'When emotion or difficulty is expressed, does it minimize or dismiss that account?'),
    'imposed_emotion': ('Handling emotion and difficulty', 'Does it attribute a feeling or emotional meaning unsupported by the participant account?'),
    'disclosure_pressure': ('Handling emotion and difficulty', 'Does it pressure emotional or intimate disclosure beyond an expressed boundary?'),
    'question_burden': ('Question clarity and burden', 'Does unclear wording or bundling competing requests make the turn materially difficult to answer? Related clarifications and a short factual question are not automatically violations.'),
    'experiential_grounding': ('Protocol grounding', 'When an applicable guide item requires first-hand information, does the turn substitute hearsay, generic speculation, or hypothetical accounts for the participant experience or observations sought?'),
}
FINDINGS = ['violation', 'no_observed_violation', 'unclear', 'not_applicable']
GAINS = ['adds_information', 'repeats_information', 'no_substantive_response', 'unclear']


def profile(purpose, labels=None, guide=None, transcript_scope='complete'):
    return {'id': 'utility_and_conduct_v2', 'description': 'Assess information gained, contextual interviewer conduct, and explicitly supplied guide coverage.',
            'interview_purpose': purpose, 'interviewer_labels': labels or ['Interviewer'],
            'checks': {key: {'group': group, 'question': question} for key, (group, question) in CHECKS.items()},
            'modality': 'text', 'guide': guide, 'transcript_scope': transcript_scope}


def segment(lines, labels):
    """Recognize speaker-prefixed plain text and timestamped logs; retain original lines."""
    headers = []
    for number, line in enumerate(lines, 1):
        text = LOG_PREFIX.sub('', line)
        match = SPEAKER_PREFIX.match(text)
        if match:
            headers.append((number, match[1].strip()))
    known = {label.casefold() for label in labels}
    turns = []
    for index, (start, speaker) in enumerate(headers):
        if speaker.casefold() not in known:
            continue
        end = headers[index + 1][0] - 1 if index + 1 < len(headers) else len(lines)
        next_interviewer = next((n for n, label in headers[index + 1:] if label.casefold() in known), len(lines) + 1)
        response = {'start': end + 1, 'end': next_interviewer - 1} if end + 1 < next_interviewer else None
        turns.append({'turn_id': f'T{len(turns) + 1:0{TURN_ID_WIDTH}d}', 'speaker': speaker,
                      'start': start, 'end': end, 'response': response})
    if not turns:
        raise ValueError('No interviewer turns found; set --interviewer-label to the transcript speaker label')
    return turns


def overview(session):
    return {'turns': session.state['interviewer_turns'], 'checks': session.metric['checks'],
            'reviewed_turn_ids': list(session.reviews),
            'remaining_turn_ids': [t['turn_id'] for t in session.state['interviewer_turns'] if t['turn_id'] not in session.reviews],
            'interview_purpose': session.metric['interview_purpose'], 'modality': 'text',
            'review_stage': session.review_stage,
            'unread_review_ids': [key for key in session.reviews if key not in session.review_reads] if session.review_stage == 'synthesis' else [],
            'guide': session.metric.get('guide'), 'transcript_scope': session.metric.get('transcript_scope', 'complete'),
            'guide_reviews': list(session.guide_reviews.values()),
            'remaining_guide_item_ids': [item['id'] for item in (session.metric.get('guide') or {}).get('items', []) if item['id'] not in session.guide_reviews]}


def quoted_ranges(session, ranges, max_end=None, within=None):
    result = []
    for item in ranges:
        start, end = item['start'], item['end']
        quote = session.range(start, end)
        if not set(range(start, end + 1)) <= session.read:
            raise ValueError('Read every cited line before submitting an assessment')
        if max_end is not None and end > max_end:
            raise ValueError('Conduct evidence cannot use a response or event after the interviewer turn')
        if within is not None and not within['start'] <= start <= end <= within['end']:
            raise ValueError('Utility evidence must come from the response to this turn')
        result.append({**item, 'quote': quote})
    return result


def save_review(session, turn_id, checks, utility):
    turn = next((t for t in session.state['interviewer_turns'] if t['turn_id'] == turn_id), None)
    if turn is None:
        raise ValueError('Unknown interviewer turn ID')
    if {c['check_id'] for c in checks} != set(CHECKS) or len(checks) != len(CHECKS):
        raise ValueError('Include every check exactly once')
    if not set(range(turn['start'], turn['end'] + 1)) <= session.read:
        raise ValueError('Read the interviewer turn before reviewing it')
    response = turn['response']
    if response and not set(range(response['start'], response['end'] + 1)) <= session.read:
        raise ValueError('Read the subsequent response before assessing information gained')
    if not response and utility['information_gain'] != 'no_substantive_response':
        raise ValueError('A turn without a response must have no_substantive_response')
    if utility['information_gain'] in ('adds_information', 'repeats_information') and not utility['evidence']:
        raise ValueError('Information judgments require response evidence')
    if not response and utility['evidence']:
        raise ValueError('No response exists to cite')
    saved_checks = []
    for check in checks:
        guide_review.validate_grounding(session, check)
        if check['finding'] in ('violation', 'no_observed_violation') and not check['evidence']:
            raise ValueError('Violations and no-observed-violation findings require evidence')
        saved_checks.append({**check, 'evidence': quoted_ranges(session, check['evidence'], max_end=turn['end'])})
    saved_utility = {**utility, 'evidence': quoted_ranges(session, utility['evidence'], within=response)}
    session.reviews[turn_id] = {**turn, 'quote': session.range(turn['start'], turn['end']),
                                'checks': saved_checks, 'utility': saved_utility}
    # Return a receipt rather than echoing all arguments and quotes into model context.
    return {'turn_id': turn_id, 'reviewed_turns': len(session.reviews),
            'total_turns': len(session.state['interviewer_turns']),
            'findings': dict(Counter(c['finding'] for c in checks))}


def validate_submission(session, args):
    if len(session.read) != len(session.lines):
        raise ValueError('Read the entire transcript before final review submission')
    missing = [t['turn_id'] for t in session.state['interviewer_turns'] if t['turn_id'] not in session.reviews]
    if missing:
        raise ValueError('Missing turn reviews: ' + ', '.join(missing))
    if session.review_stage == 'synthesis' and set(session.reviews) - session.review_reads:
        raise ValueError('Read every saved turn review before submitting the synthesis')
    missing_items = [item['id'] for item in (session.metric.get('guide') or {}).get('items', []) if item['id'] not in session.guide_reviews] if session.review_stage != 'turn' else []
    if missing_items:
        raise ValueError('Missing guide reviews: ' + ', '.join(missing_items))
    if not args.get('utility_summary', '').strip() or not args.get('conduct_summary', '').strip():
        raise ValueError('Review submission requires separate utility_summary and conduct_summary')
    if 'score' in args:
        raise ValueError('Review mode does not add a utility or conduct Likert score')


def report_lines(state):
    turns, reviews = state['interviewer_turns'], state['turn_reviews']
    lines = ['## Review coverage', '', f'{len(reviews)} of {len(turns)} interviewer turns reviewed.', '',
             'Purpose: ' + state['metric']['interview_purpose'], '',
             'Conduct findings are contextual judgments. Information gained is assessed separately; no combined score is computed.', '']
    if state.get('review_stage') == 'turn':
        lines += ['This is one parallel turn task. Later transcript lines are hidden; guide coverage belongs to the final whole-interview pass.', '']
    counts = Counter(c['finding'] for r in reviews for c in r['checks'])
    lines.extend(['Findings: ' + ', '.join(f'{name}: {counts[name]}' for name in FINDINGS), '',
                  'Counts are check findings, not independent incidents or an overall quality score.', ''])
    flagged = [(review, check) for review in reviews for check in review['checks']
               if check['finding'] in ('violation', 'unclear')]
    lines.extend(['## Conduct findings to inspect', ''])
    if not flagged:
        lines.extend(['No violations or unclear findings recorded so far.', ''])
    for review, check in flagged:
        lines.append(f'- {review["turn_id"]} (interviewer lines {review["start"]}–{review["end"]}), '
                     f'{check["check_id"]}: **{check["finding"]}**. {check["reason"]}')
    lines.extend(['', '## Check definitions', '', '| Check | Group | Question |', '| --- | --- | --- |'])
    for name, definition in state['metric']['checks'].items():
        lines.append(f'| {name} | {definition["group"]} | {definition["question"]} |')
    lines.extend(['', '## Per turn assessments', ''])
    for turn in turns:
        review = next((r for r in reviews if r['turn_id'] == turn['turn_id']), None)
        lines.extend([f'### {turn["turn_id"]} interviewer lines {turn["start"]} to {turn["end"]}', ''])
        if review is None:
            lines.extend(['Not yet reviewed.', ''])
            continue
        lines.extend(['> ' + part for part in review['quote'].splitlines()])
        lines.extend(['', '**Information gained**: ' + review['utility']['information_gain'], '', review['utility']['account'], ''])
        for span in review['utility']['evidence']:
            lines.extend([f'Response lines {span["start"]} to {span["end"]}:', '', *['> ' + part for part in span['quote'].splitlines()], ''])
        if review['utility']['limitations']:
            lines.extend(['Utility limitations: ' + review['utility']['limitations'], ''])
        lines.extend(['| Conduct check | Finding | Explanation and source lines |', '| --- | --- | --- |'])
        for check in review['checks']:
            refs = ', '.join(f'{s["start"]}–{s["end"]}' for s in check['evidence'])
            reason = check['reason'].replace('|', '\\|').replace('\n', ' ')
            guides = ', '.join(check.get('guide_item_ids', []))
            suffix = f'; guide items {guides}' if guides else ''
            lines.append(f'| {check["check_id"]} | {check["finding"]} | {reason} (lines {refs or "not cited"}{suffix}) |')
        lines.append('')
    lines.extend(guide_review.report_lines(state))
    if 'submission' in state:
        final = state['submission']
        lines.extend(['## Utility synthesis', '', final['utility_summary'], '', '## Conduct synthesis', '', final['conduct_summary'], ''])
    return lines
