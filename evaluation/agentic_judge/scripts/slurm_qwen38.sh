#!/usr/bin/env bash
# Run inside a Slurm GPU allocation. Extra arguments override the example CLI
# defaults below (e.g. --transcript PATH --purpose TEXT --guide PATH).
set -euo pipefail
SPARKME_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
SPARKME_PYTHON=${SPARKME_PYTHON:-/project/def-alee/danu/saved_env/user_sim/bin/python}
SPARKME_MODEL=${SPARKME_MODEL:-Qwen/Qwen3.8-27B}
SPARKME_DTYPE=${SPARKME_DTYPE:-bfloat16}
SPARKME_CACHE=${SPARKME_CACHE:-/project/def-alee/danu/.cache/sparkme}
SPARKME_QWEN_DEPS=${SPARKME_QWEN_DEPS:-$SPARKME_CACHE/qwen-agent-deps}
SPARKME_RUN=${SPARKME_RUN:-$SPARKME_ROOT/evaluation/agentic_judge/runs/qwen38-slurm-${SLURM_JOB_ID:-local}}
SPARKME_PORT=${SPARKME_PORT:-18038}
SPARKME_MAX_NUM_SEQS=${SPARKME_MAX_NUM_SEQS:-4}
if [[ -z "${SLURM_JOB_ID:-}" ]]; then
    echo 'Launch this script with salloc/srun or sbatch on a GPU node.' >&2
    exit 2
fi
mkdir -p "$SPARKME_RUN" "$SPARKME_CACHE"
chmod 700 "$SPARKME_RUN"
cd "$SPARKME_ROOT"
export HF_HOME="$SPARKME_CACHE/huggingface"
SPARKME_PYTHON_HEADERS=${SPARKME_PYTHON_HEADERS:-$SPARKME_CACHE/build-deps/headers/usr/include/python3.12}
if [[ -f "$SPARKME_PYTHON_HEADERS/Python.h" ]]; then
    export C_INCLUDE_PATH="$SPARKME_PYTHON_HEADERS${C_INCLUDE_PATH:+:$C_INCLUDE_PATH}"
    export CPLUS_INCLUDE_PATH="$SPARKME_PYTHON_HEADERS${CPLUS_INCLUDE_PATH:+:$CPLUS_INCLUDE_PATH}"
fi
SPARKME_ENV_BIN=$(dirname "$SPARKME_PYTHON")
SPARKME_DEFAULT_CUDA_HOME="$SPARKME_ENV_BIN/../lib/python3.12/site-packages/nvidia/cu13"
if [[ -x "$SPARKME_CACHE/cuda13.0/nvidia/cu13/bin/nvcc" ]]; then
    SPARKME_DEFAULT_CUDA_HOME="$SPARKME_CACHE/cuda13.0/nvidia/cu13"
fi
SPARKME_CUDA_HOME=${SPARKME_CUDA_HOME:-$SPARKME_DEFAULT_CUDA_HOME}
export PATH="$SPARKME_ENV_BIN:$PATH"
if [[ -x "$SPARKME_CUDA_HOME/bin/nvcc" ]]; then
    export CUDA_HOME="$SPARKME_CUDA_HOME"
    export PATH="$CUDA_HOME/bin:$PATH"
fi
export VLLM_WORKER_MULTIPROC_METHOD=spawn
export OMP_NUM_THREADS=1
# Use installed GPU kernels instead of optional FlashInfer/DeepGEMM JIT builds.
export VLLM_USE_DEEP_GEMM=0
export VLLM_USE_FLASHINFER_SAMPLER=0
hostname
nvidia-smi -L
"$SPARKME_PYTHON" -c 'import torch; print(torch.__version__); print(torch.cuda.get_device_properties(0))'
"$SPARKME_PYTHON" -m vllm.entrypoints.openai.api_server \
    --model "$SPARKME_MODEL" --served-model-name "$SPARKME_MODEL" --dtype "$SPARKME_DTYPE" \
    --host 127.0.0.1 --port "$SPARKME_PORT" --language-model-only \
    --max-model-len 65536 --max-num-seqs "$SPARKME_MAX_NUM_SEQS" --enable-prefix-caching --gpu-memory-utilization 0.90 \
    --enable-auto-tool-choice --tool-call-parser qwen3_coder --reasoning-parser qwen3 \
    --linear-backend cutlass --gdn-prefill-backend triton \
    --enforce-eager > "$SPARKME_RUN/vllm.log" 2>&1 &
