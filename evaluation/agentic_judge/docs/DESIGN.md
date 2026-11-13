# Interview utility and interviewer conduct

Keep SparkMe's utility objective and define what information contributes to it. Assess interviewer conduct separately through concrete constraints on each proposed question or conversational turn, using the relevant interview history. The original labels in [metrics.txt](metrics.txt) are starting concepts, not parallel personality scores.

This is a working design to test on exchanges. The `review` mode implements a first text-based version; the definitions remain provisional and require calibration.

## Information within interview utility

Useful information improves our understanding of the participant in relation to the interview's purpose. It can concern experiences, circumstances, interpretations, reasoning, feelings, personal stakes, or relevant qualifications and tensions. Its value depends on what the history already establishes.

Information can be expressed verbally or emotionally. For example, the words “my manager checks my work” describe a situation; frustration, reassurance, or resignation in its delivery can add information about its significance. Emotional expression is evidence to interpret in context, not a direct measurement of an internal state. Text permits judgments from wording and annotations; audio also permits judgments from delivery.

Depth, specificity, and newly revealed aspects describe contributions to this understanding. They do not need separate utility scores. There is no additional “discovery” metric, and emotional disclosure is not an automatic bonus. More words, detail, intimacy, or emotional intensity do not necessarily add relevant information. “Participant-specific” means particular to their account; uniqueness across participants requires comparison data.

An account is sufficiently developed when it supports the interview's purpose. It need not contain every possible detail or emotion. Ask what remains consequentially unclear, rather than requiring every answer to fill a universal completeness checklist.

The current [planner](../../../src/agents/exploration_planner/exploration_planner.py) operationalizes utility using marginal topic coverage, turn cost, and emergence potential. Clarifying information is a refinement of the objective's interpretation; incorporating it into coverage judgments or planner calculations still requires an explicit implementation decision. This proposal does not remove the existing emergence term.

## Reclassifying the original interviewer qualities

Empathy, patience, and responsiveness should not all be dismissed as personality traits or reserved entirely for audio. Their broad labels are difficult to score, but particular behaviors can be assessed from text. Audio adds evidence about timing and delivery.

| Original label | Proposed treatment | What text can show | What audio can add |
| --- | --- | --- | --- |
| Discovery | Part of information gained within utility | Newly revealed or clarified content | Emotional expression or delivery that adds relevant meaning |
| Understanding | Accurate interpretation and adaptation | Preserving meaning and incorporating corrections | Prosodic context that may qualify the words |
| Responsiveness and interactivity | Accurate interpretation and adaptation | Addressing the answer actually given; reformulating a question that was misunderstood | Turn coordination, overlap, and interruption |
| Empathy | Appropriate handling of expressed emotion or difficulty | Minimization, imposed feelings, or a response that acknowledges the stated concern | Whether delivery fits or conflicts with the wording |
| Patience | Respect for boundaries and conversational pace | Honoring explicit pause requests; handling an unfinished answer without pretending it was complete | Waiting, premature interruption, and resumption, where turn timing is available |
| Non-leading | Neutral elicitation | Unsupported premises, presupposed answers, or pressure toward agreement | Emphasis that may pressure or suggest an answer |
| Nonjudgmental | Neutral elicitation | Approval, disapproval, or moral framing that privileges an answer | Evaluative or dismissive delivery |
| Rapport | Leave unresolved as a separate assessment | Local exchanges can be described, but warmth or agreement alone does not establish rapport | Additional interaction evidence, without establishing internal trust by itself |

## Proposed conduct checks

Use six groups to organize fourteen concrete checks. The groups are not replacement Likert scores. Checks describe observable behavior, not the interviewer's personality or intentions.

### Accurate interpretation and adaptation

- Does the proposed turn misrepresent an answer, erase an important qualification, or rely on an interpretation the participant rejected?
- Does it treat a visibly unfinished answer as though the requested information was provided?
- When the participant signals misunderstanding or says a question does not fit, does the interviewer repeat the same framing without addressing that signal?

An unrelated next question is not automatically a violation. Choosing whether a promising thread merits further probing belongs to expected utility and planning. This group checks failures to account for the participant's actual answer or an explicit need for repair.

### Neutral elicitation

- Does the question introduce an unsupported premise or presume a particular answer?
- Does it pressure agreement or persist with an interpretation after its rejection?
- Does approval, disapproval, or moral framing make one answer appear more acceptable than another?

Suggested interpretations and requests for reasons are not automatically violations. Judge whether the wording allows correction and alternatives, and whether the history supports the premise. Brief acknowledgment is not automatically steering praise.

### Respect for boundaries and conversational pace

- Does the turn disregard a request to pause, stop, skip, or avoid a topic?
- Does it pressure an answer after the participant says they are unwilling or unable to provide one?
- Does it resume an explicitly excluded topic without renewed permission?

A refusal remains relevant beyond the immediately preceding turn. Without timing evidence, do not infer impatient interruption from adjacent transcript lines. An unfinished answer may also trigger an accurate-interpretation check; overlapping findings should refer to the same incident rather than inflate a violation count.

### Appropriate handling of emotion and difficulty

