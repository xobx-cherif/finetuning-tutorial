import copy
import random
from dataclasses import dataclass, field
from typing import Optional, Dict, Sequence

import torch
import torch.distributed
import transformers
from transformers import Trainer
from datasets import load_dataset
import wandb
import deepspeed

import os
os.environ["WANDB_PROJECT"]="falcodecompile"

IGNORE_INDEX = -100


def build_instruction_prompt(instruction: str):
    return """# This is the assembly code:
{}
# What is the source code?
""".format(
        instruction
    )


@dataclass
class ModelArguments:
    model_name_or_path: Optional[str] = field(
        default="tiiuae/Falcon3-7B-Base"
    )
    use_flash_attention: bool = field(
        default=False, metadata={"help": "Whether to use flash attention."}
    )


@dataclass
class DataArguments:
    data_path: str = field(
        default=None, metadata={"help": "Path to the training data."}
    )


@dataclass
class TrainingArguments(transformers.TrainingArguments):
    cache_dir: Optional[str] = field(default=None)
    optim: str = field(default="adamw_torch")
    model_max_length: int = field(
        default=512,
        metadata={
            "help": "Maximum sequence length. Sequences will be right padded (and possibly truncated)."
        },
    )


def _tokenize_fn(
    strings: Sequence[str], tokenizer: transformers.PreTrainedTokenizer
) -> Dict:
    """Tokenize a list of strings."""
    tokenized_list = [
        tokenizer(
            text,
            return_tensors="pt",
            padding="longest",
            max_length=tokenizer.model_max_length,
            truncation=True,
        )
        for text in strings
    ]

    input_ids = labels = [tokenized.input_ids[0] for tokenized in tokenized_list]
    input_ids_lens = labels_lens = [
        tokenized.input_ids.ne(tokenizer.pad_token_id).sum().item()
        for tokenized in tokenized_list
    ]

    return dict(
        input_ids=input_ids,
        labels=labels,
        input_ids_lens=input_ids_lens,
        labels_lens=labels_lens,
    )


def preprocess(
    sources: Sequence[str],
    targets: Sequence[str],
    tokenizer: transformers.PreTrainedTokenizer,
) -> Dict:
    """Preprocess the data by tokenizing."""
    examples = [s + t for s, t in zip(sources, targets)]
    examples_tokenized, sources_tokenized = [
        _tokenize_fn(strings, tokenizer) for strings in (examples, sources)
    ]
    input_ids = examples_tokenized["input_ids"]

    labels = copy.deepcopy(input_ids)
    for label, source_len in zip(labels, sources_tokenized["input_ids_lens"]):
        label[:source_len] = IGNORE_INDEX
    return dict(input_ids=input_ids, labels=labels)


@dataclass
class DataCollatorForSupervisedDataset(object):
    """Collate examples for supervised fine-tuning."""

    tokenizer: transformers.PreTrainedTokenizer

    def __call__(self, instances: Sequence[Dict]) -> Dict[str, torch.Tensor]:
        input_ids, labels = tuple(
            [instance[key] for instance in instances] for key in ("input_ids", "labels")
        )
        input_ids = [torch.tensor(x) for x in input_ids]
        input_ids = torch.nn.utils.rnn.pad_sequence(
            input_ids, batch_first=True, padding_value=self.tokenizer.pad_token_id
        )
        labels = [torch.tensor(x) for x in labels]
        labels = torch.nn.utils.rnn.pad_sequence(
            labels, batch_first=True, padding_value=IGNORE_INDEX
        )

        return dict(
            input_ids=input_ids,
            labels=labels,
            attention_mask=input_ids.ne(self.tokenizer.pad_token_id),
        )


def train_tokenize_function(examples, tokenizer):
    sources = [
        build_instruction_prompt(instruction) for instruction in examples["instruction"]
    ]
    eos_token = tokenizer.eos_token
    targets = [f"{output}\n{eos_token}" for output in examples["output"]]
    data_dict = preprocess(sources, targets, tokenizer)
    return data_dict


def train():
    parser = transformers.HfArgumentParser(
        (ModelArguments, DataArguments, TrainingArguments)
    )
    model_args, data_args, training_args = parser.parse_args_into_dataclasses()

    if training_args.local_rank == 0:
        print("=" * 100)
        print(training_args)

    tokenizer = transformers.AutoTokenizer.from_pretrained(
        model_args.model_name_or_path,
        model_max_length=training_args.model_max_length,
        padding_side="right",
        use_fast=True,
        trust_remote_code=True,
    )

    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
        tokenizer.pad_token_id = tokenizer.eos_token_id

    print("PAD Token:", tokenizer.pad_token, tokenizer.pad_token_id)
    print("BOS Token", tokenizer.bos_token, tokenizer.bos_token_id)
    print("EOS Token", tokenizer.eos_token, tokenizer.eos_token_id)

    if training_args.local_rank == 0:
        print("Load tokenizer from {} over.".format(model_args.model_name_or_path))

    model_kwargs = {}
    if model_args.use_flash_attention:
        model_kwargs["attn_implementation"] = "flash_attention_2"

    model = transformers.AutoModelForCausalLM.from_pretrained(
        model_args.model_name_or_path, torch_dtype=torch.bfloat16, trust_remote_code=True, **model_kwargs
    ) #, torch_dtype=torch.bfloat16
    
    if training_args.local_rank == 0:
        print("Load model from {} over.".format(model_args.model_name_or_path))

    raw_train_datasets = load_dataset(data_args.data_path, split="train")
        #"json",
        #data_files=data_args.data_path,
        #split="train",
        #cache_dir=training_args.cache_dir,
    #)
    if training_args.local_rank > 0:
        torch.distributed.barrier()

    train_dataset = raw_train_datasets.map(
        train_tokenize_function,
        batched=True,
        batch_size=64,
        num_proc=8,
        remove_columns=raw_train_datasets.column_names,
        load_from_cache_file=True,  # not args.overwrite_cache
        desc="Running Encoding",
        fn_kwargs={"tokenizer": tokenizer},
    )

    if training_args.local_rank == 0:
        torch.distributed.barrier()

    if training_args.local_rank == 0:
        print("Training dataset samples:", len(train_dataset))
        for index in random.sample(range(len(train_dataset)), 3):
            print(
                f"Sample {index} of the training set: {train_dataset[index]['input_ids']}, {train_dataset[index]['labels']}."
            )
            print(
                f"Sample {index} of the training set: {tokenizer.decode(list(train_dataset[index]['input_ids']))}."
            )

    data_collator = DataCollatorForSupervisedDataset(tokenizer=tokenizer)
    data_module = dict(
        train_dataset=train_dataset, eval_dataset=None, data_collator=data_collator
    )
    trainer = Trainer(
        model=model, tokenizer=tokenizer, args=training_args, **data_module
    )

    trainer.train()
    trainer.save_model()
    trainer.save_state()


if __name__ == "__main__":
    train()
