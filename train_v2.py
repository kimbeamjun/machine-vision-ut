# train_v2.py
import os, sys, time, json, random, argparse
import numpy as np
from pathlib import Path
from collections import Counter, defaultdict
import torch, torch.nn as nn, torch.optim as optim
from torch.utils.data import Dataset, DataLoader, WeightedRandomSampler
from torchvision import transforms
import cv2, timm
from tqdm import tqdm

BASE_DIR    = Path("/home/llm-server/Desktop/beomjun")
DATASET_DIR = BASE_DIR / "machine-vision-ut" / "dataset"
WEIGHTS_DIR = BASE_DIR / "machine-vision-ut" / "models" / "weights"
WEIGHTS_DIR.mkdir(parents=True, exist_ok=True)
SAVE_PATH = WEIGHTS_DIR / "emotion_model_v2.pth"
LOG_PATH  = WEIGHTS_DIR / "train_log_v2.json"

# AffectNet/CK+ 폴더명 → 프로젝트 클래스 (대소문자 모두 처리)
FOLDER_TO_CLASS = {
    "anger":"negative", "Anger":"negative",
    "contempt":"negative", "Contempt":"negative",
    "disgust":"negative", "disgust":"negative",
    "fear":"negative", "Fear":"negative",
    "happy":"positive", "Happy":"positive",
    "sad":"negative", "Sad":"negative",
    "sadness":"negative",
    "neutral":"neutral", "Neutral":"neutral",
    "surprise":"surprise", "Surprise":"surprise",
    # RAF-DB 숫자
    "1":"surprise","2":"negative","3":"negative",
    "4":"positive","5":"negative","6":"negative","7":"neutral",
}
EMOTION_CLASSES = ["neutral", "negative", "positive", "confusion", "surprise"]
CLASS_TO_IDX    = {c: i for i, c in enumerate(EMOTION_CLASSES)}

IMG_SIZE        = 260
BATCH_SIZE      = 16
NUM_WORKERS     = 2
PHASE1_EPOCHS   = 12
PHASE2_EPOCHS   = 28
LR_PHASE1       = 5e-4
LR_PHASE2       = 5e-5
WEIGHT_DECAY    = 1e-4
LABEL_SMOOTHING = 0.1
DROPOUT         = 0.35
VAL_RATIO       = 0.15
TEST_RATIO      = 0.15
SEED            = 42
FEAT_DIM        = 1408
MEAN = [0.485, 0.456, 0.406]
STD  = [0.229, 0.224, 0.225]


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
                transforms.RandomAffine(degrees=0, translate=(0.07, 0.07), shear=5),
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


def _load_dir(base: Path) -> list:
    """폴더 하나를 스캔해서 샘플 리스트 반환"""
    samples = []
    if not base.exists():
        return samples
    for d in sorted(base.iterdir()):
        if not d.is_dir():
            continue
        # 대소문자 모두 처리
        cls = FOLDER_TO_CLASS.get(d.name) or FOLDER_TO_CLASS.get(d.name.lower())
        if cls is None:
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
    """
    AffectNet 구조 지원:
      d/Train/{class}/*.jpg
      d/Test/{class}/*.jpg
    또는
      d/train/{class}/  d/val/{class}/
    """
    s = []
    # Train/Test 형식
    for split in ["Train", "train", "Test", "test", "val", "Val"]:
        s += _load_dir(d / split)
    if not s:
        # 바로 클래스 폴더가 있는 경우
        s = _load_dir(d)
    print(f"[AffectNet] {len(s)}장")
    return s


def load_rafdb(d: Path) -> list:
    s = []
    for split in ["train", "test"]:
        s += _load_dir(d / split)
    print(f"[RAF-DB] {len(s)}장")
    return s


def split_ds(samples):
    random.seed(SEED)
    random.shuffle(samples)
    n = len(samples)
    nt = int(n * TEST_RATIO)
    nv = int(n * VAL_RATIO)
    return samples[nt + nv:], samples[nt:nt + nv], samples[:nt]


