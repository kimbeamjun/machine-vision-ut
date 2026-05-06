# ai_server/training/prepare_ckplus.py
"""
CK+ (Extended Cohn-Kanade) 데이터셋 전처리 스크립트

CK+ 데이터셋 공식 배포는 신청 필요:
  https://www.jeffcohn.net/Resources/

다운로드 후 아래 구조로 압축 해제:
  data/ckplus_raw/
    ├── cohn-kanade-images/      ← 얼굴 이미지 시퀀스
    │   └── S005/001/000001.png ...
    └── FACS_labels/             ← 감정 레이블 (선택)
        └── Emotion/
            └── S005/001/S005_001_00000011_emotion.txt

CK+ 감정 코드 → 우리 프로젝트 클래스 매핑:
  0: neutral   → neutral
  1: anger     → negative
  2: contempt  → negative
  3: disgust   → negative
  4: fear      → negative
  5: happy     → positive
  6: sadness   → negative
  7: surprise  → surprise
  (confusion은 CK+에 없음 → 학습 후 추론 시 모델이 자연스럽게 분리됨)
"""

import os
import shutil
import random
from pathlib import Path
import cv2
import numpy as np
from tqdm import tqdm

# ── 경로 설정 ────────────────────────────────────────────────────
RAW_IMAGE_DIR  = Path("data/ckplus_raw/cohn-kanade-images")
EMOTION_DIR    = Path("data/ckplus_raw/FACS_labels/Emotion")
OUTPUT_DIR     = Path("data/ckplus_processed")   # 전처리 완료 이미지 저장

IMG_SIZE = 260   # EfficientNet-B2 입력 크기

# CK+ 감정 코드 → 프로젝트 클래스
CK_TO_CLASS = {
    0: "neutral",
    1: "negative",   # anger
    2: "negative",   # contempt
    3: "negative",   # disgust
    4: "negative",   # fear
    5: "positive",   # happy
    6: "negative",   # sadness
    7: "surprise",
}

CLASSES = ["neutral", "negative", "positive", "confusion", "surprise"]


def _read_emotion_label(label_path: Path) -> int | None:
    """레이블 파일에서 감정 코드(0~7) 읽기"""
    try:
        text = label_path.read_text().strip()
        return int(float(text))
    except Exception:
        return None


def prepare_dataset(val_ratio: float = 0.15, test_ratio: float = 0.15):
    """
    CK+ 원본 데이터 → 전처리 후 train/val/test 분할 저장

    CK+ 특성:
    - 각 시퀀스의 마지막 3장: 피크 감정 프레임 (레이블 있음)
    - 첫 번째 프레임: neutral (레이블 없음 → neutral로 처리)
    """
    print("=== CK+ 데이터셋 전처리 시작 ===")

    # 출력 디렉토리 생성
    for split in ["train", "val", "test"]:
        for cls in CLASSES:
            (OUTPUT_DIR / split / cls).mkdir(parents=True, exist_ok=True)

    all_samples = []   # [(image_path, class_name)]

    # 시퀀스 순회
    subjects = sorted(RAW_IMAGE_DIR.iterdir()) if RAW_IMAGE_DIR.exists() else []
    if not subjects:
        print(f"[오류] CK+ 이미지 경로 없음: {RAW_IMAGE_DIR}")
        print("  data/ckplus_raw/cohn-kanade-images/ 에 데이터를 압축 해제하세요.")
        return

    for subj_dir in tqdm(subjects, desc="Subject"):
        for seq_dir in sorted(subj_dir.iterdir()):
            frames = sorted(seq_dir.glob("*.png"))
            if not frames:
                continue

            # 레이블 파일 탐색
            label_files = sorted((EMOTION_DIR / subj_dir.name / seq_dir.name).glob("*_emotion.txt"))
            if label_files:
                emotion_code = _read_emotion_label(label_files[0])
                class_name   = CK_TO_CLASS.get(emotion_code, "neutral")
                # 마지막 3프레임: 피크 감정
                for frame in frames[-3:]:
                    all_samples.append((frame, class_name))
            # 첫 번째 프레임: neutral
            all_samples.append((frames[0], "neutral"))

    print(f"전체 샘플 수: {len(all_samples)}")

    # 클래스별 샘플 수 출력
    from collections import Counter
    cnt = Counter(cls for _, cls in all_samples)
    for c, n in sorted(cnt.items()):
        print(f"  {c}: {n}장")

    # 셔플 후 분할
    random.seed(42)
    random.shuffle(all_samples)
    n      = len(all_samples)
    n_val  = int(n * val_ratio)
    n_test = int(n * test_ratio)
    splits = {
        "test":  all_samples[:n_test],
        "val":   all_samples[n_test:n_test + n_val],
        "train": all_samples[n_test + n_val:],
    }

    # 이미지 전처리 후 복사
    for split, samples in splits.items():
        for img_path, class_name in tqdm(samples, desc=f"{split} 복사"):
            img = cv2.imread(str(img_path))
            if img is None:
                continue
            img_resized = cv2.resize(img, (IMG_SIZE, IMG_SIZE))
            dst = OUTPUT_DIR / split / class_name / img_path.name
            # 같은 파일명 충돌 방지
            if dst.exists():
                stem = img_path.stem
                dst = OUTPUT_DIR / split / class_name / f"{subj_dir.name}_{seq_dir.name}_{img_path.name}"
            cv2.imwrite(str(dst), img_resized)

    print(f"\n전처리 완료: {OUTPUT_DIR}")
    for split in ["train", "val", "test"]:
        total = sum(len(list((OUTPUT_DIR / split / c).iterdir())) for c in CLASSES)
        print(f"  {split}: {total}장")


if __name__ == "__main__":
    prepare_dataset()
