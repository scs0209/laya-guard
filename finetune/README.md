# Fine-tuning the model on your own commands

The rules (policy.yaml and the safe lane) decide what they can; the commands they can't settle go to the model. Out
of the box that is [laya-guard-multilingual](https://huggingface.co/Ayaannnnnnn/laya-guard-multilingual), Laya fine-tuned
with these scripts (three runs: the CHANGELOG has what went wrong in the first two). These scripts let you do the same
on the shell commands your own agents ran: build a training set, train on a free GPU, and compare the result with the
base model on sets it never saw. Your data stays in `train/`, which git ignores. To run your checkpoint, point
`LAYA_GUARD_MODEL` at its folder.

## Steps

```bash
# 1. Commands from your agents' transcripts, only under --projects, never a folder or command with an --exclude word
python finetune/extract.py --projects ~/Projects --exclude yourcompany,yourclient

# 2. Which ones the rules already decide (in each command's own folder); the rest (rule "model") need a label.
#    --batches 6 also writes them to train/batch1..6.jsonl, to label in parts
python finetune/split.py --batches 6
```

3. Label the commands whose `rule` is `model` against [LABELING.md](LABELING.md), as `train/labels*.jsonl` lines
   `{"id": <the command's id in train/commands.jsonl>, "label": "allow"|"review"|"block", "why": "..."}`. The id is a
   hash of the command as it ran, so running extract.py again never moves a label onto another command.
   `train/labels_overrides.jsonl` is read last: corrections go there. Real commands rarely deserve `block`; examples
   you write yourself go in `train/synthetic.jsonl`, `train/augment.jsonl` or `train/round3.jsonl` as
   `{"command", "label"}` lines. build.py keeps a command once across all sources, drops anything equal or similar
   (≥ 0.85) to an evaluation case, real commands included, and keeps each agent session on one side of the split.

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
- Train on your own commands only if you're allowed to. Masking catches tokens, keys, passwords in URLs and flags,
  `NAME=value` secrets and long opaque ids, but it can't know every secret: read `train/train.jsonl` before you upload
  it anywhere or share a checkpoint trained on it. `train/` is created readable by you only.