SPARKME_SERVER_PID=$!
cleanup() {
    kill "$SPARKME_SERVER_PID" 2>/dev/null || true
    wait "$SPARKME_SERVER_PID" 2>/dev/null || true
}
trap cleanup EXIT
export SPARKME_PORT SPARKME_RUN SPARKME_MODEL SPARKME_DTYPE SPARKME_SERVER_PID SPARKME_MAX_NUM_SEQS
"$SPARKME_PYTHON" - <<'PY'
import json,os,time,urllib.request,socket
from importlib.metadata import version
from pathlib import Path
port=os.environ['SPARKME_PORT']
for attempt in range(240):
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/health',timeout=3): break
    except Exception:
        try: os.kill(int(os.environ['SPARKME_SERVER_PID']),0)
        except ProcessLookupError: raise SystemExit('vLLM exited; inspect vllm.log')
        if attempt % 6 == 0: print(f'Waiting for model server ({attempt*5}s)',flush=True)
        time.sleep(5)
else: raise SystemExit('Server readiness deadline exceeded')
body={'model':os.environ['SPARKME_MODEL'], 'messages':[{'role':'user','content':'Call read_lines with start=1 and end=1. Do not answer in prose.'}],
      'tools':[{'type':'function','function':{'name':'read_lines','description':'Read transcript lines',
                'parameters':{'type':'object','properties':{'start':{'type':'integer'},'end':{'type':'integer'}},'required':['start','end']}}}],
      'tool_choice':'auto','temperature':0,'max_tokens':256,'chat_template_kwargs':{'enable_thinking':False}}
req=urllib.request.Request(f'http://127.0.0.1:{port}/v1/chat/completions',data=json.dumps(body).encode(),headers={'Content-Type':'application/json'})
with urllib.request.urlopen(req,timeout=180) as r: result=json.load(r)
Path(os.environ['SPARKME_RUN'],'smoke.json').write_text(json.dumps(result,indent=2)+'\n')
assert result['choices'][0]['message'].get('tool_calls'), result
cache=Path(os.environ['HF_HOME'])/'hub'/('models--'+os.environ['SPARKME_MODEL'].replace('/','--'))
revision=(cache/'refs/main').read_text().strip()
config=json.loads((cache/'snapshots'/revision/'config.json').read_text())
info={'slurm_job_id':os.environ['SLURM_JOB_ID'], 'node':socket.gethostname(),
      'model':os.environ['SPARKME_MODEL'], 'revision':revision,
      'dtype_requested':os.environ['SPARKME_DTYPE'],
      'checkpoint_quantization':config.get('quantization_config'),
      'checkpoint_text_dtype':config.get('text_config',{}).get('dtype'),
      'vllm':version('vllm'), 'torch':version('torch'), 'thinking':False,
      'prefix_caching':True, 'max_num_seqs':int(os.environ['SPARKME_MAX_NUM_SEQS']),
      'native_tool_parser':'qwen3_coder', 'linear_backend':'cutlass', 'gdn_prefill_backend':'triton'}
Path(os.environ['SPARKME_RUN'],'execution.json').write_text(json.dumps(info,indent=2)+'\n')
print('Native tool-call smoke test passed',flush=True)
PY
PYTHONPATH="$SPARKME_QWEN_DEPS${PYTHONPATH:+:$PYTHONPATH}" "$SPARKME_PYTHON" -m evaluation.agentic_judge.runner \
    --mode review --backend qwen --model "$SPARKME_MODEL" --base-url "http://127.0.0.1:$SPARKME_PORT/v1" \
    --qwen-native-tools --qwen-thinking off \
    --transcripts evaluation/agentic_judge/examples/interview2.txt \
    --purpose 'Understand how AI affects the participant’s work, workflows, adoption, trust, control, skills, and future expectations.' \
    --output "$SPARKME_RUN/review" --workers "$SPARKME_MAX_NUM_SEQS" --timeout 1500 --max-tools 180 --max-model-calls 100 \
    --max-input-tokens 56000 --max-output-tokens 4096 "$@"
