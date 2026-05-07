# train_v3.py
#
# v2 대비 변경점:
#   1. Stratified val split  — 클래스별 균등 비율, neutral 반드시 포함
#   2. 클래스 가중치 재조정  — negative 상향, surprise 하향
#   3. Threshold calibration — val 기준으로 클래스별 최적 threshold 탐색
#   4. 4-class 운영          — confusion 클래스 완전 제거 (데이터 0장)
#   5. AffectNet val 포함    — val 셋에도 AffectNet 샘플 반영
#
# 실행:
#   python train_v3.py --affectnet ./dataset
#
import os, sys, time, json, random, argparse
import numpy as np
from pathlib import Path
from collections import Counter, defaultdict

import torch, torch.nn as nn, torch.optim as optim
from torch.utils.data import Dataset, DataLoader, WeightedRandomSampler
from torchvision import transforms
import cv2, timm
from tqdm import tqdm
from sklearn.metrics import f1_score

# ── 경로 ────────────────────────────────────────────────────
BASE_DIR    = Path("/home/llm-server/Desktop/beomjun")
DATASET_DIR = BASE_DIR / "machine-vision-ut" / "dataset"
WEIGHTS_DIR = BASE_DIR / "machine-vision-ut" / "models" / "weights"
WEIGHTS_DIR.mkdir(parents=True, exist_ok=True)
SAVE_PATH   = WEIGHTS_DIR / "emotion_model_v3.pth"
LOG_PATH    = WEIGHTS_DIR / "train_log_v3.json"
V2_PATH     = WEIGHTS_DIR / "emotion_model_v2.pth"   # v2 백본 재사용

# ── 클래스 정의 (confusion 제거 → 4-class) ──────────────────
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
# v3: confusion 제거, 4-class
EMOTION_CLASSES = ["neutral", "negative", "positive", "surprise"]
CLASS_TO_IDX    = {c: i for i, c in enumerate(EMOTION_CLASSES)}
IDX_TO_CLASS    = {i: c for i, c in enumerate(EMOTION_CLASSES)}
NUM_CLASSES     = 4

# ── 하이퍼파라미터 ──────────────────────────────────────────
IMG_SIZE        = 260
BATCH_SIZE      = 16
NUM_WORKERS     = 2
PHASE1_EPOCHS   = 10
PHASE2_EPOCHS   = 30
LR_PHASE1       = 5e-4
LR_PHASE2       = 5e-5
WEIGHT_DECAY    = 1e-4
LABEL_SMOOTHING = 0.05          # v2(0.1) → 약하게
DROPOUT         = 0.30          # v2(0.35) → 약하게
TEST_RATIO      = 0.15
SEED            = 42
FEAT_DIM        = 1408
MEAN = [0.485, 0.456, 0.406]
STD  = [0.229, 0.224, 0.225]

# ── Val 크기: 클래스당 최소 50장 보장 ──────────────────────
VAL_PER_CLASS   = 60            # 클래스당 val 샘플 수


# ════════════════════════════════════════════════════════════
# 데이터
# ════════════════════════════════════════════════════════════
def _load_dir(base: Path) -> list:
    samples = []
    if not base.exists():
        return samples
    for d in sorted(base.iterdir()):
        if not d.is_dir():
            continue
        cls = FOLDER_TO_CLASS.get(d.name) or FOLDER_TO_CLASS.get(d.name.lower())
        if cls is None or cls not in CLASS_TO_IDX:   # confusion 자동 제외
            continue
        idx = CLASS_TO_IDX[cls]
        for p in list(d.glob("*.png")) + list(d.glob("*.jpg")) + list(d.glob("*.jpeg")):
            samples.append((p, idx))
    return samples

def load_ckplus(d: Path) -> list:
    s = _load_dir(d)
    print(f"[CK+] {len(s)}장")
    return s

def load_affectnet(d: Path) -> list:
    s = []
    for split in ["Train","train","Test","test","val","Val"]:
        s += _load_dir(d / split)
    if not s:
        s = _load_dir(d)
    print(f"[AffectNet] {len(s)}장")
    return s


