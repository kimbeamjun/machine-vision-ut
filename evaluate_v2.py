"""
evaluate_v2.py — emotion_model_v2 상세 평가
  - Confusion Matrix (정규화 + raw count)
  - Per-class Precision / Recall / F1
  - Val/Test 갭 분석
  - 틀린 샘플 이미지 저장 (top-K 어려운 샘플)

실행:
  python evaluate_v2.py --data ./dataset --weights models/weights/emotion_model_v2.pth
"""

import argparse, os, sys, json
from pathlib import Path
from collections import defaultdict

import torch
import torch.nn as nn
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from sklearn.metrics import (
    confusion_matrix, classification_report,
    ConfusionMatrixDisplay
)
from torchvision import transforms
from torch.utils.data import DataLoader, Dataset
from PIL import Image

# ────────────────────────────────────────────────────────────
# 0. 인자
# ────────────────────────────────────────────────────────────
parser = argparse.ArgumentParser()
parser.add_argument("--data",    default="./dataset")
parser.add_argument("--weights", default="models/weights/emotion_model_v2.pth")
parser.add_argument("--batch",   type=int, default=32)
parser.add_argument("--topk",    type=int, default=16,
                    help="틀린 샘플 중 confidence 높은 순 저장 수")
parser.add_argument("--out",     default="eval_results",
                    help="결과 저장 폴더")
args = parser.parse_args()

os.makedirs(args.out, exist_ok=True)
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
print(f"Device: {device}")

# ────────────────────────────────────────────────────────────
# 1. 클래스 정의 (v2 학습 로그 기준)
# ────────────────────────────────────────────────────────────
CLASSES = ["neutral", "negative", "positive", "confusion", "surprise"]
# val/test에 실제 등장한 클래스만 (confusion=0)
ACTIVE_CLASSES = ["neutral", "negative", "positive", "surprise"]
CLS2IDX = {c: i for i, c in enumerate(CLASSES)}
IDX2CLS = {i: c for i, c in enumerate(CLASSES)}

# ────────────────────────────────────────────────────────────
# 2. 데이터셋
# ────────────────────────────────────────────────────────────
MEAN = [0.485, 0.456, 0.406]
STD  = [0.229, 0.224, 0.225]

eval_tf = transforms.Compose([
    transforms.Resize(256),
    transforms.CenterCrop(224),
    transforms.ToTensor(),
    transforms.Normalize(MEAN, STD),
])

class EmotionDataset(Dataset):
    """
    dataset/
      ck+/   또는  affectnet/  아래
        <class_name>/
          img.jpg ...
    또는
      test/ val/
        <class_name>/
          img.jpg ...
    두 구조 모두 탐색
    """
    def __init__(self, root, split="test", transform=None):
        self.transform = transform
        self.samples   = []   # (path, label_idx)

        root = Path(root)
        # 우선 split 폴더 탐색
        split_dir = root / split
        if split_dir.exists():
            self._load_from(split_dir)
        else:
            # fallback: 전체 데이터셋에서 파일명 패턴으로 split 추정
            # (학습 코드가 random_split 사용 시 재현 불가 → 전체 로드)
            print(f"[WARN] '{split_dir}' 없음 → 전체 데이터 로드")
            for subdir in sorted(root.iterdir()):
                if subdir.is_dir():
                    self._load_from(subdir)

        if len(self.samples) == 0:
            raise FileNotFoundError(
                f"'{root}/{split}' 에서 이미지를 찾을 수 없습니다.\n"
                "구조 예시: dataset/test/<class>/*.jpg"
            )
        print(f"[{split}] {len(self.samples)}장 로드")

    def _load_from(self, folder: Path):
        for cls_dir in sorted(folder.iterdir()):
            if not cls_dir.is_dir():
                continue
            label_name = cls_dir.name.lower()
            # 폴더 이름 → 클래스 매핑 (유연하게)
            matched = None
            for c in CLASSES:
                if c in label_name or label_name in c:
                    matched = c
                    break
            if matched is None:
                print(f"  [SKIP] '{cls_dir.name}' — 클래스 매핑 실패")
                continue
            idx = CLS2IDX[matched]
            for ext in ("*.jpg","*.jpeg","*.png","*.bmp"):
                for img_path in cls_dir.glob(ext):
                    self.samples.append((str(img_path), idx))

    def __len__(self):
        return len(self.samples)

    def __getitem__(self, i):
        path, label = self.samples[i]
        img = Image.open(path).convert("RGB")
        if self.transform:
            img = self.transform(img)
        return img, label, path


