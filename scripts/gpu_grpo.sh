#!/usr/bin/env bash
# Stage 5 (GRPO) on the GPU server. Run from the unpacked repo root:  bash scripts/gpu_grpo.sh <step>
# steps: setup | merge | serve-sft | eval-sft | train | serve-ckpts | select | serve-grpo | eval-grpo | stop | pack
# Environment overrides: GDATA (GRPO data dir), RUN (output dir), TAG, LIMIT_TRAIN, EVAL_LIMIT, CKPTS
set -euo pipefail
ROOT=/root/autodl-tmp
BASE_MODEL=${BASE_MODEL:-$ROOT/models/Qwen3-4B-Instruct-2507}
SFT_MODEL=${SFT_MODEL:-$ROOT/models/rateyourdj-sft-v1}      # base + stage 4 adapter, merged
GDATA=${GDATA:-data/training/grpo/grpo-v1}
RUN=${RUN:-runs/grpo-v1}
TAG=${TAG:-v1}
export HF_ENDPOINT=${HF_ENDPOINT:-https://hf-mirror.com} HF_HOME=${HF_HOME:-$ROOT/hf} HF_HUB_DISABLE_XET=1
export OMP_NUM_THREADS=${OMP_NUM_THREADS:-8} PYTHONPATH=src
export VLLM_USE_FLASHINFER_SAMPLER=0 VLLM_ATTENTION_BACKEND=FLASH_ATTN

serve() {  # $1 = extra vLLM args
  nohup vllm serve "$SFT_MODEL" --served-model-name base --port 8000 --max-model-len 16384 \
    --gpu-memory-utilization 0.85 $1 > vllm.log 2>&1 &
  echo "vLLM starting (log: vllm.log) ..."
  for i in $(seq 1 90); do
    if curl -sf localhost:8000/v1/models > /dev/null; then echo "ready"; curl -s localhost:8000/v1/models | python -c "import json,sys; print([m['id'] for m in json.load(sys.stdin)['data']])"; return 0; fi
    sleep 5
  done
  echo "vLLM did not come up, see vllm.log"; tail -30 vllm.log; exit 1
}
gpu_free() { if curl -sf localhost:8000/v1/models > /dev/null; then echo "vLLM is still running: bash scripts/gpu_grpo.sh stop"; exit 1; fi; }

case "${1:-}" in
  setup)
    pip install "trl>=0.23,<0.26" "datasets>=2.20" "transformers>=4.56,<5" "vllm==0.11.0"
    python -c "import torch, transformers, trl, peft, vllm; print('torch', torch.__version__, torch.version.cuda, '| transformers', transformers.__version__, '| trl', trl.__version__, '| peft', peft.__version__, '| vllm', vllm.__version__)"
    ;;
  merge)
    python -m rateyourdj.training.grpo_train merge --base "$BASE_MODEL" --adapter runs/sft-v1/adapter --out "$SFT_MODEL"
    ;;
  serve-sft)
    serve ""
    ;;
  eval-sft)      # baseline before GRPO: SFT vs SFT + fixed mix on the test prompts
    python -m rateyourdj.training.grpo_eval --data "$GDATA/test.jsonl" --models "sft=base" --tokenizer "$SFT_MODEL" \
      --out "runs/grpo-eval-sft-$TAG" ${EVAL_LIMIT:+--limit $EVAL_LIMIT}
    ;;
  train)
    gpu_free
    python -m rateyourdj.training.grpo_train train --data-dir "$GDATA" --sft-model "$SFT_MODEL" --output-dir "$RUN" \
      ${LIMIT_TRAIN:+--limit-train $LIMIT_TRAIN} "${@:2}" 2>&1 | tee "$RUN.train.log"
    ;;
  serve-ckpts)   # every saved checkpoint as a LoRA module, for checkpoint selection on val
    mods=$(for d in "$RUN"/checkpoints/checkpoint-*; do echo -n "$(basename "$d")=$d "; done)
    serve "--enable-lora --max-lora-rank 64 --max-loras 4 --lora-modules $mods"
    ;;
  select)        # val set, all checkpoints (hidden positives allowed here: checkpoint selection)
    models=$(for d in "$RUN"/checkpoints/checkpoint-*; do n=$(basename "$d"); echo -n "$n=$n,"; done)
    python -m rateyourdj.training.grpo_eval --data "$GDATA/val.jsonl" --models "sft=base,$models" --tokenizer "$SFT_MODEL" \
      --out "runs/grpo-select-$TAG" --limit ${SELECT_LIMIT:-60}
    ;;
  serve-grpo)    # CKPT=checkpoint-N picks a checkpoint; default: the final adapter
    path=${CKPT:+$RUN/checkpoints/$CKPT}; path=${path:-$RUN/adapter}
    serve "--enable-lora --max-lora-rank 64 --lora-modules grpo=$path"
    ;;
  eval-grpo)
    python -m rateyourdj.training.grpo_eval --data "$GDATA/test.jsonl" --models "sft=base,grpo=grpo" --tokenizer "$SFT_MODEL" \
      --out "runs/grpo-eval-$TAG" ${EVAL_LIMIT:+--limit $EVAL_LIMIT}
    ;;
  stop)
    pkill -f "vllm serve" || true; sleep 5; nvidia-smi --query-gpu=memory.used --format=csv
    ;;
  pack)
    tar czf "results-grpo-$TAG.tgz" --exclude 'checkpoints/*/optimizer.pt' --exclude 'checkpoints/*/rng_state.pth' \
      "$RUN" runs/grpo-eval-sft-$TAG runs/grpo-select-$TAG runs/grpo-eval-$TAG "$RUN.train.log" 2>/dev/null || true
    ls -lh "results-grpo-$TAG.tgz"
    ;;
  *)
    sed -n 2,5p "$0"; exit 1 ;;
esac
