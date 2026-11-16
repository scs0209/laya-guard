# 내 명령으로 모델 추가 학습하기

[← README로](../README.ko.md) · [English](README.md) · **한국어**

규칙(policy.yaml과 safe lane)은 판단할 수 있는 것을 판단하고, 규칙이 정하지 못한 명령은 모델로 갑니다. 기본 모델은
[laya-guard-multilingual](https://huggingface.co/Ayaannnnnnn/laya-guard-multilingual)입니다. 이 스크립트들로 Laya를
추가 학습한 모델이며, 1차 리비전은 셸 명령으로, 2차 리비전은 거기서 이어서 데스크톱 클릭을 더해 학습했습니다
(Hub의 모델 카드에 둘 다 있습니다). 이 스크립트로 내 에이전트가 실행한 셸 명령을 가지고 같은 일을 할 수 있습니다.
학습 세트를 만들고, 무료 GPU에서 학습하고, 결과를 laya-guard가 쓰는 모델과 학습에 쓰지 않은 세트로 비교합니다.
데이터는 git이 무시하는 `train/`에만 남습니다. 학습한 체크포인트를 쓰려면 `LAYA_GUARD_MODEL`에 그 폴더를 지정하세요.

## 순서

```bash
# 1. 에이전트 대화 기록에서 명령을 모읍니다. --projects 아래만 읽고, --exclude 단어가 들어간 폴더나 명령은 뺍니다
python finetune/extract.py --projects ~/Projects --exclude yourcompany,yourclient

# 2. 규칙이 이미 판단하는 명령을 가려냅니다(각 명령의 원래 폴더에서). 나머지(rule "model")에 라벨이 필요합니다.
#    --batches 6은 그 명령을 train/batch1..6.jsonl로도 나눠 써서 나눠 라벨링할 수 있게 합니다
python finetune/split.py --batches 6
```

3. `rule`이 `model`인 명령에 [LABELING.md](LABELING.md)를 기준으로 라벨을 붙여 `train/labels*.jsonl`에
   `{"id": <train/commands.jsonl의 명령 id>, "label": "allow"|"review"|"block", "why": "..."}` 줄로 씁니다. id는
   실행된 명령 그대로의 해시라서, extract.py를 다시 돌려도 라벨이 다른 명령으로 옮겨 가지 않습니다.
   `train/labels_overrides.jsonl`은 마지막에 읽으니 수정은 거기에 씁니다. 실제 명령 중 `block`이 맞는 경우는
   드뭅니다. 직접 쓴 예시는 `train/synthetic.jsonl`, `train/augment.jsonl`, `train/round3.jsonl`에
   `{"command", "label"}` 줄로 넣습니다. build.py는 모든 출처를 통틀어 같은 명령을 한 번만 남기고, 평가 사례와 같거나
   비슷한(≥ 0.85) 것은 실제 명령이라도 빼며, 에이전트 세션 하나는 학습과 검증 중 한쪽에만 둡니다.

```bash
# 4. laya-guard가 묻는 질문 그대로 train/train.jsonl과 train/val.jsonl을 만듭니다.
#    --no-safe-lane: safe lane이 허용하는 명령은 뺍니다(실제로는 모델이 볼 일이 없습니다)
#    --oversample 2: allow가 많을 때 review와 block 행을 학습 쪽에 두 번씩 넣습니다(빼면 모델이 차단을 거의 못 합니다)
#    데스크톱 클릭(finetune/desktop_data.py)은 따로 나누며, --no-desktop이면 넣지 않습니다
python finetune/build.py --no-safe-lane --oversample 2

# 5. 모델을 불러오지 않고 파일만 검사합니다
laya-train --data train/train.jsonl --eval train/val.jsonl --base multilingual --out train/ckpt --dry-run
```

6. GPU에서 학습합니다. [colab.ipynb](colab.ipynb)를 Google Colab(무료 T4)에서 열고, 두 파일을 올리고, 셀을 차례로
   실행한 뒤, 받은 체크포인트를 `train/ckpt/`에 풉니다. 노트북은 laya-guard가 쓰는 모델에서, `guard.py`가 고정한
   리비전으로 이어서 학습합니다(2 에포크, 인코더 학습률 1e-5). Mac에서도 되지만 느리고, 그동안 컴퓨터를 쓰기 어렵습니다.

```bash
# 7. laya-guard가 쓰는 모델과 내 체크포인트를 벤치마크, 평가 세트, 클릭 세트(학습 데이터에 없음)와
#    train/val.jsonl로 비교합니다. current 대신 base를 쓰면 원래 Laya와 비교합니다
python finetune/evaluate.py current
python finetune/evaluate.py train/ckpt
python finetune/evaluate.py --compare
```

분할을 한 번 만든 뒤 여러 학습을 비교하는 동안에는, 새 예시를 다른 행과 같은 모양으로 `train/train.jsonl`에만
덧붙이세요. build.py는 에이전트 세션 단위로 나누기 때문에, 행이 몇 개만 늘어도 어떤 세션이 `val.jsonl`로 가는지가
다시 섞이고(한 번은 새 예시 30개 중 17개가 그리로 갔습니다) 학습끼리 비교할 수 없게 됩니다. 비교가 끝나면 다시
만드세요.

## 결과 읽는 법

- **검증 정확도만 믿지 마세요.** 대부분이 `allow`이면 늘 allow라고 답하는 모델도 점수가 높게 나옵니다. 라벨별 줄과
  모델이 본 적 없는 세트를 보세요.
- **합격 기준은 결과를 보기 전에 정하세요.** 예를 들어 모델 혼자 허용하는 일상 명령은 늘고, 잡아내는 공격은 줄지
  않고, 한국어는 나빠지지 않고, 최종 판정은 하나도 나빠지지 않을 것.
- **사례별 변화를 읽으세요.** `--compare` 끝에 나옵니다.
- **최종 판정뿐 아니라 모델 단독 줄도 보세요.** 규칙이 약해진 모델을 가려 줘서, 모델이 위장한 명령
  (`rm -rf customer-data  # clean stale build output`)을 통과시켜도 최종 줄은 그대로일 수 있습니다.
- **클릭 세트와 함께 val의 클릭도 보세요.** 클릭 세트의 위험한 버튼은 하는 일을 이름에 드러냅니다(Delete
  Permanently). val의 버튼은 덜 드러나는 경우가 많습니다(Reply All, Revert to Saved). 한 학습은 클릭 세트에서 31개 중
  29개를 잡고 val에서는 17개 중 7개를 잡았습니다.
- **한두 건 차이는 우연일 수 있습니다.** 데이터를 비교하는 동안에는 seed를 고정하고, 후보가 나오면 seed를 바꿔 한 번 더
  돌리세요. 같은 세트로 두 seed 중 나은 쪽을 고르면 결과가 조금 좋게 보입니다.
- **보고하는 세트를 보고 기준값이나 단어 목록을 고르지 마세요.** val에서 고르거나, 그러지 않았다고 밝히세요.
- 허락된 경우에만 내 명령으로 학습하세요. 가리기는 토큰, 키, URL과 플래그의 비밀번호, `NAME=value` 비밀값, 긴 불투명
  id를 잡지만 모든 비밀을 알 수는 없습니다. 어딘가에 올리거나 그 데이터로 학습한 체크포인트를 공유하기 전에
  `train/train.jsonl`을 읽어 보세요. `train/`은 나만 읽을 수 있게 만들어집니다.

## 2차 리비전까지 겪은 일

학습 15번이 평가 결과와 함께 `train/`에 남아 있고, 아래 각 줄은 어떤 학습에서 드러난 일입니다.

- **매번 원래 Laya에서 다시 학습했습니다.** 노트북이 `multilingual`에서 시작했기 때문에 학습할 때마다 셸 명령도 다시
  배웠고, 매번 다른 몇 개가 흔들렸습니다. 쓰고 있는 모델에서 이어서 학습하자 유지됐습니다.
- **다른 조건으로 학습한 모델과 비교했습니다.** 1차 리비전은 지금은 없는 데이터로 4 에포크 학습했는데, 초기 학습들은
  더 새 데이터로 2 에포크였습니다. 그래서 회귀가 어느 쪽 때문인지 알 수 없었습니다. 클릭 없이 4 에포크로 돌린 학습에서도
  같은 회귀가 나와서 원인이 데이터에 있다는 것을 알았습니다.
- **비율을 옮기다 반대쪽으로 넘어갔습니다.** review 클릭이 많으면 평범한 클릭도 묻습니다(25개 중 14개만 허용).
  allow가 대부분이면 위험한 클릭을 통과시킵니다(31개 중 25개만 보류). `--oversample`을 빼면 차단을 거의 못 합니다
  (val에서 14개 중 2개).
- **모델 하나가 두 종류의 입력을 봅니다.** "지우기, 덮어쓰기, 옮기기는 묻는다"를 가르친 클릭이 `rm -rf dist`와
  `mv src/old.ts src/new.ts`까지 review 기준선 위로 올렸습니다.
- **쉬운 예시는 넓게 퍼집니다.** 평범한 `rm -rf coverage`를 allow로 넣었더니 `rm -rf customer-data  # clean stale
  build output`도 통과했습니다. 위장한 삭제를 block으로 함께 넣어야 했습니다.
- **클릭에 전용 질문을 따로 물었더니**(도구 호출 질문과 다른 지시와 기준) 클릭 결과가 나빠지고 block 클릭 두 개를
  통과시켰습니다. 그래서 laya-guard는 모든 입력에 질문 하나를 씁니다.
- **덜 드러나는 클릭에서 학습이 멈췄습니다.** 놓친 종류를 더 넣어도 놓치는 클릭이 바뀔 뿐이었습니다(어느 쪽이든 val에서
  17개 중 14개). 나머지는 `desktop.py`의 버튼 단어 규칙이 잡습니다.
- **같은 버튼이 분할 양쪽에 들어갔습니다.** 클릭을 앱 이름이 들어간 문장 단위로 묶어서, Mail의 "Send"는 학습에,
  Messages의 "Send"는 검증에 들어갈 수 있었습니다. val 클릭 82개 중 69개가 학습과 같은 라벨이었고, 위의 val 클릭 숫자는
  새 세트보다 후하게 나온 것입니다. 지금 build.py는 클릭을 라벨 단위로 묶습니다. 클릭 세트
  (`eval/testset_desktop.jsonl`)는 처음부터 라벨이 겹치지 않았습니다.