# ────────────────────────────────────────────────────────────
# 3. 모델 로드 (v1 백본 + v2 head 구조 자동 감지)
# ────────────────────────────────────────────────────────────
def load_model(weight_path: str, num_classes: int = 5):
    ckpt = torch.load(weight_path, map_location=device)

    # checkpoint가 dict인지 state_dict인지 판별
    if isinstance(ckpt, dict):
        if "model_state_dict" in ckpt:
            state = ckpt["model_state_dict"]
        elif "state_dict" in ckpt:
            state = ckpt["state_dict"]
        else:
            state = ckpt
    else:
        state = ckpt

    # 첫 레이어 키로 백본 아키텍처 추정
    first_key = next(iter(state))
    print(f"  첫 키: {first_key}")

    if "efficientnet" in first_key.lower() or "features.0" in first_key:
        from torchvision.models import efficientnet_b2
        model = efficientnet_b2(weights=None)
        model.classifier[1] = nn.Linear(model.classifier[1].in_features, num_classes)
    elif "resnet" in first_key.lower() or "layer1" in first_key:
        from torchvision.models import resnet50
        model = resnet50(weights=None)
        model.fc = nn.Linear(model.fc.in_features, num_classes)
    else:
        # 가장 일반적인 경우: EfficientNet-B2 시도
        print("  [WARN] 아키텍처 자동 감지 실패 → EfficientNet-B2 시도")
        from torchvision.models import efficientnet_b2
        model = efficientnet_b2(weights=None)
        model.classifier[1] = nn.Linear(model.classifier[1].in_features, num_classes)

    # strict=False 로 head 불일치 허용
    missing, unexpected = model.load_state_dict(state, strict=False)
    if missing:
        print(f"  Missing keys ({len(missing)}): {missing[:3]} ...")
    if unexpected:
        print(f"  Unexpected keys ({len(unexpected)}): {unexpected[:3]} ...")

    model.to(device).eval()
    return model


print(f"\n모델 로드: {args.weights}")
model = load_model(args.weights)

# ────────────────────────────────────────────────────────────
# 4. 추론 함수
# ────────────────────────────────────────────────────────────
@torch.no_grad()
def run_inference(loader):
    all_preds, all_labels, all_probs, all_paths = [], [], [], []
    for imgs, labels, paths in loader:
        imgs = imgs.to(device)
        logits = model(imgs)
        probs  = torch.softmax(logits, dim=1).cpu()
        preds  = probs.argmax(dim=1)

        all_preds.extend(preds.numpy())
        all_labels.extend(labels.numpy())
        all_probs.extend(probs.numpy())
        all_paths.extend(paths)

    return (np.array(all_preds), np.array(all_labels),
            np.array(all_probs), all_paths)


# ────────────────────────────────────────────────────────────
# 5. 평가 실행 (val + test)
# ────────────────────────────────────────────────────────────
results = {}

