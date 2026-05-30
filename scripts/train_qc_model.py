"""
Fine-tune a Gemma 3 1B QC model on the qc_dataset_sharegpt.jsonl dataset.

ROCm (AMD gfx1030) compatible — sets required env vars before torch import,
uses attn_implementation="eager" to bypass Flash Attention 2.

Usage (from project root):
    python scripts/train_qc_model.py

The script tries unsloth first; falls back to plain transformers + peft if
the unsloth package is not installed or fails to load.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Must be set before any torch / ROCm import
os.environ.setdefault("PYTORCH_HIP_ALLOC_CONF", "garbage_collection_threshold:0.8,max_split_size_mb:512")
os.environ.setdefault("HSA_ENABLE_SDMA", "0")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

# ── Config ─────────────────────────────────────────────────────────────────────

MODEL_ID      = "unsloth/gemma-3-1b-it"
DATASET_PATH  = Path(__file__).parent / "qc_dataset_sharegpt.jsonl"
OUTPUT_DIR    = Path(__file__).parent.parent / "models" / "qc_gemma3_1b"

LORA_R        = 16
LORA_ALPHA    = 16
LORA_DROPOUT  = 0.0
MAX_SEQ_LEN   = 1024

BATCH_SIZE    = 1
GRAD_ACCUM    = 16      # effective batch size = 16
MAX_STEPS     = 2800    # ~3 epochs over 14 928 examples with eff. batch 16
LEARNING_RATE = 2e-4
WARMUP_STEPS  = 50
SAVE_STEPS    = 500
LOGGING_STEPS = 10

# ── Dataset loading ─────────────────────────────────────────────────────────────

def load_sharegpt_dataset():
    """Load qc_dataset_sharegpt.jsonl and convert to HF Dataset."""
    from datasets import Dataset

    records = []
    with open(DATASET_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))

    print(f"Loaded {len(records)} examples from {DATASET_PATH}")
    return Dataset.from_list(records)


def apply_chat_template(examples, tokenizer):
    """Convert ShareGPT conversations to the model's chat template format."""
    texts = []
    for convs in examples["conversations"]:
        messages = []
        for turn in convs:
            role = turn["from"]
            if role == "system":
                messages.append({"role": "system", "content": turn["value"]})
            elif role == "human":
                messages.append({"role": "user", "content": turn["value"]})
            elif role == "gpt":
                messages.append({"role": "assistant", "content": turn["value"]})
        text = tokenizer.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=False,
        )
        texts.append(text)
    return {"text": texts}


# ── Unsloth path ────────────────────────────────────────────────────────────────

def train_with_unsloth() -> None:
    print("Attempting unsloth training path …")
    from unsloth import FastLanguageModel
    from unsloth import is_bfloat16_supported
    from trl import SFTTrainer, SFTConfig, train_on_responses_only

    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=MODEL_ID,
        max_seq_length=MAX_SEQ_LEN,
        load_in_4bit=True,
        dtype=None,                         # auto-detect (bf16 on ROCm)
        attn_implementation="eager",        # disable FA2 — required for gfx1030
    )

    model = FastLanguageModel.get_peft_model(
        model,
        r=LORA_R,
        lora_alpha=LORA_ALPHA,
        lora_dropout=LORA_DROPOUT,
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                        "gate_proj", "up_proj", "down_proj"],
        bias="none",
        use_gradient_checkpointing="unsloth",
        random_state=42,
    )

    dataset = load_sharegpt_dataset()
    dataset = dataset.map(
        lambda ex: apply_chat_template(ex, tokenizer),
        batched=True,
        remove_columns=dataset.column_names,
    )

    trainer = SFTTrainer(
        model=model,
        tokenizer=tokenizer,
        train_dataset=dataset,
        args=SFTConfig(
            dataset_text_field="text",
            max_seq_length=MAX_SEQ_LEN,
            per_device_train_batch_size=BATCH_SIZE,
            gradient_accumulation_steps=GRAD_ACCUM,
            max_steps=MAX_STEPS,
            learning_rate=LEARNING_RATE,
            warmup_steps=WARMUP_STEPS,
            bf16=is_bfloat16_supported(),
            fp16=not is_bfloat16_supported(),
            logging_steps=LOGGING_STEPS,
            save_steps=SAVE_STEPS,
            output_dir=str(OUTPUT_DIR),
            optim="adamw_8bit",
            lr_scheduler_type="cosine",
            seed=42,
            report_to="none",
        ),
    )

    # Train only on the assistant (gpt) turns — not on the prompt
    trainer = train_on_responses_only(
        trainer,
        instruction_part="<start_of_turn>user\n",
        response_part="<start_of_turn>model\n",
    )

    print("Starting training …")
    trainer.train()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(str(OUTPUT_DIR))
    tokenizer.save_pretrained(str(OUTPUT_DIR))
    print(f"Model saved to {OUTPUT_DIR}")


