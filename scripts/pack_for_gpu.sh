#!/usr/bin/env bash
# Pack code + the data the GPU server needs (catalog, vector index, user context, SFT data)
# into one archive. Run on the Mac from the repo root:  bash scripts/pack_for_gpu.sh [sft-data-dir]
set -euo pipefail
SFT_DIR="${1:-data/training/sft/sft-v1-smoke}"
IDX="data/index/$(cat data/index/LATEST)"
OUT="rateyourdj-gpu.tgz"
tar czf "$OUT" \
  --exclude '__pycache__' --exclude '*.pyc' --exclude '*.egg-info' \
  pyproject.toml src eval scripts \
  data/catalog/manifest.json data/catalog/processed/songs.jsonl \
  data/index/LATEST "$IDX/manifest.json" "$IDX/ids.json" "$IDX/embeddings.npy" \
  data/users/participant_001/context.json \
  "$SFT_DIR"
ls -lh "$OUT"
echo "上传到服务器的 /root/autodl-tmp/ 后解压：mkdir -p /root/autodl-tmp/rateyourDJ && tar xzf $OUT -C /root/autodl-tmp/rateyourDJ"
