# ai_server/config.py
# 환경 변수 및 전역 설정

import os
from dotenv import load_dotenv

load_dotenv()

# ── Redis ────────────────────────────────────────────────────────
# 큐A: 메인 서버 Redis (Celery Broker — 태스크 수신)
REDIS_HOST_A     = os.getenv("REDIS_HOST_A",     "10.10.10.113")
REDIS_PORT_A     = int(os.getenv("REDIS_PORT_A", "6379"))
REDIS_PASSWORD_A = os.getenv("REDIS_PASSWORD_A", "1234")

# 큐B: AI 서버 로컬 Redis (분석 결과 메인 서버로 송신)
REDIS_HOST       = os.getenv("REDIS_HOST_B",     "10.10.10.128")
REDIS_PORT_B     = int(os.getenv("REDIS_PORT_B", "6380"))
REDIS_PASSWORD_B = os.getenv("REDIS_PASSWORD_B", "")  # 비밀번호 없으면 빈 문자열

# ── MinIO ────────────────────────────────────────────────────────
MINIO_ENDPOINT  = os.getenv("MINIO_ENDPOINT",   "10.10.10.113:9000")
MINIO_ACCESS    = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET    = os.getenv("MINIO_SECRET_KEY", "minioadmin")
MINIO_BUCKET    = os.getenv("MINIO_BUCKET",     "udt")
MINIO_SECURE    = False

# ── Celery ───────────────────────────────────────────────────────
# 비밀번호 있으면 :password@ 형태로 URL에 포함
_broker_auth = f":{REDIS_PASSWORD_A}@" if REDIS_PASSWORD_A else ""
CELERY_BROKER  = f"redis://{_broker_auth}{REDIS_HOST_A}:{REDIS_PORT_A}/0"
CELERY_BACKEND = f"redis://{_broker_auth}{REDIS_HOST_A}:{REDIS_PORT_A}/1"

# ── 모델 경로 ────────────────────────────────────────────────────
MODEL_DIR           = os.getenv("MODEL_DIR", "/home/llm-server/Desktop/beomjun/machine-vision-ut/models/weights")
EMOTION_MODEL_PATH  = os.path.join(MODEL_DIR, "emotion_model_v3.pth")

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
