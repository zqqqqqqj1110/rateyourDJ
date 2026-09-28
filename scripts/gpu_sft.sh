#!/usr/bin/env bash
# Stage 4 on the GPU server (AutoDL RTX 5090 D 32GB). Run from the unpacked repo root:
#   bash scripts/gpu_sft.sh <step>
# steps: setup | gpu | download | stats | serve-base | eval-base | train | serve-lora | eval-lora | stop | pack
# Environment overrides: DATA (SFT data dir), RUN (training output dir), MODEL_DIR, TAG,
#   EVAL_LIMIT (evaluate only the first N test samples; the fixed queries come first)
set -euo pipefail
ROOT=/root/autodl-tmp
MODEL_ID=${MODEL_ID:-Qwen/Qwen3-4B-Instruct-2507}
MODEL_DIR=${MODEL_DIR:-$ROOT/models/Qwen3-4B-Instruct-2507}
DATA=${DATA:-data/training/sft/sft-v1-smoke}
RUN=${RUN:-runs/sft-v1-smoke}
TAG=${TAG:-smoke}
export HF_ENDPOINT=${HF_ENDPOINT:-https://hf-mirror.com}   # AutoDL: HuggingFace mirror
export HF_HOME=${HF_HOME:-$ROOT/hf}                        # keep caches on the data disk
export HF_HUB_DISABLE_XET=1                                # the Xet backend does not work through hf-mirror (401)
export OMP_NUM_THREADS=${OMP_NUM_THREADS:-8}
# FlashInfer cannot detect SM 12.x (RTX 50xx) on a CUDA 12.8 driver: use the PyTorch sampler + FlashAttention
export VLLM_USE_FLASHINFER_SAMPLER=0 VLLM_ATTENTION_BACKEND=FLASH_ATTN
export PYTHONPATH=src

serve() {  # $1 = extra vLLM args
  nohup vllm serve "$MODEL_DIR" --served-model-name base --port 8000 \
    --max-model-len 16384 --gpu-memory-utilization 0.72 \
    --enable-auto-tool-choice --tool-call-parser hermes $1 > vllm.log 2>&1 &
  echo "vLLM starting (log: vllm.log) ..."
  for i in $(seq 1 90); do
    if curl -sf localhost:8000/v1/models > /dev/null; then echo "ready"; curl -s localhost:8000/v1/models; echo; return 0; fi
    sleep 5
  done
  echo "vLLM did not come up, see vllm.log"; tail -30 vllm.log; exit 1
}

case "${1:-}" in
  setup)
    pip install -U pip
    # vLLM 0.11.0 = torch 2.8.0 + CUDA 12.8: runs on CUDA 12.8 drivers and supports Blackwell (sm_120)
    pip install "vllm==0.11.0" "transformers>=4.56,<5"
    pip install -e . --no-deps
    pip install "peft>=0.15" "accelerate>=1.0" "sentence-transformers>=3.0" numpy Flask
    python -c "import torch, transformers, peft, vllm; print('torch', torch.__version__, torch.version.cuda, '| transformers', transformers.__version__, '| peft', peft.__version__, '| vllm', vllm.__version__)"
    bash "$0" gpu
    ;;
  gpu)   # works in AutoDL no-GPU mode too: then it just says so
    python -c "
import torch
if torch.cuda.is_available():
    print('GPU', torch.cuda.get_device_name(0), 'capability', torch.cuda.get_device_capability(0), 'arch list', torch.cuda.get_arch_list())
else:
    print('no GPU visible (CPU mode) - fine for setup / download / stats')
"
    ;;
  download)
    HF=$(command -v hf >/dev/null && echo "hf download" || echo "huggingface-cli download")
    $HF "$MODEL_ID" --local-dir "$MODEL_DIR"
    $HF BAAI/bge-m3 --exclude "onnx/*"
    ;;
  stats)
    python -m rateyourdj.training.sft_lora stats --data "$DATA" --model "$MODEL_DIR" | tee "stats-$TAG.json"
    ;;
  serve-base)
    serve ""
    ;;
  eval-base)
    python -m rateyourdj.training.sft_eval --data "$DATA/test.jsonl" --model base --out "runs/sft-eval-base-$TAG" ${EVAL_LIMIT:+--limit $EVAL_LIMIT}
    ;;
  train)
    if curl -sf localhost:8000/v1/models > /dev/null; then echo "vLLM is still running: bash scripts/gpu_sft.sh stop"; exit 1; fi
    python -m rateyourdj.training.sft_lora train --base-model "$MODEL_DIR" --data-dir "$DATA" --output-dir "$RUN" "${@:2}" 2>&1 | tee "train-$TAG.log"
    ;;
  serve-lora)
    serve "--enable-lora --max-lora-rank 64 --lora-modules rateyourdj=$RUN/adapter"
    ;;
  eval-lora)
    python -m rateyourdj.training.sft_eval --data "$DATA/test.jsonl" --model rateyourdj --out "runs/sft-eval-lora-$TAG" ${EVAL_LIMIT:+--limit $EVAL_LIMIT}
    ;;
  stop)
    pkill -f "vllm serve" || true; sleep 5; nvidia-smi --query-gpu=memory.used --format=csv
    ;;
  pack)
    tar czf "results-$TAG.tgz" --exclude 'checkpoints' "$RUN" "runs/sft-eval-base-$TAG" "runs/sft-eval-lora-$TAG" \
      "stats-$TAG.json" "train-$TAG.log" 2>/dev/null || true
    ls -lh "results-$TAG.tgz"
    ;;
  *)
    sed -n 2,6p "$0"; exit 1 ;;
esac
