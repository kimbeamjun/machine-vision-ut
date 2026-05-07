# prepare_rafdb.py
"""
RAF-DB 데이터셋 다운로드 및 전처리 스크립트

RAF-DB 공식 신청:
  http://www.whdeng.cn/RAF/model1.html
  (학술용 무료 / 이메일 신청 후 구글 드라이브 링크 수신)

다운로드 후 압축 해제 위치:
  ~/Desktop/beomjun/machine-vision-ut/dataset/rafdb_raw/
    └── basic/
        ├── Image/
        │   └── aligned/         ← 정렬된 얼굴 이미지 (100×100)
        │       ├── train_00001_aligned.jpg
        │       └── ...
        └── EmoLabel/
            └── list_patition_label.txt  ← 레이블 파일

RAF-DB 레이블:
  1=Surprise 2=Fear 3=Disgust 4=Happiness 5=Sadness 6=Anger 7=Neutral

전처리 후 구조:
  dataset/rafdb/
    ├── train/
    │   ├── 1/ (surprise)
    │   ├── 2/ (fear → negative)
    │   └── ...
    └── test/
        └── ...

실행:
  python prepare_rafdb.py
"""

import os
import shutil
from pathlib import Path
from tqdm import tqdm
import cv2

RAW_DIR    = Path("/home/llm-server/Desktop/beomjun/machine-vision-ut/dataset/rafdb_raw")
OUTPUT_DIR = Path("/home/llm-server/Desktop/beomjun/machine-vision-ut/dataset/rafdb")
LABEL_FILE = RAW_DIR / "basic" / "EmoLabel" / "list_patition_label.txt"
IMAGE_DIR  = RAW_DIR / "basic" / "Image" / "aligned"
IMG_SIZE   = 260


def prepare():
    if not RAW_DIR.exists():
        print(f"[오류] RAF-DB 원본 폴더 없음: {RAW_DIR}")
        print("  1. http://www.whdeng.cn/RAF/model1.html 에서 신청")
        print("  2. 수신한 구글 드라이브 링크에서 다운로드")
        print(f"  3. {RAW_DIR} 에 압축 해제")
        return

    if not LABEL_FILE.exists():
        print(f"[오류] 레이블 파일 없음: {LABEL_FILE}")
        return

    # 출력 디렉토리 생성
    for split in ["train", "test"]:
        for cls in range(1, 8):
            (OUTPUT_DIR / split / str(cls)).mkdir(parents=True, exist_ok=True)

    # 레이블 파일 파싱
    # 형식: train_00001.jpg 1
    label_map = {}
    with open(LABEL_FILE, "r") as f:
        for line in f:
            parts = line.strip().split()
            if len(parts) == 2:
                fname, label = parts[0], int(parts[1])
                label_map[fname] = label

    print(f"전체 레이블: {len(label_map)}개")

    train_cnt = test_cnt = skip_cnt = 0

    for fname, label in tqdm(label_map.items(), desc="전처리"):
        # train/test 구분 (파일명 기준)
        split = "train" if fname.startswith("train") else "test"

        # aligned 이미지 파일명으로 변환
        stem     = fname.replace(".jpg", "")
        aligned  = IMAGE_DIR / f"{stem}_aligned.jpg"

        if not aligned.exists():
            skip_cnt += 1
            continue

        # 이미지 리사이즈 후 저장
        img = cv2.imread(str(aligned))
        if img is None:
            skip_cnt += 1
            continue

        img_resized = cv2.resize(img, (IMG_SIZE, IMG_SIZE))
        dst = OUTPUT_DIR / split / str(label) / f"{stem}.jpg"
        cv2.imwrite(str(dst), img_resized)

        if split == "train":
            train_cnt += 1
        else:
            test_cnt += 1

    print(f"\n전처리 완료:")
    print(f"  train: {train_cnt}장")
    print(f"  test:  {test_cnt}장")
    print(f"  스킵:  {skip_cnt}장")
    print(f"  저장:  {OUTPUT_DIR}")

    # 클래스별 분포
    label_names = {
        "1": "surprise", "2": "fear(negative)", "3": "disgust(negative)",
        "4": "happy(positive)", "5": "sadness(negative)",
        "6": "anger(negative)", "7": "neutral"
    }
    print("\n클래스별 train 분포:")
    for cls, name in label_names.items():
        count = len(list((OUTPUT_DIR / "train" / cls).glob("*.jpg")))
        print(f"  {cls} {name}: {count}장")


if __name__ == "__main__":
    prepare()
