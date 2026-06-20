"""CLI for rateyourDJ model training.

Data-prep subcommands (``build-sft``, ``build-grpo``) are dependency-free and
run anywhere. Training subcommands (``train-sft``, ``train-grpo``) require the
optional ``[training]`` extras and a GPU; they print a clear install hint when
the dependencies are missing instead of crashing with an ImportError.
"""

from __future__ import annotations

import argparse
import json
from typing import Any

from .dataset import build_grpo_file, build_sft_file


def _print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "rateyourDJ model training: prepare SFT/GRPO datasets from agent "
            "trajectories and run LoRA SFT / GRPO fine-tuning."
        )
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    build_sft = subparsers.add_parser(
        "build-sft",
        help="build supervised prompt/completion samples from a trajectory JSONL",
    )
    build_sft.add_argument(
        "input_path",
        help="trajectory dataset JSONL (from rateyourdj-l7 export/split)",
    )
    build_sft.add_argument("output_path", help="output SFT JSONL path")
    build_sft.add_argument(
        "--min-reward",
        type=float,
        default=None,
        help="only keep trajectories with average_reward >= this value",
    )

    build_grpo = subparsers.add_parser(
        "build-grpo",
        help="build prompt-grouped reward samples for GRPO from a trajectory JSONL",
    )
    build_grpo.add_argument("input_path", help="trajectory dataset JSONL")
    build_grpo.add_argument("output_path", help="output GRPO JSONL path")
    build_grpo.add_argument(
        "--min-group-size",
        type=int,
        default=2,
        help="minimum responses per prompt to keep a group (default 2)",
    )

    train_sft = subparsers.add_parser(
        "train-sft",
        help="run LoRA SFT (requires the [training] extras + a GPU)",
    )
    train_sft.add_argument("train_file", help="SFT JSONL from build-sft")
    train_sft.add_argument("--output-dir", default="data/training/sft-model")
    train_sft.add_argument("--base-model", default="Qwen/Qwen2.5-0.5B-Instruct")
    train_sft.add_argument("--epochs", type=float, default=1.0)
    train_sft.add_argument("--learning-rate", type=float, default=2e-4)
    train_sft.add_argument("--batch-size", type=int, default=1)
    train_sft.add_argument("--grad-accum", type=int, default=8)
    train_sft.add_argument("--seed", type=int, default=20260615)

    train_grpo = subparsers.add_parser(
        "train-grpo",
        help="run GRPO (requires the [training] extras + a GPU)",
    )
    train_grpo.add_argument("train_file", help="GRPO JSONL from build-grpo")
    train_grpo.add_argument("--output-dir", default="data/training/grpo-model")
    train_grpo.add_argument("--base-model", default="Qwen/Qwen2.5-0.5B-Instruct")
    train_grpo.add_argument("--epochs", type=float, default=1.0)
    train_grpo.add_argument("--learning-rate", type=float, default=1e-5)
    train_grpo.add_argument("--batch-size", type=int, default=1)
    train_grpo.add_argument("--grad-accum", type=int, default=8)
    train_grpo.add_argument("--num-generations", type=int, default=4)
    train_grpo.add_argument("--seed", type=int, default=20260615)
    return parser


def main() -> int:
    args = build_parser().parse_args()

    if args.command == "build-sft":
        result = build_sft_file(
            args.input_path,
            args.output_path,
            min_reward=args.min_reward,
        )
        _print_json(result.to_dict())
        return 0

    if args.command == "build-grpo":
        result = build_grpo_file(
            args.input_path,
            args.output_path,
            min_group_size=args.min_group_size,
        )
        _print_json(result.to_dict())
        return 0

    if args.command == "train-sft":
        from .sft import SFTConfig, run_sft

        config = SFTConfig(
            train_file=args.train_file,
            output_dir=args.output_dir,
            base_model=args.base_model,
            epochs=args.epochs,
            learning_rate=args.learning_rate,
            per_device_batch_size=args.batch_size,
            gradient_accumulation_steps=args.grad_accum,
            seed=args.seed,
        )
        try:
            result = run_sft(config)
        except RuntimeError as error:
            print(str(error))
            return 1
        _print_json(result)
        return 0

    if args.command == "train-grpo":
        from .grpo import GRPOConfig, run_grpo

        config = GRPOConfig(
            train_file=args.train_file,
            output_dir=args.output_dir,
            base_model=args.base_model,
            epochs=args.epochs,
            learning_rate=args.learning_rate,
            per_device_batch_size=args.batch_size,
            gradient_accumulation_steps=args.grad_accum,
            num_generations=args.num_generations,
            seed=args.seed,
        )
        try:
            result = run_grpo(config)
        except RuntimeError as error:
            print(str(error))
            return 1
        _print_json(result)
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
