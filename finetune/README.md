# Fine-tuning the model on your own commands

The rules (policy.yaml and the safe lane) decide what they can; the commands they can't settle go to Laya, which
laya-guard uses as it ships, without fine-tuning. Its model card describes it as a base to fine-tune. These scripts
build a training set from the shell commands your own agents ran, train on a free GPU, and compare the result with
the base model on sets it never saw. Your data stays in `train/`, which git ignores.

Nothing here changes what laya-guard runs. A first attempt (in the [CHANGELOG](../CHANGELOG.md)) raised validation
accuracy from 0.61 to 0.90 while everyday commands and Korean got worse on the held-out sets, so it wasn't used.

## Steps

```bash
# 1. Commands from your agents' transcripts, only under --projects, never a folder or command with an --exclude word
python finetune/extract.py --projects ~/Projects --exclude yourcompany,yourclient

# 2. Which ones the rules already decide; the rest (rule "model") need a label
python finetune/split.py
```

3. Label the commands whose `rule` is `model` against [LABELING.md](LABELING.md), as `train/labels*.jsonl` lines
   `{"i": <line number in train/commands.jsonl, from 0>, "label": "allow"|"review"|"block", "why": "..."}`. A later
   file wins, so corrections can go in `train/labels_overrides.jsonl`. Real commands rarely deserve `block`; examples
   you write yourself go in `train/synthetic.jsonl` or `train/augment.jsonl` as `{"command", "label"}` lines, and any
   that copy or closely resemble (similarity ≥ 0.85) an evaluation case are dropped.

```bash
# 4. train/train.jsonl and train/val.jsonl, with the exact question laya-guard asks.
#    --no-safe-lane: drop what the safe lane allows (the model never sees it in use);
#    --oversample 2: repeat review and block rows in train when allow dominates
python finetune/build.py --no-safe-lane --oversample 2

# 5. Check the files without loading the model
laya-train --data train/train.jsonl --eval train/val.jsonl --base multilingual --out train/ckpt --dry-run
```

6. Train on a GPU: open [colab.ipynb](colab.ipynb) in Google Colab (free T4), upload the two files, run the cells,
   and unzip the downloaded checkpoint into `train/ckpt/`. On a Mac it is slow and takes the machine with it.

```bash
# 7. Base model vs your checkpoint, on the benchmark and eval sets (never in the training data)
python finetune/evaluate.py base
python finetune/evaluate.py train/ckpt
python finetune/evaluate.py --compare
```

## Reading the result

- **Don't trust the validation accuracy alone.** When most rows are `allow`, a model that always says allow scores
  high. Look at the per-label lines and at the sets the model never saw.
- **Write your pass criteria before you look.** For example: everyday commands the model alone allows go up, attacks
  it catches don't go down, Korean doesn't regress, and no final verdict gets worse.
- **Read the case-by-case changes** at the end of `--compare`.
- Train on your own commands only if you're allowed to. A checkpoint trained on them shouldn't be shared.
