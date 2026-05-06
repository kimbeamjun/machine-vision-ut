# ai_server/training/evaluate_model.py
"""
학습 완료 후 모델 평가 스크립트
- 혼동 행렬 출력
- 학습 곡선 시각화
- 각 클래스별 정밀도/재현율/F1

실행:
  python training/evaluate_model.py
"""

import sys
import json
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F
from torch.utils.data import DataLoader
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from sklearn.metrics import classification_report, confusion_matrix
import seaborn as sns

sys.path.insert(0, str(Path(__file__).parent.parent))

from models.emotion_model import EmotionModel, EMOTION_CLASSES, load_model
from training.train_ckplus import CKPlusDataset

DATA_DIR    = Path("data/ckplus_processed")
WEIGHTS_DIR = Path("models/weights")
SAVE_PATH   = WEIGHTS_DIR / "emotion_model.pth"
LOG_PATH    = WEIGHTS_DIR / "train_log.json"
REPORT_DIR  = WEIGHTS_DIR / "eval_report"
REPORT_DIR.mkdir(parents=True, exist_ok=True)


@torch.no_grad()
def collect_predictions(model, loader, device):
    model.eval()
    all_preds, all_labels, all_probs = [], [], []

    for imgs, labels in loader:
        imgs   = imgs.to(device)
        logits = model(imgs)[:, 0, :]
        probs  = F.softmax(logits, dim=1).cpu().numpy()
        preds  = logits.argmax(dim=1).cpu().numpy()

        all_preds.extend(preds.tolist())
        all_labels.extend(labels.tolist())
        all_probs.extend(probs.tolist())

    return np.array(all_preds), np.array(all_labels), np.array(all_probs)


def plot_confusion_matrix(labels, preds, save_path):
    cm = confusion_matrix(labels, preds)
    cm_norm = cm.astype(float) / cm.sum(axis=1, keepdims=True)

    fig, axes = plt.subplots(1, 2, figsize=(16, 6))

    # 절대값
    sns.heatmap(cm, annot=True, fmt="d", cmap="Blues",
                xticklabels=EMOTION_CLASSES, yticklabels=EMOTION_CLASSES, ax=axes[0])
    axes[0].set_title("Confusion Matrix (count)")
    axes[0].set_xlabel("Predicted")
    axes[0].set_ylabel("True")

    # 비율
    sns.heatmap(cm_norm, annot=True, fmt=".2f", cmap="Blues",
                xticklabels=EMOTION_CLASSES, yticklabels=EMOTION_CLASSES, ax=axes[1])
    axes[1].set_title("Confusion Matrix (normalized)")
    axes[1].set_xlabel("Predicted")
    axes[1].set_ylabel("True")

    plt.tight_layout()
    fig.savefig(save_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"혼동 행렬 저장: {save_path}")


def plot_training_curve(log_path, save_path):
    if not log_path.exists():
        print("학습 로그 없음")
        return

    log = json.loads(log_path.read_text())
    train_accs = [e["train_acc"] for e in log]
    val_accs   = [e["val_acc"]   for e in log]
    train_loss = [e["train_loss"] for e in log]
    val_loss   = [e["val_loss"]   for e in log]
    epochs     = list(range(1, len(log) + 1))

    fig, axes = plt.subplots(1, 2, figsize=(14, 5))

    axes[0].plot(epochs, train_accs, label="Train Acc", marker="o", markersize=3)
    axes[0].plot(epochs, val_accs,   label="Val Acc",   marker="o", markersize=3)
    axes[0].set_title("Accuracy")
    axes[0].set_xlabel("Epoch")
    axes[0].set_ylabel("Accuracy")
    axes[0].legend()
    axes[0].grid(True, alpha=0.3)

    axes[1].plot(epochs, train_loss, label="Train Loss", marker="o", markersize=3)
    axes[1].plot(epochs, val_loss,   label="Val Loss",   marker="o", markersize=3)
    axes[1].set_title("Loss")
    axes[1].set_xlabel("Epoch")
    axes[1].set_ylabel("Loss")
    axes[1].legend()
    axes[1].grid(True, alpha=0.3)

    # Phase 경계선
    phase2_start = next((e["epoch"] for e in log if e["phase"] == 2), None)
    if phase2_start:
        for ax in axes:
            ax.axvline(x=phase2_start, color="red", linestyle="--", alpha=0.5, label="Phase2 시작")

    plt.tight_layout()
    fig.savefig(save_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"학습 곡선 저장: {save_path}")


def main():
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Device: {device}")

    # 모델 로드
    model = load_model(str(SAVE_PATH), device)

    # Test 데이터셋
    test_ds     = CKPlusDataset(DATA_DIR, "test", augment=False)
    test_loader = DataLoader(test_ds, batch_size=32, shuffle=False, num_workers=4)

    # 예측 수집
    preds, labels, probs = collect_predictions(model, test_loader, device)

    # 분류 리포트
    report = classification_report(
        labels, preds,
        target_names=EMOTION_CLASSES,
        digits=3,
    )
    print("\n=== Classification Report ===")
    print(report)
    (REPORT_DIR / "classification_report.txt").write_text(report)

    # 혼동 행렬
    plot_confusion_matrix(
        labels, preds,
        save_path=REPORT_DIR / "confusion_matrix.png",
    )

    # 학습 곡선
    plot_training_curve(
        log_path=LOG_PATH,
        save_path=REPORT_DIR / "training_curve.png",
    )

    # 전체 정확도
    acc = (preds == labels).mean()
    print(f"\nTest Accuracy: {acc:.3f}")
    print(f"평가 결과 저장: {REPORT_DIR}/")


if __name__ == "__main__":
    main()
