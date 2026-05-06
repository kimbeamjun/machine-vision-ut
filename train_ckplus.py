# train_ckplus.py
"""
dataset/
├── anger/      → negative
├── contempt/   → negative
├── disgust/    → negative
├── fear/       → negative
├── happy/      → positive
├── sadness/    → negative
└── surprise/   → surprise

실행:
  python train_ckplus.py
"""

import time
import json
import random
from pathlib import Path
from collections import Counter

import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import Dataset, DataLoader
from torchvision import transforms
import cv2
from tqdm import tqdm
import timm

# ── 경로 ──────────────────────────────────────────────────────────
BASE_DIR    = Path("/home/llm-server/Desktop/beomjun")
DATASET_DIR = BASE_DIR / "machine-vision-ut" / "dataset"
WEIGHTS_DIR = BASE_DIR / "models" / "weights"
WEIGHTS_DIR.mkdir(parents=True, exist_ok=True)
SAVE_PATH   = WEIGHTS_DIR / "emotion_model.pth"
LOG_PATH    = WEIGHTS_DIR / "train_log.json"

# ── 폴더 → 클래스 매핑 ────────────────────────────────────────────
# neutral / confusion 은 CK+에 없으므로 학습 클래스에서 제외
FOLDER_TO_CLASS = {
    "anger":    "negative",
    "contempt": "negative",
    "disgust":  "negative",
    "fear":     "negative",
    "sadness":  "negative",
    "happy":    "positive",
    "surprise": "surprise",
}

TRAIN_CLASSES = ["negative", "positive", "surprise"]
CLASS_TO_IDX  = {c: i for i, c in enumerate(TRAIN_CLASSES)}

# ── 하이퍼파라미터 ────────────────────────────────────────────────
IMG_SIZE      = 260
BATCH_SIZE    = 32
NUM_WORKERS   = 4
PHASE1_EPOCHS = 15
PHASE2_EPOCHS = 25
LR_PHASE1     = 3e-4
LR_PHASE2     = 5e-5
WEIGHT_DECAY  = 1e-4
VAL_RATIO     = 0.15
TEST_RATIO    = 0.15
SEED          = 42


# ── Dataset ───────────────────────────────────────────────────────
class CKDataset(Dataset):
    def __init__(self, samples: list, augment: bool = False):
        self.samples = samples
        self.augment = augment

    def _build_transform(self):
        ops = [transforms.ToPILImage(), transforms.Resize((IMG_SIZE, IMG_SIZE))]
        if self.augment:
            ops += [
                transforms.RandomHorizontalFlip(p=0.5),
                transforms.RandomRotation(degrees=12),
                transforms.ColorJitter(brightness=0.3, contrast=0.3, saturation=0.15),
                transforms.RandomAffine(degrees=0, translate=(0.06, 0.06)),
            ]
        ops += [
            transforms.ToTensor(),
            transforms.Normalize(mean=[0.485, 0.456, 0.406],
                                 std =[0.229, 0.224, 0.225]),
        ]
        return transforms.Compose(ops)

    def __len__(self):
        return len(self.samples)

    def __getitem__(self, idx):
        img_path, label = self.samples[idx]
        img_bgr = cv2.imread(str(img_path))
        if img_bgr is None:
            img_rgb = np.zeros((IMG_SIZE, IMG_SIZE, 3), dtype=np.uint8)
        else:
            img_rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
        tensor = self._build_transform()(img_rgb)
        return tensor, label


# ── 모델 (EfficientNet-B2 + 분류 헤드) ───────────────────────────
class EmotionModel(nn.Module):
    def __init__(self, num_classes: int, dropout: float = 0.4):
        super().__init__()
        self.backbone = timm.create_model(
            "efficientnet_b2", pretrained=True, num_classes=0, global_pool="avg"
        )
        self.head = nn.Sequential(
            nn.LayerNorm(1408),
            nn.Dropout(dropout),
            nn.Linear(1408, 512),
            nn.GELU(),
            nn.Dropout(dropout * 0.5),
            nn.Linear(512, num_classes),
        )

    def forward(self, x):
        return self.head(self.backbone(x))


