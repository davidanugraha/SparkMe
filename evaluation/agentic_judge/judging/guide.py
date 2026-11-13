"""Explicit interview information requirements and whole-transcript coverage review."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

GROUNDING = ('unspecified', 'first_hand')
COVERAGE = ('covered', 'partial', 'not_observed', 'unclear', 'not_applicable')


def load_guide(path: Path):
    raw = path.read_bytes()
    data = json.loads(raw.decode('utf-8'))
    if not isinstance(data, dict) or set(data) - {'title', 'grounding', 'items'}:
        raise ValueError('Guide must be an object with title, optional grounding, and items')
    if not isinstance(data.get('title'), str) or not data['title'].strip():
        raise ValueError('Guide title must be nonempty')
    grounding = data.get('grounding', 'unspecified')
    if grounding not in GROUNDING:
        raise ValueError('Guide grounding must be unspecified or first_hand')
    if not isinstance(data.get('items'), list) or not data['items']:
        raise ValueError('Guide items must be a nonempty list of information requirements')
    items, ids = [], set()
    for item in data['items']:
        if not isinstance(item, dict) or set(item) - {'id', 'description', 'required', 'grounding', 'condition'}:
            raise ValueError('Invalid guide item fields')
        for key in ('id', 'description'):
            if not isinstance(item.get(key), str) or not item[key].strip():
                raise ValueError(f'Guide item {key} must be nonempty')
        if item['id'] in ids:
            raise ValueError('Guide item IDs must be unique')
        ids.add(item['id'])
        if type(item.get('required', True)) is not bool:
            raise ValueError('Guide item required must be a boolean')
        if item.get('grounding', grounding) not in GROUNDING:
            raise ValueError('Invalid guide item grounding')
        if 'condition' in item and (not isinstance(item['condition'], str) or not item['condition'].strip()):
            raise ValueError('Guide item condition must be nonempty when provided')
        items.append({**item, 'required': item.get('required', True), 'grounding': item.get('grounding', grounding)})
    return {'title': data['title'], 'items': items, 'source_name': path.name,
            'sha256': hashlib.sha256(raw).hexdigest()}


def validate_grounding(session, check):
    guide = session.metric.get('guide')
    items = {item['id']: item for item in guide['items']} if guide else {}
    references = check.get('guide_item_ids', [])
    if len(references) != len(set(references)) or any(key not in items for key in references):
        raise ValueError('Unknown or repeated guide item reference')
    if check['check_id'] != 'experiential_grounding':
        return
    grounded = {key for key, item in items.items() if item['grounding'] == 'first_hand'}
    if not grounded and check['finding'] != 'not_applicable':
        raise ValueError('Experiential grounding is not_applicable without an explicit first_hand guide requirement')
    if check['finding'] in ('violation', 'no_observed_violation') and (not references or not set(references) <= grounded):
        raise ValueError('Grounding findings must reference applicable first_hand guide item IDs')


def save_review(session, item_id, coverage, finding, reason, evidence, missing_information, limitations):
    # Import here to keep schema and quote validation in the existing assessment module.
    from .turns import quoted_ranges
    guide = session.metric.get('guide')
    item = next((item for item in guide['items'] if item['id'] == item_id), None) if guide else None
    if item is None:
        raise ValueError('Unknown guide item ID; supply --guide for guide coverage review')
    if len(session.read) != len(session.lines):
        raise ValueError('Read the entire transcript before judging guide coverage')
    if coverage in ('covered', 'partial', 'not_applicable') and not evidence:
        raise ValueError('Coverage, partial coverage, and applicability exceptions require transcript evidence')
    if coverage in ('partial', 'not_observed') and not missing_information.strip():
        raise ValueError('Explain what required information is missing')
    if coverage == 'covered' and (finding != 'no_observed_violation' or missing_information.strip()):
        raise ValueError('Covered items must have no_observed_violation and no missing information')
    if coverage == 'not_applicable' and finding != 'not_applicable':
        raise ValueError('Applicability exceptions must use not_applicable')
    if coverage == 'unclear' and finding != 'unclear':
        raise ValueError('Unclear coverage must have an unclear finding')
    if finding == 'violation':
        if not item['required'] or coverage not in ('partial', 'not_observed'):
            raise ValueError('Only an unmet required information item can be a coverage violation')
        if session.metric.get('transcript_scope') == 'excerpt':
            raise ValueError('Missing information in an excerpt cannot establish a whole-interview omission; use unclear')
    if item['required'] and coverage in ('partial', 'not_observed'):
        if finding not in ('violation', 'unclear'):
            raise ValueError('Unmet required items must be violation or unclear; use not_applicable with evidence for a legitimate exception')
    if not item['required'] and coverage in ('partial', 'not_observed') and finding != 'no_observed_violation':
        raise ValueError('An optional omission is no_observed_violation')
    quotes = quoted_ranges(session, evidence)
    # Guide items describe information sought, not procedural actions. Asking a
    # question alone does not establish that the information was obtained.
    if coverage in ('covered', 'partial'):
        interviewer_lines = {n for turn in session.state['interviewer_turns'] for n in range(turn['start'], turn['end'] + 1)}
        if not any(n not in interviewer_lines for span in evidence for n in range(span['start'], span['end'] + 1)):
            raise ValueError('Coverage must cite participant information, not only interviewer questions')
    session.guide_reviews[item_id] = {'item_id': item_id, 'coverage': coverage, 'finding': finding,
        'reason': reason, 'evidence': quotes, 'missing_information': missing_information, 'limitations': limitations}
    return {'item_id': item_id, 'reviewed_items': len(session.guide_reviews), 'total_items': len(guide['items'])}


def report_lines(state):
    guide = state['metric'].get('guide')
    lines = ['## Interview guide coverage', '']
    if state.get('review_stage') == 'turn':
        return lines + ['Deferred: this worker reviews one turn. Whole-interview coverage is assessed in the synthesis pass.', '']
    if not guide:
        return lines + ['Not assessed: no interview guide was supplied. Required coverage and first-hand grounding are not inferred from the interview purpose alone.', '']
    records = {r['item_id']: r for r in state.get('guide_reviews', [])}
    lines += [guide['title'], '', f'Transcript scope: {state["metric"].get("transcript_scope", "complete")}. '
              f'{len(records)} of {len(guide["items"])} guide items reviewed.', '',
              'These are whole-transcript information requirements. A question being asked is not sufficient evidence of coverage.', '']
    for item in guide['items']:
        lines += [f'### Guide item {item["id"]}', '', item['description'], '',
                  f'Required: {item["required"]}. Grounding: {item["grounding"]}.', '']
        if item.get('condition'):
            lines += ['Condition: ' + item['condition'], '']
        record = records.get(item['id'])
        if record is None:
            lines += ['Not yet reviewed.', '']
            continue
        lines += [f'Coverage: **{record["coverage"]}**. Conduct finding: **{record["finding"]}**.', '', record['reason'], '']
        for span in record['evidence']:
            lines += [f'Transcript lines {span["start"]}–{span["end"]}:', '', *['> ' + x for x in span['quote'].splitlines()], '']
        if record['missing_information']:
            lines += ['Missing information: ' + record['missing_information'], '']
        if record['limitations']:
            lines += ['Limitations: ' + record['limitations'], '']
    return lines