def stratified_split(samples, val_per_class=VAL_PER_CLASS, test_ratio=TEST_RATIO, seed=SEED):
    """
    1. 클래스별로 나눔
    2. Test: 클래스별 test_ratio 비율
    3. Val : 클래스별 val_per_class 장 (test 제외 후)
    4. Train: 나머지
    """
    random.seed(seed)
    by_class = defaultdict(list)
    for s in samples:
        by_class[s[1]].append(s)
    for lst in by_class.values():
        random.shuffle(lst)

    train, val, test = [], [], []
    for cls_idx, lst in by_class.items():
        n_test = max(1, int(len(lst) * test_ratio))
        n_val  = min(val_per_class, len(lst) - n_test)
        test  += lst[:n_test]
        val   += lst[n_test : n_test + n_val]
        train += lst[n_test + n_val :]

    random.shuffle(train)
    return train, val, test


def print_dist(name, samples):
    cnt = Counter(l for _, l in samples)
    print(f"\n{name} ({len(samples)}장):")
    for i, c in enumerate(EMOTION_CLASSES):
        bar = "█" * (cnt.get(i, 0) // max(len(samples) // 40, 1))
        print(f"  {c:<12}: {cnt.get(i,0):5d}  {bar}")


# ════════════════════════════════════════════════════════════
# Dataset & Augmentation
# ════════════════════════════════════════════════════════════
class EmotionDataset(Dataset):
    def __init__(self, samples, augment=False):
        self.samples = samples
        self.augment = augment

    def _tf(self):
        pil = [transforms.ToPILImage(),
               transforms.Resize((IMG_SIZE + 20, IMG_SIZE + 20))]
        if self.augment:
            pil += [
                transforms.RandomCrop((IMG_SIZE, IMG_SIZE)),
                transforms.RandomHorizontalFlip(0.5),
                transforms.RandomRotation(15),
                transforms.ColorJitter(brightness=0.35, contrast=0.35,
                                       saturation=0.15, hue=0.08),
                transforms.RandomAffine(degrees=0, translate=(0.07,0.07), shear=5),
                transforms.RandomGrayscale(p=0.1),
            ]
        else:
            pil += [transforms.CenterCrop((IMG_SIZE, IMG_SIZE))]
        ten = [transforms.ToTensor(), transforms.Normalize(MEAN, STD)]
        if self.augment:
            ten += [transforms.RandomErasing(p=0.25, scale=(0.02, 0.12), value=0)]
        return transforms.Compose(pil + ten)

    def __len__(self): return len(self.samples)

    def __getitem__(self, idx):
        p, label = self.samples[idx]
        img = cv2.imread(str(p))
        img = np.zeros((IMG_SIZE, IMG_SIZE, 3), dtype=np.uint8) if img is None \
              else cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        return self._tf()(img).unsqueeze(0), label


def make_sampler(samples):
    labels = [l for _, l in samples]
    cnt    = Counter(labels)
    w      = {c: 1.0 / n for c, n in cnt.items()}
    total  = sum(w.values())
    w      = {c: v / total for c, v in w.items()}
    return WeightedRandomSampler([w[l] for l in labels], len(samples), replacement=True)


# ════════════════════════════════════════════════════════════
# 모델
# ════════════════════════════════════════════════════════════
class EmotionModel(nn.Module):
    def __init__(self, n=NUM_CLASSES, drop=DROPOUT):
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


# ════════════════════════════════════════════════════════════
# Train / Eval
# ════════════════════════════════════════════════════════════
def train_ep(model, loader, crit, opt, dev, scaler):
    model.train()
    ls = cr = tot = 0
    for imgs, labels in tqdm(loader, desc="  train", leave=False):
        imgs, labels = imgs.to(dev), labels.to(dev)
        opt.zero_grad()
        with torch.amp.autocast("cuda"):
            logits = model(imgs)[:, 0, :]
            loss   = crit(logits, labels)
        scaler.scale(loss).backward()
        scaler.unscale_(opt)
        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        scaler.step(opt); scaler.update()
        ls  += loss.item() * labels.size(0)
        cr  += (logits.argmax(1) == labels).sum().item()
        tot += labels.size(0)
    return ls / tot, cr / tot


@torch.no_grad()
def eval_ep(model, loader, crit, dev):
    model.eval()
    ls = cr = tot = 0
    pa, la = [], []
    for imgs, labels in tqdm(loader, desc="  eval ", leave=False):
        imgs, labels = imgs.to(dev), labels.to(dev)
        with torch.amp.autocast("cuda"):
            logits = model(imgs)[:, 0, :]
            loss   = crit(logits, labels)
        ls  += loss.item() * labels.size(0)
        p    = logits.argmax(1)
        cr  += (p == labels).sum().item()
        tot += labels.size(0)
        pa.extend(p.cpu().tolist())
        la.extend(labels.cpu().tolist())
    # per-class accuracy
    cc, ct = defaultdict(int), defaultdict(int)
    for p, l in zip(pa, la):
        ct[l] += 1; cc[l] += int(p == l)
    pc = {EMOTION_CLASSES[k]: round(cc[k]/ct[k],3) for k in ct}
    # macro F1 (저장 기준을 acc 대신 macro-F1로 변경)
    macro_f1 = f1_score(la, pa, average="macro", zero_division=0)
    return ls/tot, cr/tot, pc, macro_f1


# ════════════════════════════════════════════════════════════
# Threshold Calibration
# ════════════════════════════════════════════════════════════
@torch.no_grad()
def get_probs(model, loader, dev):
    model.eval()
    all_probs, all_labels = [], []
    for imgs, labels in loader:
        imgs = imgs.to(dev)
        with torch.amp.autocast("cuda"):
            logits = model(imgs)[:, 0, :]
        probs = torch.softmax(logits, dim=1).cpu().numpy()
        all_probs.extend(probs)
        all_labels.extend(labels.numpy())
    return np.array(all_probs), np.array(all_labels)


def calibrate_thresholds(probs, labels, n_classes=NUM_CLASSES):
    """
    클래스별 threshold를 탐색해서 macro-F1 최대화.
    각 샘플에 대해 softmax prob > threshold 인 클래스 중 argmax 선택.
    threshold 미달 시 원래 argmax 유지.
    """
    best_thresh = np.ones(n_classes) * 0.5
    best_f1     = f1_score(labels, probs.argmax(axis=1),
                           average="macro", zero_division=0)

    thresholds = np.arange(0.1, 0.95, 0.05)

    for cls_idx in range(n_classes):
        best_t = best_thresh[cls_idx]
        for t in thresholds:
            tmp_thresh         = best_thresh.copy()
            tmp_thresh[cls_idx] = t
            preds = apply_threshold(probs, tmp_thresh)
            f1    = f1_score(labels, preds, average="macro", zero_division=0)
            if f1 > best_f1:
                best_f1             = f1
                best_t              = t
        best_thresh[cls_idx] = best_t

    print(f"\n[Threshold Calibration]")
    for i, (cls, t) in enumerate(zip(EMOTION_CLASSES, best_thresh)):
        print(f"  {cls:<12}: {t:.2f}")
    print(f"  Calibrated macro-F1 on val: {best_f1:.4f}")
    return best_thresh


def apply_threshold(probs, thresholds):
    """threshold 조정 후 예측"""
    thresholds = np.clip(thresholds, 0.05, 1.0)   # 0 나눗셈 방지
    adjusted   = probs / thresholds
    return adjusted.argmax(axis=1)



# ════════════════════════════════════════════════════════════
# emotion_model.py 자동 업데이트
# ════════════════════════════════════════════════════════════
def _update_emotion_model_py(path: Path):
    """
    학습 완료 후 emotion_model.py를 v3 4-class 구조에 맞게 자동 업데이트
    TRAINED_CLASSES, NUM_TRAINED, head 출력 크기 변경
    """
    if not path.exists():
        print(f"[WARN] emotion_model.py 없음: {path}")
        return

    content = path.read_text()

    # TRAINED_CLASSES 업데이트
    import re
    content = re.sub(
        r'TRAINED_CLASSES\s*=\s*\[.*?\]',
        'TRAINED_CLASSES = ["neutral", "negative", "positive", "surprise"]  # v3: 4-class',
        content
    )
    # NUM_TRAINED 업데이트
    content = re.sub(
        r'NUM_TRAINED\s*=\s*\d+',
        'NUM_TRAINED     = 4  # v3',
        content
    )
    # head Linear 출력 크기 업데이트 (3 → 4)
    content = re.sub(
        r'(nn\.Linear\(512,\s*)\d+(\))',
        r'\g<1>4\g<2>',
        content
    )
    # predict_emotion threshold 기준 업데이트
    content = re.sub(
        r'thresholds_v\d+\.json',
        'thresholds_v3.json',
        content
    )

    path.write_text(content)
    print(f"[OK] emotion_model.py 4-class로 업데이트: {path}")


# ════════════════════════════════════════════════════════════
# Main
# ════════════════════════════════════════════════════════════
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--affectnet", default=None)
    ap.add_argument("--rafdb",     default=None)
    args = ap.parse_args()

    dev = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"\nDevice: {dev}")
    if dev.type == "cuda":
        tot  = torch.cuda.get_device_properties(0).total_memory / 1e9
        used = torch.cuda.memory_reserved(0) / 1e9
        print(f"GPU: {torch.cuda.get_device_name(0)}")
        print(f"VRAM: {tot:.1f}GB  (여유: {tot-used:.1f}GB)")

    # ── 데이터 로드 ──────────────────────────────────────────
    ck = load_ckplus(DATASET_DIR)
    af = load_affectnet(Path(args.affectnet)) if args.affectnet else []
    all_samples = ck + af
    print(f"합계: {len(all_samples)}장  (CK+={len(ck)}, AffectNet={len(af)})")

    # ── Stratified split ─────────────────────────────────────
    tr, va, te = stratified_split(all_samples, VAL_PER_CLASS, TEST_RATIO, SEED)
    print_dist("Train", tr)
    print_dist("Val",   va)
    print_dist("Test",  te)

    # Val 분포 검증
    val_cnt = Counter(l for _, l in va)
    missing = [EMOTION_CLASSES[i] for i in range(NUM_CLASSES) if val_cnt.get(i,0) == 0]
    if missing:
        print(f"\n[WARN] Val에 없는 클래스: {missing}")
    else:
        print(f"\n[OK] Val 모든 클래스 포함")

    tr_ld = DataLoader(EmotionDataset(tr, True),  BATCH_SIZE,
                       sampler=make_sampler(tr), num_workers=NUM_WORKERS, pin_memory=True)
    va_ld = DataLoader(EmotionDataset(va, False), BATCH_SIZE,
                       shuffle=False, num_workers=NUM_WORKERS, pin_memory=True)
    te_ld = DataLoader(EmotionDataset(te, False), BATCH_SIZE,
                       shuffle=False, num_workers=NUM_WORKERS, pin_memory=True)

    # ── 모델 초기화 ──────────────────────────────────────────
    model = EmotionModel()
    # v2 → v1 → ImageNet pretrained 순서로 fallback
    V1_PATH = WEIGHTS_DIR / "emotion_model.pth"
    loaded = False
    for path, label in [(V2_PATH, "v2"), (V1_PATH, "v1")]:
        if path.exists():
            st = torch.load(path, map_location=dev, weights_only=True)
            bb = {k: v for k, v in st.items() if k.startswith("backbone.")}
            model.load_state_dict(bb, strict=False)
            print(f"\n✓ {label} 백본 로드 ({len(bb)}레이어) — head 새로 학습 (4-class)")
            loaded = True
            break
    if not loaded:
        bb = timm.create_model("efficientnet_b2", pretrained=True,
                               num_classes=0, global_pool="avg")
        model.backbone.load_state_dict(bb.state_dict())
        print("\n✓ ImageNet pretrained 백본")
    model.to(dev)

    # ── 클래스 가중치 (v3 조정) ───────────────────────────────
    # v2 문제: negative Recall 낮음 → 가중치 올림
    #          surprise Precision 낮음 → 가중치 낮춤
    cnt_tr = Counter(l for _, l in tr)
    print(f"\n학습 클래스 분포: {dict(cnt_tr)}")

    # 역수 기반 기본 가중치
    cw = torch.zeros(NUM_CLASSES)
    for i in range(NUM_CLASSES):
        n = cnt_tr.get(i, 0)
        cw[i] = 1.0 / n if n > 0 else 0.0
    cw = cw / cw.sum()

    # 수동 조정: negative 1.5x 상향, surprise 0.7x 하향
    neg_idx = CLASS_TO_IDX["negative"]
    sur_idx = CLASS_TO_IDX["surprise"]
    cw[neg_idx] *= 1.5
    cw[sur_idx] *= 0.7
    cw = cw / cw.sum()   # 재정규화
    cw = cw.to(dev)

    print(f"클래스 가중치 (수동 조정 후):")
    for i, c in enumerate(EMOTION_CLASSES):
        print(f"  {c:<12}: {cw[i].item():.4f}")

    crit   = nn.CrossEntropyLoss(weight=cw, label_smoothing=LABEL_SMOOTHING)
    scaler = torch.amp.GradScaler("cuda")
    best_f1     = 0.0
    best_acc    = 0.0
    log         = []

    print(f"\n증강: RandomCrop+Flip+Rotation+ColorJitter+Affine+Grayscale+RandomErasing")
    print(f"LabelSmoothing={LABEL_SMOOTHING} | Dropout={DROPOUT} | Batch={BATCH_SIZE}")
    print(f"저장 기준: macro-F1 (v2는 accuracy 기준이었음)")

    # ── Phase 1: 백본 freeze ─────────────────────────────────
    print(f"\n{'━'*60}")
    print(f"Phase 1: 백본 freeze ({PHASE1_EPOCHS}ep, lr={LR_PHASE1})")
    print("━"*60)
    for p in model.backbone.parameters():
        p.requires_grad = False
    opt = optim.AdamW(filter(lambda p: p.requires_grad, model.parameters()),
                      lr=LR_PHASE1, weight_decay=WEIGHT_DECAY)
    sch = optim.lr_scheduler.CosineAnnealingWarmRestarts(opt, T_0=4, eta_min=1e-6)

    for ep in range(1, PHASE1_EPOCHS + 1):
        t0 = time.time()
        tl, ta = train_ep(model, tr_ld, crit, opt, dev, scaler)
        vl, va_acc, pc, vf1 = eval_ep(model, va_ld, crit, dev)
        sch.step()
        print(f"[P1 {ep:02d}/{PHASE1_EPOCHS}] "
              f"train {tl:.4f}/{ta:.3f}  "
              f"val {vl:.4f}/{va_acc:.3f}  macro-F1={vf1:.3f}  ({time.time()-t0:.0f}s)")
        print(f"         {pc}")
        log.append({"phase":1,"ep":ep,"tr_acc":ta,"val_acc":va_acc,"val_f1":vf1})
        if vf1 > best_f1:
            best_f1 = vf1; best_acc = va_acc
            torch.save(model.state_dict(), SAVE_PATH)
            print(f"         ✓ 저장  macro-F1={vf1:.3f}  acc={va_acc:.3f}")

    # ── Phase 2: 전체 fine-tuning ────────────────────────────
    print(f"\n{'━'*60}")
    print(f"Phase 2: 전체 fine-tuning ({PHASE2_EPOCHS}ep, lr={LR_PHASE2})")
    print("━"*60)
    for p in model.backbone.parameters():
        p.requires_grad = True
    opt = optim.AdamW([
        {"params": model.backbone.parameters(), "lr": LR_PHASE2 * 0.1},
        {"params": model.head.parameters(),     "lr": LR_PHASE2},
    ], weight_decay=WEIGHT_DECAY)
    sch = optim.lr_scheduler.CosineAnnealingWarmRestarts(opt, T_0=10, eta_min=1e-7)

    for ep in range(1, PHASE2_EPOCHS + 1):
        t0 = time.time()
        tl, ta = train_ep(model, tr_ld, crit, opt, dev, scaler)
        vl, va_acc, pc, vf1 = eval_ep(model, va_ld, crit, dev)
        sch.step()
        print(f"[P2 {ep:02d}/{PHASE2_EPOCHS}] "
              f"train {tl:.4f}/{ta:.3f}  "
              f"val {vl:.4f}/{va_acc:.3f}  macro-F1={vf1:.3f}  ({time.time()-t0:.0f}s)")
        print(f"         {pc}")
        log.append({"phase":2,"ep":ep,"tr_acc":ta,"val_acc":va_acc,"val_f1":vf1})
        if vf1 > best_f1:
            best_f1 = vf1; best_acc = va_acc
            torch.save(model.state_dict(), SAVE_PATH)
            print(f"         ✓ 저장  macro-F1={vf1:.3f}  acc={va_acc:.3f}")

    # ── Threshold Calibration ────────────────────────────────
    print(f"\n{'━'*60}")
    print("Threshold Calibration (val 기준)")
    print("━"*60)
    model.load_state_dict(torch.load(SAVE_PATH, map_location=dev, weights_only=True))
    val_probs, val_labels = get_probs(model, va_ld, dev)
    best_thresholds = calibrate_thresholds(val_probs, val_labels)

    # threshold 저장
    thresh_path = WEIGHTS_DIR / "thresholds_v3.json"
    thresh_dict = {EMOTION_CLASSES[i]: float(best_thresholds[i])
                   for i in range(NUM_CLASSES)}
    thresh_path.write_text(json.dumps(thresh_dict, indent=2))
    print(f"  -> {thresh_path}")

    # ── 최종 Test ────────────────────────────────────────────
    print(f"\n{'━'*60}\n최종 Test\n{'━'*60}")
    model.load_state_dict(torch.load(SAVE_PATH, map_location=dev, weights_only=True))
    test_probs, test_labels = get_probs(model, te_ld, dev)

    # argmax (기본)
    preds_raw = test_probs.argmax(axis=1)
    acc_raw   = (preds_raw == test_labels).mean()
    f1_raw    = f1_score(test_labels, preds_raw, average="macro", zero_division=0)
    print(f"\n[Argmax]  Test Acc={acc_raw:.3f}  macro-F1={f1_raw:.3f}")
    per_cls = {}
    for i, c in enumerate(EMOTION_CLASSES):
        mask = test_labels == i
        if mask.sum() > 0:
            per_cls[c] = round((preds_raw[mask] == i).mean(), 3)
    print(f"Per-class: {per_cls}")

    # threshold 적용
    preds_cal = apply_threshold(test_probs, best_thresholds)
    acc_cal   = (preds_cal == test_labels).mean()
    f1_cal    = f1_score(test_labels, preds_cal, average="macro", zero_division=0)
    print(f"\n[Calibrated]  Test Acc={acc_cal:.3f}  macro-F1={f1_cal:.3f}")
    per_cls_cal = {}
    for i, c in enumerate(EMOTION_CLASSES):
        mask = test_labels == i
        if mask.sum() > 0:
            per_cls_cal[c] = round((preds_cal[mask] == i).mean(), 3)
    print(f"Per-class: {per_cls_cal}")

    LOG_PATH.write_text(json.dumps(log, indent=2, ensure_ascii=False))

    # ── emotion_model.py 4-class 업데이트 안내 ──────────────
    model_py_path = BASE_DIR / "machine-vision-ut" / "emotion_model.py"
    _update_emotion_model_py(model_py_path)

    print(f"\n✅ 완료")
    print(f"   Best val macro-F1 = {best_f1:.3f}  (val_acc={best_acc:.3f})")
    print(f"   Test Acc (argmax)     : {acc_raw:.3f}")
    print(f"   Test Acc (calibrated) : {acc_cal:.3f}")
    print(f"   가중치: {SAVE_PATH}")
    print(f"   threshold: {thresh_path}")
    print(f"   emotion_model.py: 4-class로 자동 업데이트 완료")


if __name__ == "__main__":
    main()
