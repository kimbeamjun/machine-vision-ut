# evaluate_v2.py — train_v2.py 구조 완전 일치 버전
#
# 실행:
#   python evaluate_v2.py --affectnet ./dataset --weights models/weights/emotion_model_v2.pth
#
import os, random, argparse
import numpy as np
from pathlib import Path
from collections import Counter, defaultdict

import torch
import torch.nn as nn
import timm
import cv2
from torchvision import transforms
from torch.utils.data import DataLoader
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from sklearn.metrics import (
    confusion_matrix, classification_report,
    ConfusionMatrixDisplay, f1_score,
    precision_score, recall_score,
)

# ── train_v2.py 와 완전히 동일한 상수 ──────────────────────
FOLDER_TO_CLASS = {
    "anger":"negative","Anger":"negative",
    "contempt":"negative","Contempt":"negative",
    "disgust":"negative",
    "fear":"negative","Fear":"negative",
    "happy":"positive","Happy":"positive",
    "sad":"negative","Sad":"negative","sadness":"negative",
    "neutral":"neutral","Neutral":"neutral",
    "surprise":"surprise","Surprise":"surprise",
    "1":"surprise","2":"negative","3":"negative",
    "4":"positive","5":"negative","6":"negative","7":"neutral",
}
EMOTION_CLASSES = ["neutral", "negative", "positive", "confusion", "surprise"]
CLASS_TO_IDX    = {c: i for i, c in enumerate(EMOTION_CLASSES)}
IDX_TO_CLASS    = {i: c for i, c in enumerate(EMOTION_CLASSES)}

IMG_SIZE    = 260
BATCH_SIZE  = 32
NUM_WORKERS = 2
FEAT_DIM    = 1408
DROPOUT     = 0.35
MEAN = [0.485, 0.456, 0.406]
STD  = [0.229, 0.224, 0.225]
VAL_RATIO  = 0.15
TEST_RATIO = 0.15
SEED       = 42

# ── 모델 (train_v2.py EmotionModel 그대로) ─────────────────
class EmotionModel(nn.Module):
    def __init__(self, n=5, drop=DROPOUT):
        super().__init__()
        self.backbone = timm.create_model(
            "efficientnet_b2", pretrained=False,
            num_classes=0, global_pool="avg"
        )
        self.head = nn.Sequential(
            nn.LayerNorm(FEAT_DIM), nn.Dropout(drop),
            nn.Linear(FEAT_DIM, 512), nn.ReLU(),
            nn.Dropout(drop), nn.Linear(512, n),
        )
    def forward(self, x):
        if x.dim() == 5:
            B, T, C, H, W = x.shape
            return self.head(self.backbone(x.view(B*T,C,H,W)).view(B,T,FEAT_DIM))
        return self.head(self.backbone(x))

# ── 데이터셋 (train_v2.py EmotionDataset eval 모드) ─────────
class EmotionDataset(torch.utils.data.Dataset):
    def __init__(self, samples):
        self.samples = samples
        self.tf = transforms.Compose([
            transforms.ToPILImage(),
            transforms.Resize((IMG_SIZE+20, IMG_SIZE+20)),
            transforms.CenterCrop((IMG_SIZE, IMG_SIZE)),
            transforms.ToTensor(),
            transforms.Normalize(MEAN, STD),
        ])
    def __len__(self): return len(self.samples)
    def __getitem__(self, idx):
        p, label = self.samples[idx]
        img = cv2.imread(str(p))
        if img is None:
            img = np.zeros((IMG_SIZE, IMG_SIZE, 3), dtype=np.uint8)
        else:
            img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        # train_v2 는 unsqueeze(0) 해서 (1,C,H,W) 로 넣음
        return self.tf(img).unsqueeze(0), label, str(p)

# ── 데이터 로드 (train_v2.py _load_dir 그대로) ──────────────
def _load_dir(base: Path):
    samples = []
    if not base.exists():
        return samples
    for d in sorted(base.iterdir()):
        if not d.is_dir():
            continue
        cls = FOLDER_TO_CLASS.get(d.name) or FOLDER_TO_CLASS.get(d.name.lower())
        if cls is None:
            continue
        idx = CLASS_TO_IDX[cls]
        for p in list(d.glob("*.png")) + list(d.glob("*.jpg")) + list(d.glob("*.jpeg")):
            samples.append((p, idx))
    return samples

