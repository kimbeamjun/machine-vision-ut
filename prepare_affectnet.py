# prepare_affectnet.py
"""
AffectNet 데이터셋 전처리 스크립트 (캐글 버전)

캐글 다운로드:
  https://www.kaggle.com/datasets/noamsegal/affectnet-training-data
  또는
  https://www.kaggle.com/datasets/mouadriali/affectnetsample

캐글에서 받은 AffectNet 구조 (보통 두 가지 형태 중 하나):

[형태 A] 폴더 구분형:
  affectnet_raw/
    ├── 0/   (Neutral)
    ├── 1/   (Happy)
    ├── 2/   (Sad)
    ├── 3/   (Surprise)
    ├── 4/   (Fear)
    ├── 5/   (Disgust)
    ├── 6/   (Anger)
    └── 7/   (Contempt)

[형태 B] CSV + 이미지형:
  affectnet_raw/
    ├── train/
    │   ├── images/
    │   └── annotations/  (labels.csv)
    └── val/

AffectNet 레이블 → 프로젝트 클래스:
  0=Neutral  → neutral
  1=Happy    → positive
  2=Sad      → negative
  3=Surprise → surprise
  4=Fear     → negative
  5=Disgust  → negative
  6=Anger    → negative
  7=Contempt → negative

실행:
  # 형태 A (폴더형)
  python prepare_affectnet.py --type folder --src ./dataset/affectnet_raw

  # 형태 B (CSV형)
  python prepare_affectnet.py --type csv --src ./dataset/affectnet_raw

  # 자동 감지
  python prepare_affectnet.py --src ./dataset/affectnet_raw
"""

import os
import argparse
import random
import shutil
from pathlib import Path
from collections import Counter
import cv2
from tqdm import tqdm

BASE_DIR   = Path("/home/llm-server/Desktop/beomjun/machine-vision-ut")
OUTPUT_DIR = BASE_DIR / "dataset" / "affectnet"
IMG_SIZE   = 260

# AffectNet 레이블 → 프로젝트 클래스
AFFECT_TO_CLASS = {
    0: "neutral",   # Neutral
    1: "positive",  # Happy
    2: "negative",  # Sad
    3: "surprise",  # Surprise
    4: "negative",  # Fear
    5: "negative",  # Disgust
    6: "negative",  # Anger
    7: "negative",  # Contempt
}

EMOTION_CLASSES = ["neutral", "negative", "positive", "confusion", "surprise"]

# 클래스별 최대 샘플 수 (불균형 완화)
MAX_PER_CLASS = 8000


def detect_format(src_dir: Path) -> str:
    """데이터셋 형태 자동 감지"""
    # 숫자 폴더가 있으면 형태 A
    num_folders = [d for d in src_dir.iterdir() if d.is_dir() and d.name.isdigit()]
    if num_folders:
        return "folder"
    # train/ 폴더가 있으면 형태 B
    if (src_dir / "train").exists():
        return "csv"
    # 직접 이미지 폴더
    return "folder"


def process_folder_type(src_dir: Path):
    """형태 A: 숫자 폴더형 처리"""
    print(f"[형태 A] 폴더형 처리: {src_dir}")

    all_samples = []
    for label_dir in sorted(src_dir.iterdir()):
        if not label_dir.is_dir():
            continue
        try:
            label = int(label_dir.name)
        except ValueError:
            # 숫자가 아닌 폴더명이면 클래스명으로 처리
            label_map = {
                "neutral": 0, "happy": 1, "sad": 2, "surprise": 3,
                "fear": 4, "disgust": 5, "anger": 6, "contempt": 7
            }
            label = label_map.get(label_dir.name.lower(), -1)
            if label == -1:
                continue

        class_name = AFFECT_TO_CLASS.get(label)
        if class_name is None:
            continue

        imgs = (list(label_dir.glob("*.jpg")) +
                list(label_dir.glob("*.png")) +
                list(label_dir.glob("*.jpeg")))
        for p in imgs:
            all_samples.append((p, class_name, label))

    _process_and_save(all_samples)


