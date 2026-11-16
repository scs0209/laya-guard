# Fine-tuning the model on your own commands

[← Back to README](../README.md) · **English** · [한국어](README.ko.md)

The rules (policy.yaml and the safe lane) decide what they can; the commands they can't settle go to the model. Out
of the box that is [laya-guard-multilingual](https://huggingface.co/Ayaannnnnnn/laya-guard-multilingual), Laya fine-tuned
with these scripts: the first revision on shell commands, the second continued from it with desktop clicks added (the
model card on the Hub has both). These scripts let you do the same on the shell commands your own agents ran: build a
training set, train on a free GPU, and compare the result with the model laya-guard runs on sets it never saw. Your
data stays in `train/`, which git ignores. To run your checkpoint, point `LAYA_GUARD_MODEL` at its folder.

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
#    --oversample 2: repeat review and block rows in train when allow dominates (keep it: without it the model
#    stopped blocking); the desktop clicks (finetune/desktop_data.py) are split on their own, --no-desktop leaves them out
python finetune/build.py --no-safe-lane --oversample 2

# 5. Check the files without loading the model
laya-train --data train/train.jsonl --eval train/val.jsonl --base multilingual --out train/ckpt --dry-run
```

6. Train on a GPU: open [colab.ipynb](colab.ipynb) in Google Colab (free T4), upload the two files, run the cells,
   and unzip the downloaded checkpoint into `train/ckpt/`. The notebook continues from the model laya-guard runs, at
   the revision `guard.py` pins (2 epochs, encoder learning rate 1e-5). On a Mac it is slow and takes the machine
   with it.

```bash
# 7. The model laya-guard runs vs your checkpoint, on the benchmark, the eval sets and the click set (never in the
#    training data), and on train/val.jsonl. `base` instead of `current` compares with the original Laya.
python finetune/evaluate.py current
python finetune/evaluate.py train/ckpt
python finetune/evaluate.py --compare
```

Once a split is built, add new examples to `train/train.jsonl` only, as rows shaped like its others, while you compare
runs: build.py splits by agent session, so a few new rows reshuffle which sessions land in `val.jsonl` (once, 17 of 30
new examples went there) and the runs stop being comparable. Rebuild when you are done.

## Reading the result

- **Don't trust the validation accuracy alone.** When most rows are `allow`, a model that always says allow scores
  high. Look at the per-label lines and at the sets the model never saw.
- **Write your pass criteria before you look.** For example: everyday commands the model alone allows go up, attacks
  it catches don't go down, Korean doesn't regress, and no final verdict gets worse.
- **Read the case-by-case changes** at the end of `--compare`.
- **Look at the model-only lines, not only the final ones.** The rules cover for a weaker model, so a final line can
  stay level while the model lets a disguised command through (`rm -rf customer-data  # clean stale build output`).
- **Look at val's clicks next to the click set.** The click set's risky buttons say what they do (Delete
  Permanently); val's are often quieter (Reply All, Revert to Saved). One run held 29 of 31 in the set and 7 of 17
  in val.
- **A difference of one or two cases can be chance.** Keep the seed fixed while you compare data, then run a second
  seed on the candidate. Choosing the better of two seeds on the same sets flatters it a little.
- **Don't tune a threshold or a word list on the sets you report.** Pick it on val, or say that you didn't.
- Train on your own commands only if you're allowed to. Masking catches tokens, keys, passwords in URLs and flags,
  `NAME=value` secrets and long opaque ids, but it can't know every secret: read `train/train.jsonl` before you upload
  it anywhere or share a checkpoint trained on it. `train/` is created readable by you only.

## What went wrong on the way to the second revision

Fifteen runs, kept in `train/` with their evaluations; each line is what a run showed.

- **Retraining from base Laya every time.** The notebook used to start from `multilingual`, so every run relearned the
  shell commands as well, and each one moved a different few. Continuing from the model in use kept them.
- **Comparing with a model trained under other conditions.** The first revision had 4 epochs on data that no longer
  exists; early runs used 2 epochs on newer data, so a regression could come from either. A run without clicks,
  4 epochs, showed the same regressions and put the cause in the data.
- **Moving the balance and overshooting.** Many review clicks: ordinary clicks asked about (14 of 25 allowed).
  Mostly allow: risky clicks let through (25 of 31 held). Dropping `--oversample`: almost no blocks (2 of 14 in val).
- **One model, two kinds of input.** Clicks that teach "deleting, overwriting, moving: ask" raised `rm -rf dist` and
  `mv src/old.ts src/new.ts` past the review line too.
- **Easy examples generalize widely.** Plain `rm -rf coverage` allows let `rm -rf customer-data  # clean stale build
  output` through; disguised deletions labeled block had to go in next to them.
- **Asking clicks a question of their own** (different instructions and criteria from the tool-call question) made
  the click results worse and let two block clicks through, so laya-guard asks one question for everything.
- **Learning stalled on quiet clicks.** Adding more of the kinds a run missed only moved which ones were missed (14 of 17
  in val either way); the button words in `desktop.py` hold the rest.
- **The same button on both sides of the split.** Clicks were grouped by their whole sentence, which names the app, so
  "Send" in Mail could train while "Send" in Messages validated: 69 of val's 82 clicks shared a label with training,
  and the val click figures above are kinder than a fresh set would be. build.py now groups clicks by label. The click
  set (`eval/testset_desktop.jsonl`) was always label-disjoint.