# ── 데이터 로드 및 분할 ──────────────────────────────────────────
def load_and_split():
    all_samples = []
    print("=== 데이터셋 구성 ===")
    for folder in sorted(DATASET_DIR.iterdir()):
        if not folder.is_dir():
            continue
        class_name = FOLDER_TO_CLASS.get(folder.name.lower())
        if class_name is None:
            continue
        imgs = sorted(folder.glob("*.png")) + sorted(folder.glob("*.jpg"))
        for p in imgs:
            all_samples.append((p, CLASS_TO_IDX[class_name]))
        print(f"  {folder.name:12s} → {class_name:10s}: {len(imgs)}장")

    cnt = Counter(label for _, label in all_samples)
    print("\n클래스별 샘플 수:")
    for i, cls in enumerate(TRAIN_CLASSES):
        print(f"  {cls:12s}: {cnt.get(i, 0)}장")
    print(f"  합계: {len(all_samples)}장")

    if len(all_samples) == 0:
        raise FileNotFoundError(f"이미지 없음: {DATASET_DIR}")

    random.seed(SEED)
    random.shuffle(all_samples)
    n      = len(all_samples)
    n_test = int(n * TEST_RATIO)
    n_val  = int(n * VAL_RATIO)

    test  = all_samples[:n_test]
    val   = all_samples[n_test:n_test + n_val]
    train = all_samples[n_test + n_val:]
    print(f"\n분할: train={len(train)} / val={len(val)} / test={len(test)}\n")
    return train, val, test


# ── 클래스 가중치 (실제 존재 클래스만, 정규화) ───────────────────
def class_weights_tensor(samples, device):
    cnt   = Counter(label for _, label in samples)
    total = len(samples)
    weights = []
    for i in range(len(TRAIN_CLASSES)):
        n = cnt.get(i, 0)
        weights.append(total / n if n > 0 else 0.0)
    max_w = max(w for w in weights if w > 0) or 1.0
    weights = [w / max_w for w in weights]
    print(f"클래스 가중치: { {TRAIN_CLASSES[i]: round(w,3) for i,w in enumerate(weights)} }")
    return torch.tensor(weights, dtype=torch.float32).to(device)


# ── 학습 / 평가 ──────────────────────────────────────────────────
def train_one_epoch(model, loader, criterion, optimizer, device, scaler):
    model.train()
    total_loss = correct = total = 0
    for imgs, labels in tqdm(loader, desc="  train", leave=False):
        imgs, labels = imgs.to(device), labels.to(device)
        optimizer.zero_grad()
        with torch.amp.autocast("cuda"):
            logits = model(imgs)
            loss   = criterion(logits, labels)
        scaler.scale(loss).backward()
        scaler.unscale_(optimizer)
        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        scaler.step(optimizer)
        scaler.update()
        total_loss += loss.item() * labels.size(0)
        correct    += (logits.argmax(1) == labels).sum().item()
        total      += labels.size(0)
    return total_loss / total, correct / total


@torch.no_grad()
def evaluate(model, loader, criterion, device):
    model.eval()
    total_loss = correct = total = 0
    all_p, all_l = [], []
    for imgs, labels in tqdm(loader, desc="  eval ", leave=False):
        imgs, labels = imgs.to(device), labels.to(device)
        with torch.amp.autocast("cuda"):
            logits = model(imgs)
            loss   = criterion(logits, labels)
        total_loss += loss.item() * labels.size(0)
        preds = logits.argmax(1)
        correct += (preds == labels).sum().item()
        total   += labels.size(0)
        all_p.extend(preds.cpu().tolist())
        all_l.extend(labels.cpu().tolist())

    from collections import defaultdict
    cc, ct = defaultdict(int), defaultdict(int)
    for p, l in zip(all_p, all_l):
        ct[l] += 1
        cc[l] += int(p == l)
    per_cls = {TRAIN_CLASSES[k]: round(cc[k]/ct[k], 3) for k in ct}
    return total_loss / total, correct / total, per_cls