def process_csv_type(src_dir: Path):
    """형태 B: CSV + 이미지형 처리"""
    import pandas as pd

    print(f"[형태 B] CSV형 처리: {src_dir}")
    all_samples = []

    for split in ["train", "val"]:
        split_dir = src_dir / split
        if not split_dir.exists():
            continue

        # CSV 파일 탐색
        csv_files = list(split_dir.glob("*.csv")) + list((split_dir / "annotations").glob("*.csv"))
        if not csv_files:
            print(f"  {split}/: CSV 없음 — 폴더 직접 스캔")
            process_folder_type(split_dir)
            return

        csv_path = csv_files[0]
        df = pd.read_csv(csv_path)
        print(f"  {split}/: {len(df)}행 로드 ({csv_path.name})")

        # 컬럼명 자동 감지
        label_col = next((c for c in df.columns if "label" in c.lower() or "expression" in c.lower()), None)
        path_col  = next((c for c in df.columns if "path" in c.lower() or "file" in c.lower() or "image" in c.lower()), None)

        if label_col is None or path_col is None:
            print(f"  컬럼 목록: {df.columns.tolist()}")
            print("  label/path 컬럼을 찾을 수 없습니다. 컬럼명을 확인하세요.")
            return

        img_base = split_dir / "images"
        for _, row in tqdm(df.iterrows(), total=len(df), desc=f"  {split}"):
            label = int(row[label_col])
            class_name = AFFECT_TO_CLASS.get(label)
            if class_name is None:
                continue
            img_path = img_base / str(row[path_col])
            if img_path.exists():
                all_samples.append((img_path, class_name, label))

    _process_and_save(all_samples)


def _process_and_save(all_samples: list):
    """이미지 리사이즈 + train/val 분할 저장"""
    # 클래스별 샘플 수 제한 (불균형 완화)
    from collections import defaultdict
    class_samples = defaultdict(list)
    for item in all_samples:
        class_samples[item[1]].append(item)

    print(f"\n원본 클래스 분포:")
    for cls in EMOTION_CLASSES:
        print(f"  {cls:12s}: {len(class_samples[cls])}장")

    # 클래스별 최대 MAX_PER_CLASS개로 제한 후 합치기
    balanced = []
    for cls, samples in class_samples.items():
        random.shuffle(samples)
        balanced.extend(samples[:MAX_PER_CLASS])

    print(f"\n균형 조정 후 (max {MAX_PER_CLASS}/class):")
    cnt = Counter(s[1] for s in balanced)
    for cls in EMOTION_CLASSES:
        print(f"  {cls:12s}: {cnt.get(cls, 0)}장")
    print(f"  합계: {len(balanced)}장")

    # train / val 분할 (85:15)
    random.seed(42)
    random.shuffle(balanced)
    n_val    = int(len(balanced) * 0.15)
    val_s    = balanced[:n_val]
    train_s  = balanced[n_val:]

    # 출력 디렉토리 생성
    for split in ["train", "val"]:
        for cls in EMOTION_CLASSES:
            (OUTPUT_DIR / split / cls).mkdir(parents=True, exist_ok=True)

    # 이미지 저장
    for split, samples in [("train", train_s), ("val", val_s)]:
        for img_path, class_name, _ in tqdm(samples, desc=f"  {split} 저장"):
            img = cv2.imread(str(img_path))
            if img is None:
                continue
            img_resized = cv2.resize(img, (IMG_SIZE, IMG_SIZE))
            dst = OUTPUT_DIR / split / class_name / img_path.name
            # 파일명 중복 방지
            if dst.exists():
                dst = dst.with_stem(f"{dst.stem}_{random.randint(0, 9999):04d}")
            cv2.imwrite(str(dst), img_resized)

    print(f"\n✅ 전처리 완료: {OUTPUT_DIR}")
    print(f"  train: {len(train_s)}장")
    print(f"  val:   {len(val_s)}장")
    print(f"\ntrain_v2.py 실행 명령:")
    print(f"  python train_v2.py --affectnet {OUTPUT_DIR}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--src",  type=str,
                        default=str(BASE_DIR / "dataset" / "affectnet_raw"),
                        help="AffectNet 원본 경로")
    parser.add_argument("--type", type=str, choices=["folder", "csv", "auto"],
                        default="auto", help="데이터셋 형태")
    args = parser.parse_args()

    src_dir = Path(args.src)
    if not src_dir.exists():
        print(f"[오류] 경로 없음: {src_dir}")
        print("\n캐글에서 AffectNet 다운로드:")
        print("  1. https://www.kaggle.com/datasets/noamsegal/affectnet-training-data")
        print(f"  2. 압축 해제 후 → {src_dir}")
        print("  3. python prepare_affectnet.py --src <압축해제경로>")
        return

    fmt = args.type if args.type != "auto" else detect_format(src_dir)
    print(f"감지된 형태: {fmt}")

    if fmt == "folder":
        process_folder_type(src_dir)
    else:
        process_csv_type(src_dir)


if __name__ == "__main__":
    main()
