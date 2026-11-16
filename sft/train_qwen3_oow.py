#!/usr/bin/env python3
"""Fine-tune a Qwen3-8B OOW adapter with single-GPU QLoRA or BF16 LoRA."""

from __future__ import annotations

import argparse
import json
import random
from collections import Counter
from pathlib import Path
from typing import Any


DEFAULT_DATASET = "sft/output/prepared/chat"
DEFAULT_MODEL = "Qwen/Qwen3-8B"
DEFAULT_OUTPUT = "models/qwen3_8b_oow_colregs_qlora"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"{path}:{line_number}: expected a JSON object")
            prompt = row.get("prompt")
            completion = row.get("completion")
            if not isinstance(prompt, list) or not isinstance(completion, list):
                raise ValueError(
                    f"{path}:{line_number}: prompt and completion must be lists"
                )
            rows.append(row)
    return rows


def completion_action(row: dict[str, Any]) -> str:
    try:
        content = row["completion"][0]["content"]
        return str(json.loads(content)["action"])
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid assistant completion in {row.get('example_id')}") from error


def balanced_epoch_counts(
    actions: list[str], maintain_fraction: float
) -> tuple[int, int]:
    """Return class counts that retain every row and reach the target exposure.

    Balancing every action independently would give isolated ``stop`` or ``resume``
    examples enormous weights.  Instead, preserve the observed distribution within
    the maneuver class and only balance the operationally meaningful split between
    maintaining course and taking another action.
    """
    if not 0.0 < maintain_fraction < 1.0:
        raise ValueError("sampling maintain fraction must be between zero and one")
    maintain_count = sum(action == "maintain_course" for action in actions)
    maneuver_count = len(actions) - maintain_count
    if maintain_count == 0 or maneuver_count == 0:
        raise ValueError("weighted sampling requires maintain and non-maintain rows")
    target_total = max(
        len(actions),
        int((maintain_count / maintain_fraction) + 0.999999999),
        int((maneuver_count / (1.0 - maintain_fraction)) + 0.999999999),
    )
    target_maintain = max(maintain_count, round(target_total * maintain_fraction))
    target_maneuver = max(maneuver_count, target_total - target_maintain)
    return target_maintain, target_maneuver