def load_ckplus(d: Path):
    s = _load_dir(d)
    print(f"[CK+] {len(s)}장")
    return s

def load_affectnet(d: Path):
    s = []
    for split in ["Train","train","Test","test","val","Val"]:
        s += _load_dir(d / split)
    if not s:
        s = _load_dir(d)
    print(f"[AffectNet] {len(s)}장")
    return s

def split_ds(samples):
    random.seed(SEED)
    random.shuffle(samples)
    n  = len(samples)
    nt = int(n * TEST_RATIO)
    nv = int(n * VAL_RATIO)
    return samples[nt+nv:], samples[nt:nt+nv], samples[:nt]

# ── 추론 ────────────────────────────────────────────────────
@torch.no_grad()
def run_inference(model, loader, device):
    model.eval()
    all_preds, all_labels, all_probs, all_paths = [], [], [], []
    for imgs, labels, paths in loader:
        imgs = imgs.to(device)
        with torch.amp.autocast("cuda"):
            logits = model(imgs)[:, 0, :]          # (B,5)
        probs = torch.softmax(logits, dim=1).cpu().numpy()
        preds = probs.argmax(axis=1)
        all_preds.extend(preds)
        all_labels.extend(labels.numpy())
        all_probs.extend(probs)
        all_paths.extend(paths)
    return (np.array(all_preds), np.array(all_labels),
            np.array(all_probs),  all_paths)

