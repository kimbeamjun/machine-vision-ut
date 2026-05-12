"""
test_step1_imports.py
1단계: 패키지 설치 확인 + 각 모듈 import 테스트

실행: python test_step1_imports.py
"""

import sys
import subprocess

GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
RESET  = "\033[0m"

def ok(msg):   print(f"{GREEN}[OK]{RESET}   {msg}")
def fail(msg): print(f"{RED}[FAIL]{RESET} {msg}")
def info(msg): print(f"{YELLOW}[INFO]{RESET} {msg}")
def sep():     print("-" * 55)


sep()
print("1단계: 패키지 설치 및 import 확인")
sep()

results = {}

# ── 패키지별 import 테스트 ──────────────────────────────────────
checks = [
    ("torch",        "import torch; print(torch.__version__)"),
    ("torchvision",  "import torchvision; print(torchvision.__version__)"),
    ("timm",         "import timm; print(timm.__version__)"),
    ("cv2",          "import cv2; print(cv2.__version__)"),
    ("mediapipe",    "import mediapipe; print(mediapipe.__version__)"),
    ("whisper",      "import whisper; print('ok')"),
    ("scipy",        "import scipy; print(scipy.__version__)"),
    ("matplotlib",   "import matplotlib; print(matplotlib.__version__)"),
    ("numpy",        "import numpy; print(numpy.__version__)"),
    ("celery",       "import celery; print(celery.__version__)"),
    ("redis",        "import redis; print(redis.__version__)"),
    ("minio",        "import minio; print(minio.__version__)"),
    ("python-dotenv","from dotenv import load_dotenv; print('ok')"),
    ("Pillow",       "from PIL import Image; print('ok')"),
]

for name, code in checks:
    try:
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            version = result.stdout.strip()
            ok(f"{name:<18} {version}")
            results[name] = True
        else:
            fail(f"{name:<18} {result.stderr.strip()[:60]}")
            results[name] = False
    except Exception as e:
        fail(f"{name:<18} {e}")
        results[name] = False

# ── ffmpeg 확인 ─────────────────────────────────────────────────
sep()
info("ffmpeg 확인")
try:
    result = subprocess.run(
        ["ffmpeg", "-version"],
        capture_output=True, text=True, timeout=5
    )
    if result.returncode == 0:
        line = result.stdout.splitlines()[0]
        ok(f"ffmpeg           {line}")
        results["ffmpeg"] = True
    else:
        fail("ffmpeg 없음")
        results["ffmpeg"] = False
except FileNotFoundError:
    fail("ffmpeg 없음 — conda install ffmpeg -c conda-forge")
    results["ffmpeg"] = False

# ── CUDA 확인 ───────────────────────────────────────────────────
sep()
info("CUDA / GPU 확인")
try:
    result = subprocess.run(
        [sys.executable, "-c",
         "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'N/A')"],
        capture_output=True, text=True, timeout=15
    )
    if result.returncode == 0:
        out = result.stdout.strip()
        if out.startswith("True"):
            ok(f"CUDA 사용 가능: {out}")
        else:
            info(f"CUDA 없음 (CPU 모드로 동작): {out}")
        results["cuda"] = True
    else:
        info("CUDA 확인 실패 (torch 없음)")
        results["cuda"] = False
except Exception as e:
    fail(f"CUDA 확인 오류: {e}")
    results["cuda"] = False

# ── 모듈 import 테스트 (AI 서버 코드) ───────────────────────────
sep()
info("AI 서버 모듈 import 테스트")

ai_modules = [
    "config",
    "minio_client",
    "emotion_model",
    "emotion_analysis",
    "calibration_analysis",
    "whisper_analysis",
    "confusion_index",
    "gaze_analysis",
    "celery_app",
]

for mod in ai_modules:
    try:
        result = subprocess.run(
            [sys.executable, "-c", f"import {mod}; print('ok')"],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0:
            ok(f"{mod}")
            results[f"mod:{mod}"] = True
        else:
            err = result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "알 수 없는 오류"
            fail(f"{mod} — {err[:80]}")
            results[f"mod:{mod}"] = False
    except Exception as e:
        fail(f"{mod} — {e}")
        results[f"mod:{mod}"] = False

# ── 모델 파일 존재 확인 ─────────────────────────────────────────
sep()
info("모델 파일 확인")
import os
try:
    result = subprocess.run(
        [sys.executable, "-c",
         "from config import EMOTION_MODEL_PATH; "
         "import os; print(os.path.exists(EMOTION_MODEL_PATH), EMOTION_MODEL_PATH)"],
        capture_output=True, text=True, timeout=5
    )
    out = result.stdout.strip()
    if out.startswith("True"):
        ok(f"감정 모델 파일 존재: {out.split(' ', 1)[1]}")
        results["model_file"] = True
    else:
        fail(f"감정 모델 파일 없음: {out.split(' ', 1)[1] if ' ' in out else '경로 확인 불가'}")
        results["model_file"] = False
except Exception as e:
    fail(f"모델 파일 확인 오류: {e}")
    results["model_file"] = False

# ── 결과 요약 ───────────────────────────────────────────────────
sep()
print("결과 요약")
sep()

fail_list = [k for k, v in results.items() if not v]
ok_count  = sum(1 for v in results.values() if v)

print(f"통과: {ok_count} / {len(results)}")
if fail_list:
    print(f"\n{RED}실패 항목:{RESET}")
    for k in fail_list:
        print(f"  - {k}")
    print(f"\n{YELLOW}[조치]")
    if any("mod:" not in k for k in fail_list if k in [c[0] for c in checks]):
        print("  pip install -r requirements.txt")
    if "ffmpeg" in fail_list:
        print("  conda install ffmpeg -c conda-forge")
    if "model_file" in fail_list:
        print("  config.py의 MODEL_DIR 경로 확인")
    print(RESET)
else:
    print(f"\n{GREEN}모든 항목 통과 — 2단계로 진행하세요{RESET}")
    print("  python test_step2_modules.py")
