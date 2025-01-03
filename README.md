# finetuning-tutorial

![falcon](./decompile.webp)
## structure of the repository:
```
finetuning-tutorial/
├── README.md
├── ds.json
├── finetune.py
├── requirements.txt
└── train.sh
```
### Files:

* README.md: Provides instructions on how to set up and run the fine-tuning tutorial.
* ds.json: DeepSpeed configuration file.
* finetune.py: A Python script that presumably contains the logic for fine-tuning a model.
* requirements.txt: Lists the Python dependencies required to run the scripts in this repository.
* train.sh: A shell script to execute the training process, making it easier to run the fine-tuning with a single command.
## Steps

### Make the script executable
```bash
cd finetuning-tutorial && chmod a+x ./train.sh
```

### Install dependencies
```python
pip install -r requirements.txt
```

### Run the training script
```bash
./train.sh
```