def inspect_dataset(
    dataset_dir: Path,
    train_filename: str = "train_balanced.jsonl",
) -> dict[str, Any]:
    files = {
        "train": dataset_dir / train_filename,
        "validation": dataset_dir / "validation_natural.jsonl",
        "test": dataset_dir / "test_natural.jsonl",
    }
    missing = [str(path) for path in files.values() if not path.is_file()]
    if missing:
        raise FileNotFoundError("missing dataset files: " + ", ".join(missing))

    summary: dict[str, Any] = {"files": {key: str(value) for key, value in files.items()}}
    episode_sets: dict[str, set[str]] = {}
    for split, path in files.items():
        rows = read_jsonl(path)
        episode_sets[split] = {str(row.get("source_file")) for row in rows}
        summary[split] = {
            "rows": len(rows),
            "episodes": len(episode_sets[split]),
            "actions": dict(sorted(Counter(completion_action(row) for row in rows).items())),
            "reference_modes": dict(
                sorted(Counter(str(row.get("reference_mode")) for row in rows).items())
            ),
        }
    overlap = {
        "train_validation": episode_sets["train"] & episode_sets["validation"],
        "train_test": episode_sets["train"] & episode_sets["test"],
        "validation_test": episode_sets["validation"] & episode_sets["test"],
    }
    if any(overlap.values()):
        raise ValueError("episode leakage detected between dataset splits")
    summary["episode_overlap"] = {key: 0 for key in overlap}
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", default=DEFAULT_DATASET)
    parser.add_argument(
        "--train-file",
        default="train_balanced.jsonl",
        help="Training split filename inside --dataset-dir",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--mode",
        choices=("qlora", "lora"),
        default="qlora",
        help="qlora uses 4-bit NF4 base weights; lora keeps BF16 base weights",
    )
    parser.add_argument("--max-length", type=int, default=4096)
    parser.add_argument("--epochs", type=float, default=2.0)
    parser.add_argument("--learning-rate", type=float)
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--gradient-accumulation", type=int, default=16)
    parser.add_argument("--lora-rank", type=int, default=32)
    parser.add_argument("--lora-alpha", type=int, default=64)
    parser.add_argument("--lora-dropout", type=float, default=0.05)
    parser.add_argument("--seed", type=int, default=20260918)
    parser.add_argument(
        "--sampling-maintain-fraction",
        type=float,
        help=(
            "Retain every training row and use replacement sampling to target this "
            "maintain_course exposure fraction. Non-maintain actions retain their "
            "natural relative distribution."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate and summarize data without importing the training stack",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.max_length < 512:
        raise ValueError("--max-length must be at least 512")
    if args.batch_size < 1 or args.gradient_accumulation < 1:
        raise ValueError("batch and gradient accumulation must be positive")
    if args.sampling_maintain_fraction is not None and not (
        0.0 < args.sampling_maintain_fraction < 1.0
    ):
        raise ValueError("sampling maintain fraction must be between zero and one")

    dataset_dir = Path(args.dataset_dir)
    invalid_marker = dataset_dir / "INVALID_SFT_PROVENANCE.json"
    if invalid_marker.is_file():
        raise SystemExit(
            "Refusing dataset with invalid SFT provenance: "
            f"{invalid_marker}\n{invalid_marker.read_text(encoding='utf-8')}"
        )
    summary = inspect_dataset(dataset_dir, args.train_file)
    train_actions = [
        completion_action(row)
        for row in read_jsonl(dataset_dir / args.train_file)
    ]
    sampling_counts = None
    if args.sampling_maintain_fraction is not None:
        sampling_counts = balanced_epoch_counts(
            train_actions, args.sampling_maintain_fraction
        )
    learning_rate = args.learning_rate or (1e-4 if args.mode == "qlora" else 5e-5)
    run_config = {
        "model": args.model,
        "mode": args.mode,
        "output_dir": args.output_dir,
        "max_length": args.max_length,
        "epochs": args.epochs,
        "learning_rate": learning_rate,
        "micro_batch_size": args.batch_size,
        "gradient_accumulation": args.gradient_accumulation,
        "effective_batch_size": args.batch_size * args.gradient_accumulation,
        "lora_rank": args.lora_rank,
        "lora_alpha": args.lora_alpha,
        "lora_dropout": args.lora_dropout,
        "seed": args.seed,
        "sampling": {
            "mode": "retain_all_then_resample_minority" if sampling_counts is not None else "random",
            "unique_source_rows": len(train_actions),
            "epoch_samples": sum(sampling_counts) if sampling_counts is not None else len(train_actions),
            "epoch_maintain_samples": sampling_counts[0] if sampling_counts is not None else None,
            "epoch_nonmaintain_samples": sampling_counts[1] if sampling_counts is not None else None,
            "target_maintain_fraction": args.sampling_maintain_fraction,
            "all_unique_source_rows_retained": True,
        },
        "dataset": summary,
    }
    print(json.dumps(run_config, indent=2))
    if args.dry_run:
        return

    try:
        import torch
        from datasets import load_dataset
        from peft import LoraConfig, prepare_model_for_kbit_training
        from transformers import (
            AutoModelForCausalLM,
            AutoTokenizer,
            BitsAndBytesConfig,
            set_seed,
        )
        from trl import SFTConfig, SFTTrainer
    except ImportError as error:
        raise SystemExit(
            "Training dependencies are missing. Install them with: "
            "uv sync --extra train"
        ) from error

    if not torch.cuda.is_available():
        raise SystemExit("CUDA is required for this Qwen3-8B training script")
    set_seed(args.seed)
    use_bf16 = torch.cuda.is_bf16_supported()
    use_tf32 = torch.cuda.get_device_capability()[0] >= 8
    compute_dtype = torch.bfloat16 if use_bf16 else torch.float16

    tokenizer = AutoTokenizer.from_pretrained(args.model, use_fast=True)
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token
    tokenizer.padding_side = "right"

    model_kwargs: dict[str, Any] = {
        "torch_dtype": compute_dtype,
        "use_cache": False,
    }
    if args.mode == "qlora":
        model_kwargs["quantization_config"] = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_use_double_quant=True,
            bnb_4bit_compute_dtype=compute_dtype,
        )
        model_kwargs["device_map"] = {"": 0}

    model = AutoModelForCausalLM.from_pretrained(args.model, **model_kwargs)
    if args.mode == "qlora":
        model = prepare_model_for_kbit_training(
            model,
            use_gradient_checkpointing=True,
        )
    model.config.use_cache = False

    data_files = {
        "train": str(dataset_dir / args.train_file),
        "validation": str(dataset_dir / "validation_natural.jsonl"),
        "test": str(dataset_dir / "test_natural.jsonl"),
    }
    dataset = load_dataset("json", data_files=data_files)
    peft_config = LoraConfig(
        r=args.lora_rank,
        lora_alpha=args.lora_alpha,
        lora_dropout=args.lora_dropout,
        bias="none",
        task_type="CAUSAL_LM",
        target_modules="all-linear",
    )

    training_args = SFTConfig(
        output_dir=args.output_dir,
        max_length=args.max_length,
        truncation_mode="keep_start",
        completion_only_loss=True,
        packing=False,
        num_train_epochs=args.epochs,
        learning_rate=learning_rate,
        per_device_train_batch_size=args.batch_size,
        per_device_eval_batch_size=1,
        gradient_accumulation_steps=args.gradient_accumulation,
        gradient_checkpointing=True,
        gradient_checkpointing_kwargs={"use_reentrant": False},
        bf16=use_bf16,
        fp16=not use_bf16,
        tf32=use_tf32,
        optim="paged_adamw_8bit" if args.mode == "qlora" else "adamw_torch_fused",
        lr_scheduler_type="cosine",
        warmup_ratio=0.05,
        weight_decay=0.01,
        max_grad_norm=1.0,
        eval_strategy="epoch",
        save_strategy="epoch",
        logging_steps=10,
        save_total_limit=2,
        load_best_model_at_end=True,
        metric_for_best_model="eval_loss",
        greater_is_better=False,
        report_to="none",
        seed=args.seed,
        data_seed=args.seed,
        dataset_num_proc=4,
    )
    trainer_class = SFTTrainer
    if sampling_counts is not None:
        from torch.utils.data import Sampler

        class RetainAllBalancedSampler(Sampler[int]):
            def __init__(self) -> None:
                self.iteration = 0
                self.maintain_indices = [
                    index for index, action in enumerate(train_actions)
                    if action == "maintain_course"
                ]
                self.maneuver_indices = [
                    index for index, action in enumerate(train_actions)
                    if action != "maintain_course"
                ]

            def __len__(self) -> int:
                return sum(sampling_counts)

            def __iter__(self):
                rng = random.Random(args.seed + self.iteration)
                self.iteration += 1
                target_maintain, target_maneuver = sampling_counts
                indices = list(range(len(train_actions)))
                indices.extend(
                    rng.choices(
                        self.maintain_indices,
                        k=target_maintain - len(self.maintain_indices),
                    )
                )
                indices.extend(
                    rng.choices(
                        self.maneuver_indices,
                        k=target_maneuver - len(self.maneuver_indices),
                    )
                )
                rng.shuffle(indices)
                return iter(indices)

        class WeightedActionSFTTrainer(SFTTrainer):
            def _get_train_sampler(self, train_dataset=None):
                dataset = train_dataset if train_dataset is not None else self.train_dataset
                if dataset is None or len(dataset) != len(train_actions):
                    raise ValueError("processed training dataset changed row count")
                return RetainAllBalancedSampler()

        trainer_class = WeightedActionSFTTrainer

    trainer = trainer_class(
        model=model,
        args=training_args,
        train_dataset=dataset["train"],
        eval_dataset=dataset["validation"],
        processing_class=tokenizer,
        peft_config=peft_config,
    )
    trainer.train()
    trainer.save_model(args.output_dir)
    tokenizer.save_pretrained(args.output_dir)
    trainer.evaluate(dataset["test"], metric_key_prefix="test")

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "run_config.json").write_text(
        json.dumps(run_config, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