# ── 차트: confusion matrix ──────────────────────────────────
def plot_cm(labels, preds, present_idxs, present_names, title, path):
    cm      = confusion_matrix(labels, preds, labels=present_idxs)
    cm_norm = cm.astype(float) / cm.sum(axis=1, keepdims=True).clip(1)

    fig, axes = plt.subplots(1, 2, figsize=(14, 6))
    fig.suptitle(title, fontsize=13, fontweight="bold")
    for ax, mat, fmt, t in zip(
            axes,
            [cm, cm_norm],
            ["d", ".2f"],
            ["Raw Count", "Normalized (Recall)"]):
        ConfusionMatrixDisplay(mat, display_labels=present_names).plot(
            ax=ax, colorbar=False, values_format=fmt)
        ax.set_title(t)
        ax.tick_params(axis="x", rotation=30)
    plt.tight_layout()
    plt.savefig(path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  -> {path}")

# ── 차트: 틀린 샘플 ─────────────────────────────────────────
def plot_hard(preds, labels, probs, paths, title, path, topk=16):
    wrong = np.where(preds != labels)[0]
    if len(wrong) == 0:
        print("  no mistakes"); return
    conf   = probs[wrong].max(axis=1)
    topk_i = wrong[np.argsort(-conf)][:topk]

    cols = 4
    rows = (len(topk_i) + cols - 1) // cols
    fig, axes = plt.subplots(rows, cols, figsize=(cols*3, rows*3.2))
    axes = np.array(axes).flatten()
    fig.suptitle(title, fontsize=12, fontweight="bold")
    for i, si in enumerate(topk_i):
        ax  = axes[i]
        raw = cv2.imread(paths[si])
        img = cv2.cvtColor(raw, cv2.COLOR_BGR2RGB) if raw is not None \
              else np.zeros((IMG_SIZE, IMG_SIZE, 3), dtype=np.uint8)
        ax.imshow(img)
        gt   = IDX_TO_CLASS[labels[si]]
        pred = IDX_TO_CLASS[preds[si]]
        c    = probs[si][preds[si]]
        ax.set_title(f"GT:{gt}\nPred:{pred} ({c:.2f})",
                     fontsize=8, color="red")
        ax.axis("off")
    for ax in axes[len(topk_i):]:
        ax.axis("off")
    plt.tight_layout()
    plt.savefig(path, dpi=120, bbox_inches="tight")
    plt.close()
    print(f"  -> {path}")

# ── 차트: val vs test F1 ────────────────────────────────────
def plot_gap(res_val, res_test, out):
    common = sorted(set(res_val["names"]) & set(res_test["names"]))
    vf = dict(zip(res_val["names"],
                  f1_score(res_val["labels"], res_val["preds"],
                           labels=res_val["idxs"], average=None, zero_division=0)))
    tf = dict(zip(res_test["names"],
                  f1_score(res_test["labels"], res_test["preds"],
                           labels=res_test["idxs"], average=None, zero_division=0)))

    print(f"\n{'Class':<12} {'Val F1':>8} {'Test F1':>8} {'Gap':>8}")
    print("-"*42)
    for c in common:
        gap  = vf.get(c,0) - tf.get(c,0)
        flag = " <-- WARNING" if abs(gap) > 0.15 else ""
        print(f"{c:<12} {vf.get(c,0):>8.3f} {tf.get(c,0):>8.3f} {gap:>+8.3f}{flag}")
    print(f"\nAcc:  Val={res_val['acc']:.3f}  Test={res_test['acc']:.3f}  "
          f"Gap={res_val['acc']-res_test['acc']:+.3f}")

    x = np.arange(len(common)); w = 0.35
    fig, ax = plt.subplots(figsize=(max(8, len(common)*1.6), 5))
    ax.bar(x-w/2, [vf.get(c,0) for c in common], w, label="Val",  color="#0066CC", alpha=0.85)
    ax.bar(x+w/2, [tf.get(c,0) for c in common], w, label="Test", color="#FF6B35", alpha=0.85)
    for xi, c in enumerate(common):
        ax.text(xi-w/2, vf.get(c,0)+0.01, f"{vf.get(c,0):.2f}", ha="center", fontsize=9)
        ax.text(xi+w/2, tf.get(c,0)+0.01, f"{tf.get(c,0):.2f}", ha="center", fontsize=9)
    ax.set_xticks(x); ax.set_xticklabels(common, fontsize=11)
    ax.set_ylim(0, 1.1); ax.set_ylabel("F1 Score"); ax.legend()
    ax.set_title("Val vs Test F1 per Class", fontweight="bold")
    ax.axhline(0.8, color="gray", linestyle="--", linewidth=0.8, alpha=0.5)
    plt.tight_layout()
    p = f"{out}/val_test_gap.png"
    plt.savefig(p, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  -> {p}")

# ── 진단 요약 ───────────────────────────────────────────────
def print_diagnosis(res):
    labels = res["labels"]; preds = res["preds"]
    idxs   = res["idxs"];   names = res["names"]
    p_  = precision_score(labels, preds, labels=idxs, average=None, zero_division=0)
    r_  = recall_score(   labels, preds, labels=idxs, average=None, zero_division=0)
    f1_ = f1_score(       labels, preds, labels=idxs, average=None, zero_division=0)

    print(f"\n{'Class':<12} {'Precision':>10} {'Recall':>8} {'F1':>8} {'N':>6}")
    print("-"*48)
    cnt  = Counter(labels.tolist())
    weak = []
    for cls, pi, ri, fi in zip(names, p_, r_, f1_):
        idx  = CLASS_TO_IDX[cls]
        flag = " <-- WEAK" if fi < 0.75 else ""
        print(f"{cls:<12} {pi:>10.3f} {ri:>8.3f} {fi:>8.3f} {cnt.get(idx,0):>6}{flag}")
        if fi < 0.75:
            weak.append((cls, fi))

    print("\n[Diagnosis]")
    if weak:
        print(f"  Weak classes: {[c for c,_ in weak]}")
        # 어느 클래스로 오분류되는지
        cm_full = confusion_matrix(labels, preds, labels=list(range(5)))
        for cls, fi in weak:
            idx = CLASS_TO_IDX[cls]
            row = cm_full[idx]
            confused = [(IDX_TO_CLASS[j], int(row[j]))
                        for j in range(5) if j != idx and row[j] > 0]
            confused.sort(key=lambda x: -x[1])
            print(f"  [{cls}] F1={fi:.3f}  misclassified as -> {confused[:3]}")

    print(f"\n  [NOTE] confusion class: 0 samples in training")
    print(f"         -> decide: collect data OR reduce to 4-class")


# ════════════════════════════════════════════════════════════
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--affectnet", default=None)
    ap.add_argument("--rafdb",     default=None)
    ap.add_argument("--weights",   default="models/weights/emotion_model_v2.pth")
    ap.add_argument("--topk",      type=int, default=16)
    ap.add_argument("--out",       default="eval_results")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Device: {device}")

    # ── 1. 데이터 로드 (train_v2 와 동일 경로/시드) ──────────
    DATASET_DIR = Path("/home/llm-server/Desktop/beomjun/machine-vision-ut/dataset")
    ck = load_ckplus(DATASET_DIR)
    af = load_affectnet(Path(args.affectnet)) if args.affectnet else []

    ck_tr, ck_va, ck_te = split_ds(ck)
    tr = list(ck_tr); te = list(ck_te)
    if af:
        a_tr, _, a_te = split_ds(af)
        tr += a_tr; te += a_te
    va = list(ck_va)

    print(f"\nVal  : {len(va)}장")
    print(f"Test : {len(te)}장")

    for split_name, split_data in [("Val", va), ("Test", te)]:
        cnt = Counter(l for _, l in split_data)
        print(f"\n{split_name} 분포:")
        for i, c in enumerate(EMOTION_CLASSES):
            bar = "█" * (cnt.get(i, 0) // max(len(split_data) // 30, 1))
            print(f"  {c:<12}: {cnt.get(i,0):4d}  {bar}")

    # ── 2. 모델 로드 ──────────────────────────────────────────
    print(f"\n모델 로드: {args.weights}")
    model = EmotionModel().to(device)
    state = torch.load(args.weights, map_location=device, weights_only=True)
    model.load_state_dict(state, strict=True)
    print("  로드 성공 (strict=True)")
    model.eval()

    # ── 3. 추론 + 평가 ────────────────────────────────────────
    results = {}
    for split_name, ds_samples in [("val", va), ("test", te)]:
        print(f"\n{'='*50}")
        print(f"  {split_name.upper()} 평가  ({len(ds_samples)}장)")
        print("="*50)

        loader = DataLoader(
            EmotionDataset(ds_samples),
            batch_size=BATCH_SIZE, shuffle=False,
            num_workers=NUM_WORKERS, pin_memory=True,
        )
        preds, labels, probs, paths = run_inference(model, loader, device)

        present_idxs  = sorted(set(labels.tolist()))
        present_names = [IDX_TO_CLASS[i] for i in present_idxs]
        acc = (preds == labels).mean()

        print(f"\nAccuracy: {acc:.4f}")
        print(classification_report(
            labels, preds,
            labels=present_idxs, target_names=present_names,
            digits=3, zero_division=0
        ))

        results[split_name] = {
            "preds": preds, "labels": labels,
            "probs": probs, "paths":  paths,
            "idxs":  present_idxs, "names": present_names,
            "acc":   float(acc),
        }

        plot_cm(labels, preds, present_idxs, present_names,
                f"Confusion Matrix — {split_name.upper()}  (Acc={acc:.3f})",
                f"{args.out}/confusion_matrix_{split_name}.png")

        plot_hard(preds, labels, probs, paths,
                  f"Hard Mistakes — {split_name.upper()}",
                  f"{args.out}/hard_mistakes_{split_name}.png",
                  topk=args.topk)

    # ── 4. Val vs Test 갭 ─────────────────────────────────────
    if "val" in results and "test" in results:
        print(f"\n{'='*50}\n  VAL vs TEST GAP\n{'='*50}")
        plot_gap(results["val"], results["test"], args.out)

    # ── 5. 진단 요약 ──────────────────────────────────────────
    print(f"\n{'='*50}\n  DIAGNOSIS (Test set)\n{'='*50}")
    if "test" in results:
        print_diagnosis(results["test"])

    print(f"\n결과 저장: {args.out}/")
    print("  confusion_matrix_val.png / _test.png")
    print("  hard_mistakes_val.png   / _test.png")
    print("  val_test_gap.png")


if __name__ == "__main__":
    main()
