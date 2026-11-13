# Agentic interview judge

One deployed model endpoint, one runner, one queue of evaluation tasks. Give the runner one transcript or several; `--workers` controls the maximum number of concurrent agents across the entire run. Each agent connects to the same endpoint. The runner does not deploy model replicas or assign GPUs.

The judge records useful information obtained from participant responses and contextual constraints on interviewer conduct. See [the evaluation design](docs/DESIGN.md) for the provisional criteria. These are qualitative findings, not a new numerical utility or personality score.

## Run

Run from the **SparkMe repository root**, using Python 3.10 or later. Install `evaluation/agentic_judge/requirements.txt` in the client environment for Codex. For Qwen, install `evaluation/agentic_judge/requirements-qwen.txt`, which includes the shared dependencies, and start a compatible vLLM endpoint with native tool parsing.

```bash
python -m evaluation.agentic_judge.runner \
  --transcripts /path/to/interviews/*.txt \
  --backend qwen --model Qwen/Qwen3.8-27B \
  --base-url http://127.0.0.1:8000/v1 \
  --qwen-native-tools --qwen-thinking off \
  --purpose "Understand the participant's experiences of AI at work." \
  --workers 4 \
  --output evaluation/agentic_judge/runs/my-review
```

Use the same command with one path for a single transcript. Plain `Interviewer: ...` / `Participant: ...` text is supported. Use `--interviewer-label Researcher` for a different prefix; repeat it for multiple interviewers. Inspect the detected turn ranges to confirm segmentation. `review` is the default mode.

On this cluster, the existing Qwen client environment is:

```bash
PYTHONPATH=/project/def-alee/danu/.cache/sparkme/qwen-agent-deps \
/project/def-alee/danu/saved_env/user_sim/bin/python \
  -m evaluation.agentic_judge.runner --help
```

For Codex, use `--backend codex` and omit the Qwen model/server arguments. The Codex CLI must already be installed and authenticated. Its provider receives transcript content; the Qwen backend sends it to the configured endpoint. Use the inference location authorized for your transcripts.

## What the runner does

1. Read and segment each transcript into interviewer turns and their following responses.
2. Queue independent turn-review tasks. Each receives all earlier history, its assigned interviewer turn, and the following participant response. Later turns are hidden from read/search tools.
3. Run up to `--workers` agent tasks concurrently, across all inputs. Inputs are interleaved so one long transcript does not occupy the entire waiting queue.
4. Save and validate each turn's conduct findings and information gained. Conduct uses history up to the interviewer turn; information gained uses its following response.
5. Once a transcript's turn tasks have completed successfully, queue its whole-interview synthesis task. This agent must read the full source and every saved turn review, assess any supplied guide, and submit the final report. It uses the same worker pool.

The assessment unit is an **interviewer turn**, including acknowledgment/framing and any related questions within that turn. It is not a separate assessment for every question mark. The conduct/utility temporal distinction is instructed and citation bounds are validated; seeing the following response still leaves a risk of hindsight in model judgment.

`--workers 1` runs the same task queue serially. It does not select a different judging method. A worker is a client agent process, not a GPU or loaded copy of the model. Separate Slurm jobs handle separate model deployments.

A failed turn blocks that transcript's synthesis, while other independent tasks continue. Reports retain successful partial findings. Ctrl-C or SIGTERM stops active agents. `--timeout` is a deadline **per agent task**, default 300 seconds; increase it for a long final synthesis. Tool/model-call budgets also apply per agent task. Runs are not resumable yet.

## Optional interview guide

Add `--guide path/to/guide.json` to assess explicit information requirements across the whole interview. Copy and edit [the example guide](examples/interview_guide.json):

```json
{
  "title": "Work interview",
  "items": [
    {
      "id": "ai_example",
      "description": "A concrete example of the participant using AI at work, including what happened.",
      "required": true,
      "grounding": "first_hand",
      "condition": "Applies when the participant has used AI at work."
    }
  ]
}
```

`required` defaults to `true`; `grounding` defaults to `unspecified`. Use `first_hand` only where the protocol explicitly requires the participant's own experience or observations. The optional `condition` describes applicability. Requirements describe information to obtain, rather than questions that must be asked verbatim. The runner reads JSON guides, not PDFs.

Guide coverage is `covered`, `partial`, `not_observed`, `unclear`, or `not_applicable`. Volunteered information and later responses can satisfy a requirement. Asking a question alone does not. Optional omissions are not violations; legitimate exceptions such as refusals need evidence. For an interview fragment, add `--transcript-scope excerpt`; absence then cannot establish a whole-interview omission.

Without a guide, coverage is unassessed and the protocol-grounding check is not applicable. The purpose alone does not create mandatory requirements.

## Output

The output directory must be new. Every input gets its own indexed subdirectory, including when only one transcript is supplied:

```text
my-review/
├── run.json                     # Overall progress/status and transcript paths
├── 0001-interview1/
│   ├── evidence.md              # Combined readable report
│   ├── result.json              # Structured findings and completion status
│   ├── trace.jsonl              # Final-agent tool activity
│   ├── backend.log              # Final-agent diagnostics
│   ├── turns/T001/              # Local agent reports, traces and diagnostics
│   └── tasks/                   # Internal task inputs for reproducibility
└── 0002-interview2/
```