# ── Plain transformers + peft fallback ─────────────────────────────────────────

def train_with_transformers() -> None:
    print("Falling back to plain transformers + peft …")
    import torch
    from transformers import (
        AutoTokenizer,
        AutoModelForCausalLM,
        BitsAndBytesConfig,
        TrainingArguments,
        Trainer,
        DataCollatorForLanguageModeling,
    )
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training

    bnb_config = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.bfloat16,
        bnb_4bit_use_double_quant=True,
    )

    tokenizer = AutoTokenizer.from_pretrained(MODEL_ID)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    model = AutoModelForCausalLM.from_pretrained(
        MODEL_ID,
        quantization_config=bnb_config,
        device_map="auto",
        attn_implementation="eager",
    )
    model = prepare_model_for_kbit_training(model)

    lora_config = LoraConfig(
        r=LORA_R,
        lora_alpha=LORA_ALPHA,
        lora_dropout=LORA_DROPOUT,
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                        "gate_proj", "up_proj", "down_proj"],
        bias="none",
        task_type="CAUSAL_LM",
    )
    model = get_peft_model(model, lora_config)
    model.print_trainable_parameters()

    dataset = load_sharegpt_dataset()
    dataset = dataset.map(
        lambda ex: apply_chat_template(ex, tokenizer),
        batched=True,
        remove_columns=dataset.column_names,
    )

    def tokenize(examples):
        return tokenizer(
            examples["text"],
            truncation=True,
            max_length=MAX_SEQ_LEN,
            padding=False,
        )

    tokenized = dataset.map(tokenize, batched=True, remove_columns=["text"])

    training_args = TrainingArguments(
        output_dir=str(OUTPUT_DIR),
        per_device_train_batch_size=BATCH_SIZE,
        gradient_accumulation_steps=GRAD_ACCUM,
        max_steps=MAX_STEPS,
        learning_rate=LEARNING_RATE,
        warmup_steps=WARMUP_STEPS,
        bf16=True,
        logging_steps=LOGGING_STEPS,
        save_steps=SAVE_STEPS,
        optim="adamw_8bit",
        lr_scheduler_type="cosine",
        seed=42,
        report_to="none",
        gradient_checkpointing=True,
    )

    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=tokenized,
        data_collator=DataCollatorForLanguageModeling(tokenizer, mlm=False),
    )

    print("Starting training …")
    trainer.train()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(str(OUTPUT_DIR))
    tokenizer.save_pretrained(str(OUTPUT_DIR))
    print(f"Model saved to {OUTPUT_DIR}")


# ── Entry point ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not DATASET_PATH.exists():
        print(f"ERROR: dataset not found at {DATASET_PATH}", file=sys.stderr)
        print("Run:  python scripts/create_qc_dataset.py  first", file=sys.stderr)
        sys.exit(1)

    try:
        train_with_unsloth()
    except ImportError as e:
        print(f"unsloth not available ({e}), using fallback")
        train_with_transformers()
    except Exception as e:
        print(f"unsloth training failed: {e}")
        print("Retrying with plain transformers …")
        train_with_transformers()