# ── 메인 ─────────────────────────────────────────────────────────
def main():
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"\nDevice : {device}")
    if device.type == "cuda":
        print(f"GPU    : {torch.cuda.get_device_name(0)}")
        print(f"VRAM   : {torch.cuda.get_device_properties(0).total_memory/1e9:.1f} GB\n")

    train_s, val_s, test_s = load_and_split()

    train_ds = CKDataset(train_s, augment=True)
    val_ds   = CKDataset(val_s,   augment=False)
    test_ds  = CKDataset(test_s,  augment=False)

    train_loader = DataLoader(train_ds, batch_size=BATCH_SIZE, shuffle=True,
                              num_workers=NUM_WORKERS, pin_memory=True)
    val_loader   = DataLoader(val_ds,   batch_size=BATCH_SIZE, shuffle=False,
                              num_workers=NUM_WORKERS, pin_memory=True)
    test_loader  = DataLoader(test_ds,  batch_size=BATCH_SIZE, shuffle=False,
                              num_workers=NUM_WORKERS, pin_memory=True)

    model     = EmotionModel(num_classes=len(TRAIN_CLASSES)).to(device)
    cw        = class_weights_tensor(train_s, device)
    criterion = nn.CrossEntropyLoss(weight=cw, label_smoothing=0.1)
    scaler    = torch.amp.GradScaler("cuda")

    best_val_acc = 0.0
    log = []

    # ══ Phase 1: 백본 freeze ════════════════════════════════════
    print("\n" + "━"*60)
    print(f"Phase 1: 백본 freeze  ({PHASE1_EPOCHS} epoch, lr={LR_PHASE1})")
    print("━"*60)

    for p in model.backbone.parameters():
        p.requires_grad = False

    optimizer = optim.AdamW(model.head.parameters(), lr=LR_PHASE1, weight_decay=WEIGHT_DECAY)
    scheduler = optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=PHASE1_EPOCHS, eta_min=1e-5)

    for epoch in range(1, PHASE1_EPOCHS + 1):
        t0 = time.time()
        tr_loss, tr_acc            = train_one_epoch(model, train_loader, criterion, optimizer, device, scaler)
        val_loss, val_acc, per_cls = evaluate(model, val_loader, criterion, device)
        scheduler.step()
        print(f"[P1 {epoch:02d}/{PHASE1_EPOCHS}] "
              f"train {tr_loss:.4f}/{tr_acc:.3f}  "
              f"val {val_loss:.4f}/{val_acc:.3f}  ({time.time()-t0:.0f}s)")
        print(f"         {per_cls}")
        log.append({"phase":1,"epoch":epoch,"tr_loss":tr_loss,"tr_acc":tr_acc,
                    "val_loss":val_loss,"val_acc":val_acc})
        if val_acc > best_val_acc:
            best_val_acc = val_acc
            torch.save(model.state_dict(), SAVE_PATH)
            print(f"         ✓ 저장 (val_acc={val_acc:.3f})")

    # ══ Phase 2: 전체 fine-tuning ═══════════════════════════════
    print("\n" + "━"*60)
    print(f"Phase 2: 전체 fine-tuning  ({PHASE2_EPOCHS} epoch, lr={LR_PHASE2})")
    print("━"*60)

    for p in model.backbone.parameters():
        p.requires_grad = True

    optimizer = optim.AdamW([
        {"params": model.backbone.parameters(), "lr": LR_PHASE2 * 0.1},
        {"params": model.head.parameters(),     "lr": LR_PHASE2},
    ], weight_decay=WEIGHT_DECAY)
    scheduler = optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=PHASE2_EPOCHS, eta_min=1e-6)

    for epoch in range(1, PHASE2_EPOCHS + 1):
        t0 = time.time()
        tr_loss, tr_acc            = train_one_epoch(model, train_loader, criterion, optimizer, device, scaler)
        val_loss, val_acc, per_cls = evaluate(model, val_loader, criterion, device)
        scheduler.step()
        print(f"[P2 {epoch:02d}/{PHASE2_EPOCHS}] "
              f"train {tr_loss:.4f}/{tr_acc:.3f}  "
              f"val {val_loss:.4f}/{val_acc:.3f}  ({time.time()-t0:.0f}s)")
        print(f"         {per_cls}")
        log.append({"phase":2,"epoch":epoch,"tr_loss":tr_loss,"tr_acc":tr_acc,
                    "val_loss":val_loss,"val_acc":val_acc})
        if val_acc > best_val_acc:
            best_val_acc = val_acc
            torch.save(model.state_dict(), SAVE_PATH)
            print(f"         ✓ 저장 (val_acc={val_acc:.3f})")

    # ══ 최종 Test 평가 ══════════════════════════════════════════
    print("\n" + "━"*60)
    print("최종 Test 평가")
    print("━"*60)
    if SAVE_PATH.exists():
        model.load_state_dict(torch.load(SAVE_PATH, map_location=device, weights_only=True))
        test_loss, test_acc, per_cls = evaluate(model, test_loader, criterion, device)
        print(f"Test Loss={test_loss:.4f}  Test Acc={test_acc:.3f}")
        print(f"Per-class: {per_cls}")
    else:
        print("⚠ 저장된 가중치 없음")

    LOG_PATH.write_text(json.dumps(log, indent=2, ensure_ascii=False))
    print(f"\n✅ 학습 완료  |  Best val_acc: {best_val_acc:.3f}")
    print(f"   가중치: {SAVE_PATH}")


if __name__ == "__main__":
    main()
