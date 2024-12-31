WORKSPACE="/data"
DATA_PATH="Neo111x/decompile_LLM"
OUTPUT_PATH="${WORKSPACE}/output_models/falCodecompile-large-7b-v2"
MODEL_PATH="tiiuae/Falcon3-7B-Base"

deepspeed --num_gpus=4 finetune.py \
    --model_name_or_path $MODEL_PATH \
    --data_path $DATA_PATH \
    --output_dir $OUTPUT_PATH \
    --num_train_epochs 2 \
    --model_max_length 1024 \
    --per_device_train_batch_size 4 \
    --gradient_accumulation_steps 4 \
    --evaluation_strategy "no" \
    --save_strategy "steps" \
    --use_flash_attention \
    --save_steps 500 \
    --save_total_limit 100 \
    --learning_rate 2e-5 \
    --max_grad_norm 1.0 \
    --weight_decay 0.1 \
    --warmup_ratio 0.025 \
    --logging_steps 1 \
    --lr_scheduler_type "cosine" \
    --gradient_checkpointing True \
    --report_to "wandb" \
    --bf16 True \
    --deepspeed "ds.json"
#    --master_port 29600 