Exit code 0 means every input produced a validated final submission. Incomplete runs return a nonzero code and keep partial artifacts. Source hashes, line numbers, exact quoted evidence, input/profile consistency, and required review completion are validated in code. These checks do not establish that a model's interpretation is correct. A brief negative answer can add relevant information; length, intimacy, or emotional intensity are not automatic bonuses.

All run artifacts can contain transcript excerpts. `runs/` is ignored by Git; handle other output locations like the source transcripts. Original transcript files are not modified.

## Deploy on this Slurm cluster

The existing [deployment script](scripts/slurm_qwen38.sh) starts one loopback-only vLLM server, checks native tool calling, invokes the runner, and stops the server on exit:

```bash
salloc --account=def-alee --partition=open --qos=normal \
  --gres=gpu:h200_nvl_3g.71gb:1 --cpus-per-task=8 --mem=96G \
  --time=00:45:00 --job-name=sparkme-judge \
  srun --ntasks=1 bash evaluation/agentic_judge/scripts/slurm_qwen38.sh \
  --transcripts /path/to/interviews/*.txt \
  --purpose "Your actual interview objective" \
  --workers 4
```

The checkpoint defaults to standard BF16 `Qwen/Qwen3.8-27B`. Set `SPARKME_MODEL=Qwen/Qwen3.8-27B-FP8` for an FP8 deployment. The script defaults to four active server sequences (`SPARKME_MAX_NUM_SEQS=4`) and explicitly enables prefix caching. The runner's `--workers` controls client concurrency; server capacity determines how many requests vLLM can serve concurrently.

The server has a 65,536-token context. The client's default input budget is 56,000 tokens. Qwen turn/synthesis tasks fail rather than silently trimming history if their estimated context exceeds that budget; actual server token accounting is authoritative. Full transcript prefixes and final synthesis still need to fit the context.

Turn prompts place shared instructions/profile and numbered transcript history before assignment-specific details. Longer prefixes are queued first within each transcript to give the server an opportunity to cache them. Prefix caching saves repeated input computation; vLLM can batch concurrent decoding. Neither guarantees a speedup: exact prefix matches, cache residency, output lengths and GPU memory affect throughput. Concurrent requests have been tested through the real Qwen-Agent SDK against a scripted server; this simplified runner has not been benchmarked on a GPU.

Client dependencies can be installed without modifying the serving environment:

```bash
uv pip install --python /project/def-alee/danu/saved_env/user_sim/bin/python \
  --target /project/def-alee/danu/.cache/sparkme/qwen-agent-deps \
  -r evaluation/agentic_judge/requirements-qwen.txt
```

The launcher uses existing local build dependencies under `.cache/sparkme/` (Python headers and a matching CUDA toolkit), CUTLASS linear kernels, Triton GDN prefill, and Torch sampling. Paths are configurable through `SPARKME_PYTHON`, `SPARKME_CACHE`, `SPARKME_QWEN_DEPS`, `SPARKME_PYTHON_HEADERS`, and `SPARKME_CUDA_HOME`. Model, dtype, port and artifact location use `SPARKME_MODEL`, `SPARKME_DTYPE`, `SPARKME_PORT`, and `SPARKME_RUN`. Compute dtype does not identify weight quantization; `execution.json` records the checkpoint revision and quantization configuration. Default runner reports are under `runs/qwen38-slurm-JOB_ID/review/`; server logs and execution metadata are in its parent directory.

## Code to review

- Each shared LangChain `BaseTool` defines its Pydantic input schema and implementation in [backends/tools.py](backends/tools.py).
- Session state, tool dispatch, and evidence persistence live in [backends/agent.py](backends/agent.py).
- [runner.py](runner.py): arguments, one bounded task queue, deadlines, cancellation and result merging.
- [judging/turns.py](judging/turns.py): turn segmentation, conduct checks, information records and report rendering.
- [judging/guide.py](judging/guide.py): explicit protocol requirements and global coverage.
- [judging/prompts.py](judging/prompts.py): instructions shared by the backends.
- [backends/](backends/): Codex and Qwen-Agent adapters. Claude Code is not implemented.
- [scripts/slurm_qwen38.sh](scripts/slurm_qwen38.sh): deployment.

`runner.py` is both the entry point and the scheduler: run it with `python -m evaluation.agentic_judge.runner`. There are no separate single-transcript/batch CLIs or nested runner processes. The previous `cli`/`batch` entry points and `--jobs`/`--turn-workers` controls have been replaced by this runner and `--workers`.

For generic metric tasks, the same runner accepts `--mode extract` or `--mode evaluate` with `--metric path/to/metric.json`; those modes queue one full-transcript agent per input. Extraction requires a nonempty metric description. Evaluation additionally needs a rubric with finite `min_score < max_score` and a nonempty description. See [the example metric](examples/grounded_follow_up.json). Review mode uses built-in criteria and does not take `--metric`.

## Tests

```bash
python -m unittest discover -s evaluation/agentic_judge/tests -v
```

Install the shared `requirements.txt` before running tests. Qwen integration tests require `requirements-qwen.txt`. Tests cover quote integrity, source/temporal bounds, coverage requirements, overlapping requests, a global worker limit across transcripts, cancellation, deadline persistence, and final synthesis. Scripted endpoints verify execution and protocol behavior; judgment quality still needs calibration against human annotations.
