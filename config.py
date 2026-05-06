# ai_server/config.py
# 환경 변수 및 전역 설정

import os
from dotenv import load_dotenv

load_dotenv()

# ── DB ──────────────────────────────────────────────────────────
DB_HOST     = os.getenv("DB_HOST",     "10.10.10.113")
DB_PORT     = int(os.getenv("DB_PORT", "3306"))
DB_USER     = os.getenv("DB_USER",     "udt_admin")
DB_PASSWORD = os.getenv("DB_PASSWORD", "1234")
DB_NAME     = os.getenv("DB_NAME",     "UDT")

# ── Redis ────────────────────────────────────────────────────────
REDIS_HOST  = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT_A = 6379   # 큐 A: 메인서버 → AI Worker (session_id 수신)
REDIS_PORT_B = 6380   # 큐 B: AI Worker → 메인서버 (분석결과 송신)

# ── MinIO ────────────────────────────────────────────────────────
MINIO_ENDPOINT  = os.getenv("MINIO_ENDPOINT",   "10.10.10.113:9000")
MINIO_ACCESS    = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET    = os.getenv("MINIO_SECRET_KEY", "minioadmin")
MINIO_BUCKET    = os.getenv("MINIO_BUCKET",     "udt")
MINIO_SECURE    = False

# ── Celery ───────────────────────────────────────────────────────
CELERY_BROKER   = f"redis://{REDIS_HOST}:{REDIS_PORT_A}/0"
CELERY_BACKEND  = f"redis://{REDIS_HOST}:{REDIS_PORT_A}/1"

# ── 모델 경로 ────────────────────────────────────────────────────
MODEL_DIR           = os.getenv("MODEL_DIR", "/home/llm-server/Desktop/beomjun/models/weights")
EMOTION_MODEL_PATH  = os.path.join(MODEL_DIR, "emotion_model.pth")

# ── 분석 하이퍼파라미터 ──────────────────────────────────────────
FRAME_BATCH_SIZE    = 16          # Transformer 입력 프레임 수
WHISPER_MODEL       = "medium"    # VRAM ~2GB
NO_SPEECH_THRESHOLD = 0.6         # VAD 필터 임계값
ROLLING_WINDOW      = 30          # 혼란도 스무딩 윈도우 (프레임)

# ── 혼란도 가중치 (팀 내 조정 가능) ─────────────────────────────
CI_W1 = 0.25   # 부정감정 비율
CI_W2 = 0.25   # 시선 이탈 비율
CI_W3 = 0.15   # 발화 공백
CI_W4 = 0.10   # 발화 속도 급변
CI_W5 = 0.15   # 체류시간 이상치
CI_W6 = 0.10   # 태스크 실패 여부
