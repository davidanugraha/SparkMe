"""Shared transcript tools exposed by the Codex and Qwen adapters."""
from typing import Annotated, Any, Literal

from langchain_core.tools import BaseTool
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, FiniteFloat, StrictInt

from ..judging import guide, turns


MAX_READ_LINES = 200
MAX_READ_CHARACTERS = 24_000
SEARCH_PAGE_SIZE = 50
DEFAULT_SEARCH_OFFSET = 0


def require_nonblank(value: str) -> str:
    if not value.strip():
        raise ValueError('Text must contain a non-whitespace character')
    return value


NonBlank = Annotated[str, Field(min_length=1), AfterValidator(require_nonblank)]
LineNumber = Annotated[int, Field(ge=1)]
Finding = Literal[tuple(turns.FINDINGS)]
InformationGain = Literal[tuple(turns.GAINS)]
CheckID = Literal[tuple(turns.CHECKS)]
Coverage = Literal[tuple(guide.COVERAGE)]
EvidenceKind = Literal['supporting', 'contradicting', 'uncertain']


class ToolInput(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')


class LineRangeInput(ToolInput):
    start: LineNumber
    end: LineNumber


class SearchInput(ToolInput):
    text: NonBlank
    offset: int = Field(default=DEFAULT_SEARCH_OFFSET, ge=DEFAULT_SEARCH_OFFSET)


class SaveEvidenceInput(LineRangeInput):
    interpretation: NonBlank
    kind: EvidenceKind
    # Omitting the field creates an ID. An explicitly supplied ID must be text.
    evidence_id: NonBlank = Field(default_factory=lambda: None)


class EvidenceIDInput(ToolInput):
    evidence_id: NonBlank


class TurnIDInput(ToolInput):
    turn_id: NonBlank


class ConductCheckInput(ToolInput):
    check_id: CheckID
    finding: Finding
    reason: NonBlank
    evidence: list[LineRangeInput]
    guide_item_ids: list[NonBlank] = Field(default_factory=list)


class UtilityInput(ToolInput):
    information_gain: InformationGain
    account: NonBlank
    evidence: list[LineRangeInput]
    limitations: str


class SaveTurnReviewInput(TurnIDInput):
    checks: list[ConductCheckInput] = Field(min_length=len(turns.CHECKS), max_length=len(turns.CHECKS))
    utility: UtilityInput


class SaveGuideReviewInput(ToolInput):
    item_id: NonBlank
    coverage: Coverage
    finding: Finding
    reason: NonBlank
    evidence: list[LineRangeInput]
    missing_information: str
    limitations: str


class SubmitInput(ToolInput):
    summary: NonBlank
    limitations: str
    score: StrictInt | FiniteFloat = Field(default_factory=lambda: None)


class SubmitReviewInput(ToolInput):
    summary: NonBlank
    limitations: str
    utility_summary: NonBlank
    conduct_summary: NonBlank


class TranscriptTool(BaseTool):
    """Bind a LangChain tool to the transcript session it can access."""

    session: Any = Field(exclude=True, repr=False)
    args_schema: type[BaseModel]

    @property
    def parameters(self):
        parameters = convert_to_openai_tool(self.args_schema)['function']['parameters']
        # Qwen-Agent expects an explicit required list, including for tools with no arguments.
        parameters.setdefault('required', [])
        return parameters

    def call(self, arguments):
        validated = self.args_schema.model_validate(arguments)
        supplied = {name: value for name, value in validated if name in validated.model_fields_set}
        return self._run(**supplied)


class ReadLines(TranscriptTool):
    name: str = 'read_lines'
    description: str = f'Read an inclusive range of original transcript lines (1-based, at most {MAX_READ_LINES}).'
    args_schema: type[BaseModel] = LineRangeInput

    def _run(self, start, end):
        session = self.session
        session.range(start, end)
        line_numbers = range(start, end + 1)
        session.read.update(line_numbers)
        return [{'line': number, 'text': session.lines[number - 1]} for number in line_numbers]


class Search(TranscriptTool):
    name: str = 'search'
    description: str = 'Grep-style literal search in this transcript only. Returns matching line numbers; read surrounding lines for context.'
    args_schema: type[BaseModel] = SearchInput

    def _run(self, text, offset=DEFAULT_SEARCH_OFFSET):
        session = self.session
        query = text.casefold()
        matches = [number for number, line in enumerate(session.lines, 1) if query in line.casefold()]
        page = matches[offset:offset + SEARCH_PAGE_SIZE]
        next_offset = offset + len(page)
        return {
            'matching_lines': page,
            'total_matches': len(matches),
            'next_offset': next_offset if next_offset < len(matches) else None,
        }


class SaveEvidence(TranscriptTool):
    name: str = 'save_evidence'
    description: str = 'Add evidence, or replace it by ID. Quotes are extracted by the harness. Read the full range first.'
    args_schema: type[BaseModel] = SaveEvidenceInput

    def _run(self, **arguments):
        session = self.session
        start, end = arguments['start'], arguments['end']
        quote = session.range(start, end)
        if not set(range(start, end + 1)) <= session.read:
            raise ValueError('Read the cited lines before saving evidence')
        evidence_id = arguments.get('evidence_id')
        if evidence_id is not None and evidence_id not in session.evidence:
            raise ValueError('Unknown evidence ID')
        if evidence_id is None:
            evidence_id = f'E{session.next_id}'
            session.next_id += 1
        record = {**arguments, 'evidence_id': evidence_id, 'quote': quote}
        session.evidence[evidence_id] = record
        return record


class ListEvidence(TranscriptTool):
    name: str = 'list_evidence'
    description: str = 'Read the current evidence notebook.'
    args_schema: type[BaseModel] = ToolInput

    def _run(self):
        session = self.session
        return list(session.evidence.values())


class RemoveEvidence(TranscriptTool):
    name: str = 'remove_evidence'
    description: str = 'Remove an evidence entry by ID.'
    args_schema: type[BaseModel] = EvidenceIDInput

    def _run(self, evidence_id):
        session = self.session
        del session.evidence[evidence_id]
        return 'Removed'


class ReviewOverview(TranscriptTool):
    name: str = 'review_overview'
    description: str = 'List every interviewer turn and response range, check definitions, and progress. Verify segmentation by reading original lines.'
    args_schema: type[BaseModel] = ToolInput

    def _run(self):
        return turns.overview(self.session)


class ReadTurnReview(TranscriptTool):
    name: str = 'read_turn_review'
    description: str = 'Read a saved assessment before revising it.'
    args_schema: type[BaseModel] = TurnIDInput

    def _run(self, turn_id):
        session = self.session
        record = session.reviews[turn_id]
        session.review_reads.add(turn_id)
        return record


class SaveTurnReview(TranscriptTool):
    name: str = 'save_turn_review'
    description: str = (
        'Save or replace one interviewer-turn assessment. Provide each conduct check exactly once; '
        'use only history available at that turn. Separately assess information added by its subsequent participant response.'
    )
    args_schema: type[BaseModel] = SaveTurnReviewInput

    def _run(self, turn_id: str, checks: list[ConductCheckInput], utility: UtilityInput):
        return turns.save_review(
            self.session,
            turn_id=turn_id,
            checks=[check.model_dump(exclude_unset=True) for check in checks],
            utility=utility.model_dump(exclude_unset=True),
        )


class SaveGuideReview(TranscriptTool):
    name: str = 'save_guide_review'
    description: str = (
        'Save or replace a whole-transcript review of one provided guide item after reading all lines. '
        'Cite participant information for coverage; explain omissions, conditions and legitimate exceptions.'
    )
    args_schema: type[BaseModel] = SaveGuideReviewInput

    def _run(self, evidence: list[LineRangeInput], **arguments):
        return guide.save_review(self.session, evidence=[span.model_dump() for span in evidence], **arguments)


class Submit(TranscriptTool):
    name: str = 'submit'
    description: str = (
        'Finish with an evidence-grounded summary and limitations. '
        'Evaluation also requires a rubric score and reading the whole transcript.'
    )
    args_schema: type[BaseModel] = SubmitInput

    def _run(self, **submission):
        session = self.session
        score = submission.get('score')
        if session.mode == 'evaluate':
            if len(session.read) != len(session.lines):
                raise ValueError('Read the entire transcript before evaluating')
            rubric = session.metric['rubric']
            if score is None or not rubric['min_score'] <= score <= rubric['max_score']:
                raise ValueError('Provide a score within the rubric bounds')
        elif score is not None:
            raise ValueError('Extraction does not assign a score')
        session.state.update(status='completed', submission=submission)
        session.done = True
        return 'Submission accepted'


class SubmitReview(Submit):
    description: str = (
        'Finalize only after reading all lines, reviewing every interviewer turn, and reviewing every supplied guide item. '
        'Provide separate utility and conduct syntheses.'
    )
    args_schema: type[BaseModel] = SubmitReviewInput

    def _run(self, **submission):
        turns.validate_submission(self.session, submission)
        return super()._run(**submission)