for split in ["val", "test"]:
    print(f"\n{'='*50}")
    print(f"  {split.upper()} 평가")
    print(f"{'='*50}")

    try:
        ds = EmotionDataset(args.data, split=split, transform=eval_tf)
    except FileNotFoundError as e:
        print(e)
        continue

    loader = DataLoader(ds, batch_size=args.batch, shuffle=False,
                        num_workers=4, pin_memory=True)
    preds, labels, probs, paths = run_inference(loader)

    # 활성 클래스 이름 목록
    present_idxs = sorted(set(labels.tolist()))
    present_names = [IDX2CLS[i] for i in present_idxs]

    acc = (preds == labels).mean()
    print(f"\nAccuracy: {acc:.4f}")

    report = classification_report(
        labels, preds,
        labels=present_idxs,
        target_names=present_names,
        digits=3, zero_division=0
    )
    print(report)

    results[split] = {
        "acc": float(acc),
        "preds": preds, "labels": labels,
        "probs": probs, "paths": paths,
        "present_idxs": present_idxs,
        "present_names": present_names,
    }

    # ── 5a. Confusion Matrix ──────────────────────────────
    cm = confusion_matrix(labels, preds, labels=present_idxs)
    cm_norm = cm.astype(float) / cm.sum(axis=1, keepdims=True)

    fig, axes = plt.subplots(1, 2, figsize=(14, 6))
    fig.suptitle(f"Confusion Matrix — {split.upper()}  (Acc={acc:.3f})",
                 fontsize=14, fontweight="bold")

    for ax, mat, title in zip(
            axes,
            [cm, cm_norm],
            ["Raw Count", "Normalized (Recall)"]):
        disp = ConfusionMatrixDisplay(mat, display_labels=present_names)
        disp.plot(ax=ax, colorbar=False,
                  values_format=".2f" if "Norm" in title else "d")
        ax.set_title(title)
        ax.tick_params(axis="x", rotation=30)

    plt.tight_layout()
    cm_path = f"{args.out}/confusion_matrix_{split}.png"
    plt.savefig(cm_path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  → {cm_path}")

    # ── 5b. 틀린 샘플 시각화 ─────────────────────────────
    wrong_mask  = preds != labels
    wrong_idxs  = np.where(wrong_mask)[0]

    if len(wrong_idxs) == 0:
        print("  틀린 샘플 없음!")
        continue

    # confidence 높은(= 모델이 확신하며 틀린) 순 정렬
    wrong_conf  = probs[wrong_idxs].max(axis=1)
    sorted_idx  = wrong_idxs[np.argsort(-wrong_conf)]
    topk        = sorted_idx[:args.topk]

    cols = 4
    rows = (len(topk) + cols - 1) // cols
    fig, axes = plt.subplots(rows, cols, figsize=(cols * 3, rows * 3.2))
    axes = np.array(axes).flatten()
    fig.suptitle(f"Hard Mistakes — {split.upper()}  (confidence 높은 순)",
                 fontsize=13, fontweight="bold")

    for ax_i, sample_idx in enumerate(topk):
        ax = axes[ax_i]
        img = Image.open(paths[sample_idx]).convert("RGB")
        ax.imshow(img)
        true_name = IDX2CLS[labels[sample_idx]]
        pred_name = IDX2CLS[preds[sample_idx]]
        conf      = probs[sample_idx][preds[sample_idx]]
        ax.set_title(
            f"GT: {true_name}\nPred: {pred_name}  ({conf:.2f})",
            fontsize=8,
            color="red" if true_name != pred_name else "green"
        )
        ax.axis("off")

    for ax in axes[len(topk):]:
        ax.axis("off")

    plt.tight_layout()
    hard_path = f"{args.out}/hard_mistakes_{split}.png"
    plt.savefig(hard_path, dpi=120, bbox_inches="tight")
    plt.close()
    print(f"  → {hard_path}")


# ────────────────────────────────────────────────────────────
# 6. Val vs Test 갭 분석 + 클래스별 비교 차트
# ────────────────────────────────────────────────────────────
if "val" in results and "test" in results:
    print("\n" + "="*50)
    print("  VAL vs TEST 갭 분석")
    print("="*50)

    val_r  = results["val"]
    test_r = results["test"]

    from sklearn.metrics import f1_score
    val_f1  = f1_score(val_r["labels"],  val_r["preds"],
                       labels=val_r["present_idxs"],  average=None,
                       zero_division=0)
    test_f1 = f1_score(test_r["labels"], test_r["preds"],
                       labels=test_r["present_idxs"], average=None,
                       zero_division=0)

    # 공통 클래스만 비교
    common = sorted(
        set(val_r["present_names"]) & set(test_r["present_names"])
    )
    val_dict  = dict(zip(val_r["present_names"],  val_f1))
    test_dict = dict(zip(test_r["present_names"], test_f1))

    print(f"\n{'클래스':<12} {'Val F1':>8} {'Test F1':>8} {'갭':>8}")
    print("-" * 38)
    for c in common:
        gap = val_dict.get(c, 0) - test_dict.get(c, 0)
        flag = " ⚠" if abs(gap) > 0.15 else ""
        print(f"{c:<12} {val_dict.get(c,0):>8.3f} {test_dict.get(c,0):>8.3f} {gap:>+8.3f}{flag}")
    print(f"\nOverall Acc:  Val={val_r['acc']:.3f}  Test={test_r['acc']:.3f}  "
          f"갭={val_r['acc']-test_r['acc']:+.3f}")

    # 차트
    x = np.arange(len(common))
    w = 0.35
    fig, ax = plt.subplots(figsize=(max(8, len(common)*1.5), 5))
    ax.bar(x - w/2, [val_dict.get(c,0)  for c in common], w, label="Val",  color="#0066CC", alpha=0.85)
    ax.bar(x + w/2, [test_dict.get(c,0) for c in common], w, label="Test", color="#FF6B35", alpha=0.85)
    ax.set_xticks(x); ax.set_xticklabels(common, fontsize=11)
    ax.set_ylim(0, 1.05); ax.set_ylabel("F1 Score"); ax.legend()
    ax.set_title("Val vs Test F1 per Class", fontweight="bold")
    ax.axhline(0.8, color="gray", linestyle="--", linewidth=0.8, alpha=0.5)
    for xi, c in enumerate(common):
        ax.text(xi - w/2, val_dict.get(c,0)  + 0.01, f"{val_dict.get(c,0):.2f}",  ha="center", fontsize=9)
        ax.text(xi + w/2, test_dict.get(c,0) + 0.01, f"{test_dict.get(c,0):.2f}", ha="center", fontsize=9)
    plt.tight_layout()
    gap_path = f"{args.out}/val_test_gap.png"
    plt.savefig(gap_path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"\n  → {gap_path}")


# ────────────────────────────────────────────────────────────
# 7. 진단 요약 출력
# ────────────────────────────────────────────────────────────
print("\n" + "="*50)
print("  진단 요약")
print("="*50)

if "test" in results:
    r = results["test"]
    from sklearn.metrics import precision_score, recall_score, f1_score
    p = precision_score(r["labels"], r["preds"], labels=r["present_idxs"],
                        average=None, zero_division=0)
    re= recall_score(   r["labels"], r["preds"], labels=r["present_idxs"],
                        average=None, zero_division=0)
    f1= f1_score(       r["labels"], r["preds"], labels=r["present_idxs"],
                        average=None, zero_division=0)

    print(f"\n{'클래스':<12} {'Precision':>10} {'Recall':>8} {'F1':>8}")
    print("-" * 42)
    weakest = []
    for cls_name, pi, ri, fi in zip(r["present_names"], p, re, f1):
        flag = " ← 약점" if fi < 0.75 else ""
        print(f"{cls_name:<12} {pi:>10.3f} {ri:>8.3f} {fi:>8.3f}{flag}")
        if fi < 0.75:
            weakest.append(cls_name)

    print("\n[다음 단계 권장]")
    if weakest:
        print(f"  1. 약한 클래스: {weakest}")
        print(f"     → 해당 클래스 데이터 보강 또는 focal loss 적용 검토")
    if "val" in results:
        gap = results["val"]["acc"] - results["test"]["acc"]
        if gap > 0.1:
            print(f"  2. Val/Test 갭 {gap:+.3f} 큼")
            print(f"     → Val 셋 재구성 (stratified, neutral 포함) 권장")
    print(f"  3. confusion 클래스는 데이터 없음 → 수집 or 제거 결정 필요")

print(f"\n결과 저장 폴더: {args.out}/")
print("  confusion_matrix_val.png / test.png")
print("  hard_mistakes_val.png   / test.png")
print("  val_test_gap.png")