def print_dist(name, samples):
    cnt = Counter(l for _, l in samples)
    print(f"\n{name} ({len(samples)}장):")
    for i, c in enumerate(EMOTION_CLASSES):
        bar = "█" * (cnt.get(i, 0) // max(len(samples) // 40, 1))
        print(f"  {c:12s}: {cnt.get(i, 0):5d}  {bar}")


def make_sampler(samples):
    labels = [l for _, l in samples]
    cnt    = Counter(labels)
    # 실제 존재하는 클래스만 가중치 계산 (없는 클래스 제외)
    w = {}
    for c, n in cnt.items():
        w[c] = 1.0 / n   # 샘플 수 역수 → 균등 샘플링
    total_w = sum(w.values())
    w = {c: v / total_w for c, v in w.items()}
    weights = [w[l] for l in labels]
    return WeightedRandomSampler(weights, len(samples), replacement=True)


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
            return self.head(self.backbone(x.view(B*T, C, H, W)).view(B, T, FEAT_DIM))
        return self.head(self.backbone(x))


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
        scaler.step(opt)
        scaler.update()
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
    cc, ct = defaultdict(int), defaultdict(int)
    for p, l in zip(pa, la):
        ct[l] += 1
        cc[l] += int(p == l)
    pc = {EMOTION_CLASSES[k]: round(cc[k] / ct[k], 3) for k in ct}
    return ls / tot, cr / tot, pc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--affectnet", default=None, help="AffectNet 경로")
    ap.add_argument("--rafdb",     default=None, help="RAF-DB 경로")
    args = ap.parse_args()

    dev = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"\nDevice: {dev}")
    if dev.type == "cuda":
        tot  = torch.cuda.get_device_properties(0).total_memory / 1e9
        used = torch.cuda.memory_reserved(0) / 1e9
        print(f"GPU: {torch.cuda.get_device_name(0)}")
        print(f"VRAM: {tot:.1f}GB  (여유: {tot - used:.1f}GB)")

    # 데이터 로드
    ck = load_ckplus(DATASET_DIR)
    af = load_affectnet(Path(args.affectnet)) if args.affectnet else []
    rf = load_rafdb(Path(args.rafdb))         if args.rafdb     else []
    print(f"합계: CK+={len(ck)}, AffectNet={len(af)}, RAF-DB={len(rf)}")

    # 분할
    ck_tr, ck_va, ck_te = split_ds(ck)
    tr = list(ck_tr); te = list(ck_te)
    if af:
        a_tr, _, a_te = split_ds(af)
        tr += a_tr; te += a_te
    if rf:
        r_tr, _, r_te = split_ds(rf)
        tr += r_tr; te += r_te
    va = list(ck_va)

    print_dist("Train", tr)
    print_dist("Val",   va)

    tr_ld = DataLoader(EmotionDataset(tr, True),  BATCH_SIZE,
                       sampler=make_sampler(tr), num_workers=NUM_WORKERS, pin_memory=True)
    va_ld = DataLoader(EmotionDataset(va, False), BATCH_SIZE,
                       shuffle=False, num_workers=NUM_WORKERS, pin_memory=True)
    te_ld = DataLoader(EmotionDataset(te, False), BATCH_SIZE,
                       shuffle=False, num_workers=NUM_WORKERS, pin_memory=True)

    # 모델 초기화
    model = EmotionModel()
    v1 = WEIGHTS_DIR / "emotion_model.pth"
    if v1.exists():
        st = torch.load(v1, map_location=dev, weights_only=True)
        bb = {k: v for k, v in st.items() if k.startswith("backbone.")}
        model.load_state_dict(bb, strict=False)
        print(f"\n✓ v1 백본 로드 ({len(bb)}레이어) — head 새로 학습")
    else:
        bb = timm.create_model("efficientnet_b2", pretrained=True,
                               num_classes=0, global_pool="avg")
        model.backbone.load_state_dict(bb.state_dict())
        print("\n✓ ImageNet pretrained 백본")
    model.to(dev)

    # ── 핵심 수정: 클래스 가중치를 실제 존재 클래스만으로 계산 ──
    cnt_tr = Counter(l for _, l in tr)
    present = [i for i in range(5) if cnt_tr.get(i, 0) > 0]
    cw = torch.ones(5, dtype=torch.float32)
    for i in present:
        cw[i] = 1.0 / cnt_tr[i]
    # 없는 클래스(neutral, confusion)는 가중치 0 → 손실에 기여 안 함
    for i in range(5):
        if cnt_tr.get(i, 0) == 0:
            cw[i] = 0.0
    cw = cw / cw[cw > 0].sum()   # 정규화
    cw = cw.to(dev)
    print(f"\n클래스 가중치: { {EMOTION_CLASSES[i]: round(float(cw[i]),4) for i in range(5)} }")

    crit   = nn.CrossEntropyLoss(weight=cw, label_smoothing=LABEL_SMOOTHING)
    scaler = torch.amp.GradScaler("cuda")
    best   = 0.0
    log    = []

    print(f"\n증강: RandomCrop+Flip+Rotation+ColorJitter+Affine+Grayscale+RandomErasing")
    print(f"LabelSmoothing={LABEL_SMOOTHING} | Dropout={DROPOUT} | Batch={BATCH_SIZE}")

    # ── Phase 1: 백본 freeze ──────────────────────────────────
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
        tl, ta           = train_ep(model, tr_ld, crit, opt, dev, scaler)
        vl, va_acc, pc   = eval_ep(model, va_ld, crit, dev)
        sch.step()
        print(f"[P1 {ep:02d}/{PHASE1_EPOCHS}] "
              f"train {tl:.4f}/{ta:.3f}  val {vl:.4f}/{va_acc:.3f}  ({time.time()-t0:.0f}s)")
        print(f"         {pc}")
        log.append({"phase":1,"ep":ep,"tr_acc":ta,"val_acc":va_acc})
        if va_acc > best:
            best = va_acc
            torch.save(model.state_dict(), SAVE_PATH)
            print(f"         ✓ 저장 val_acc={va_acc:.3f}")

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
        tl, ta           = train_ep(model, tr_ld, crit, opt, dev, scaler)
        vl, va_acc, pc   = eval_ep(model, va_ld, crit, dev)
        sch.step()
        print(f"[P2 {ep:02d}/{PHASE2_EPOCHS}] "
              f"train {tl:.4f}/{ta:.3f}  val {vl:.4f}/{va_acc:.3f}  ({time.time()-t0:.0f}s)")
        print(f"         {pc}")
        log.append({"phase":2,"ep":ep,"tr_acc":ta,"val_acc":va_acc})
        if va_acc > best:
            best = va_acc
            torch.save(model.state_dict(), SAVE_PATH)
            print(f"         ✓ 저장 val_acc={va_acc:.3f}")

    # ── Test ─────────────────────────────────────────────────
    print(f"\n{'━'*60}\n최종 Test\n{'━'*60}")
    if SAVE_PATH.exists():
        model.load_state_dict(torch.load(SAVE_PATH, map_location=dev, weights_only=True))
        _, ta, pc = eval_ep(model, te_ld, crit, dev)
        print(f"Test Acc={ta:.3f}")
        print(f"Per-class: {pc}")
    else:
        print("⚠ 저장된 가중치 없음")

    LOG_PATH.write_text(json.dumps(log, indent=2, ensure_ascii=False))
    print(f"\n✅ 완료  Best val_acc={best:.3f}  (v1:0.857  향상:{best-0.857:+.3f})")
    print(f"   가중치: {SAVE_PATH}")


if __name__ == "__main__":
    main()