- When the participant expresses emotion or difficulty, does the turn minimize, dismiss, contradict, or replace that account with an unsupported emotional interpretation?
- Does it press for emotional or intimate disclosure beyond an expressed boundary?

The interviewer need not produce sympathetic language after every emotional statement. A relevant neutral question may fit the exchange. Silence about emotion is not by itself evidence of deficient empathy, and vocal expression alone may leave the emotion's interpretation unclear.

### Question clarity and burden

Does unclear wording or bundling competing requests make the turn materially difficult to answer? Judge the actual burden in context. Related clarifications, several connected clauses, or a short factual question are not automatically violations. This is a constraint on answerability, not a requirement for one fixed question style.

### Protocol grounding

When an applicable interview-guide requirement explicitly calls for first-hand evidence, does the turn substitute hearsay, generic speculation, or hypothetical accounts for the participant's experience or observations? Background, reflection, and future-oriented questions can still serve the purpose. Apply this check only to an explicit `first_hand` requirement supplied through `--guide`; otherwise mark it not applicable.

## Whole-interview guide coverage

Some failures concern the interview as a whole rather than one question. An optional JSON guide declares information requirements, their required/optional status, and any applicability conditions. Judge whether each requirement was covered anywhere in the supplied transcript, using participant evidence rather than the presence of a question. Later answers and volunteered information can satisfy it.

Record covered, partial, not observed, unclear, or not applicable coverage, with reasons, exact evidence, and missing information. Missing required information may establish an omission when the complete interview supports that judgment; explicit refusals or inapplicable conditions can justify an exception. An optional omission is not a violation. For excerpts, absence cannot establish a whole-interview omission. Without a guide, coverage is unassessed; the research purpose alone does not define mandatory topics.

This adds a global constraint check, not another utility dimension or a Likert scale. Utility still concerns relevant information obtained and its novelty; conduct still concerns contextual constraints. Neither check proves that a different question would have obtained a better answer.

## Judging a proposed question in context

The unit is the proposed interviewer turn, including any acknowledgment or framing before the question. Supply the interview purpose, relevant history, corrections, and active boundaries. The same wording can be appropriate in one history and leading in another: “Why were you frustrated?” has a different premise after an explicit statement of frustration than after a neutral account.

For each applicable check, record one finding:

- **Violation:** the wording and context provide evidence that the constraint is breached.
- **No observed violation:** the available evidence does not show a breach; this does not establish ideal interviewing.
- **Unclear:** the available context or modality does not support a reliable judgment.
- **Not applicable:** the triggering situation is absent, such as an emotion-specific check when no emotion is expressed.

Record the check, source lines, explanation, and relevant earlier context. A suggested revision can help make the finding actionable. Do not infer that an unasked alternative would necessarily produce a better response.

For generation, the intended workflow is: propose questions, check conduct, revise or exclude clear violations, and select among acceptable candidates using expected utility. Some constraints concern observable wording directly; others require contextual judgment. The handling of unclear findings remains a policy choice. Adding an instruction to a prompt does not itself guarantee compliance.

For retrospective evaluation, assess the information actually obtained and document conduct findings separately. A rich participant answer does not excuse a constraint violation or establish that the question maximized expected utility. An uninformative response does not by itself establish a poor question.

## Implications for the agentic evaluator

The harness supports `review` mode alongside generic extraction and rubric evaluation. It reads and searches one transcript, records all fourteen conduct checks for each detected interviewer turn, and documents information gained separately from its subsequent response. Final submission requires complete line and turn coverage, a review of every supplied guide item, and separate utility and conduct syntheses. A brief negative answer can add information when it resolves a relevant uncertainty. Source quotes and temporal citation boundaries are checked in code; interpretations still require review. The existing numerical SparkMe utility calculation is not executed or replaced by this qualitative report.

First test the checks on a small set of exchanges, including ambiguous cases and cases without violations. Revise checks that produce unhelpful or inconsistent judgments. Do not collect a fixed quota of positive examples or sum overlapping findings into an overall interviewer score. Audio-specific findings should remain unclear when only text is available.


## Concurrency and shared prefixes

There is one runner and one queue, accepting one or more transcripts. Review tasks are independent interviewer-turn assessments with the full earlier history and following response; later turns are hidden. A single global `--workers` limit controls active agents. Once all turn tasks for a transcript succeed, its synthesis/guide agent joins the same queue. There is no separate batch mode or per-transcript worker pool. GPU/model deployments belong to Slurm and the deployment script, not the runner.

The runner validates and merges saved turn findings. The synthesis agent must read the complete transcript and every saved turn review before submission; it cannot overwrite worker judgments. Failed tasks block that transcript's final submission while independent tasks continue. Cancellation stops active agent processes and preserves partial findings. Each backend process isolates its mutable SDK state while using the same configured model endpoint.

The question and following response are both visible, so conduct/utility separation still depends on the temporal judgment rule and citation validation. Receiving a numbered inline prefix counts as reading the supplied source lines. Source hashes and original numbering are retained.

Shared instructions/profile and numbered history precede assignment-specific details, allowing exact-prefix KV reuse where supported. Prefix caching reduces repeated prefill work; server batching can improve aggregate decode throughput. Neither guarantees speedup or judgment accuracy. These execution choices do not add evaluation dimensions or scores.
