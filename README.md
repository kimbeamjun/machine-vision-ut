# UDT AI 서버

UDT(User-Defined Testing) 플랫폼의 AI 분석 워커 서버입니다.  
Celery Worker 기반으로 동작하며 표정 / 시선 / 음성 3종 분석과 혼란도 산출을 담당합니다.

---

## 목차

1. [시스템 구성](#시스템-구성)
2. [파일 구조](#파일-구조)
3. [환경 설정](#환경-설정)
4. [설치](#설치)
5. [실행](#실행)
6. [분석 파이프라인](#분석-파이프라인)
7. [트러블슈팅](#트러블슈팅)

---

## 시스템 구성

```
[메인 서버 FastAPI]
    │  Celery send_task (큐A, ai_tasks)
    ▼
[Redis 큐A] ──→ [AI 서버 Celery Worker ×4]
 10.10.10.113:6379       │  MinIO SDK (업로드)
                         ▼
                      [MinIO]
                  10.10.10.113:9000

분석 완료 후:
AI 서버 → main_app.send_task() → 메인 서버 Celery Worker
```

| 항목 | 값 |
|------|----|
| AI 서버 IP | 10.10.10.128 |
| 메인 서버 IP | 10.10.10.113 |
| Redis Broker (큐A) | 10.10.10.113:6379 |
| MinIO | 10.10.10.113:9000 |
| Worker 수 | 4 |
| GPU | NVIDIA RTX 5090 (32GB VRAM) |

---

## 파일 구조

```
ai_server/
├── celery_app.py           # Worker 태스크 정의 (메인 진입점)
├── calibration_analysis.py # MediaPipe 홍채 좌표 추출
├── emotion_analysis.py     # 표정 분석 (EfficientNet-B2 + Transformer)
├── emotion_model.py        # 모델 아키텍처 정의
├── gaze_analysis.py        # 시선 매핑 + 히트맵 생성
├── whisper_analysis.py     # STT + silence_sec 계산
├── confusion_index.py      # 혼란도 산출 (6요소 가중합)
├── minio_client.py         # MinIO 유틸리티
├── config.py               # 환경변수 및 하이퍼파라미터
├── requirements.txt
└── models/weights/
    ├── emotion_model_v3.pth
    └── thresholds_v3.json
```

---

## 환경 설정

### `.env` 파일 생성

프로젝트 루트에 `.env` 파일을 만들고 아래 값을 채웁니다.

```env
# Redis 큐A (메인 서버 Broker)
REDIS_HOST_A=10.10.10.113
REDIS_PORT_A=6379
REDIS_PASSWORD_A=1234

# MinIO
MINIO_ENDPOINT=10.10.10.113:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin
MINIO_BUCKET=ut-platform

# 모델 경로
MODEL_DIR=/home/llm-server/Desktop/beomjun/machine-vision-ut/models/weights
```

---

## 설치

### 1. Conda 환경 생성

```bash
conda create -n udt python=3.12 -y
conda activate udt
```

### 2. PyTorch 설치 (CUDA 12.x)

```bash
conda install pytorch torchvision torchaudio pytorch-cuda=12.1 -c pytorch -c nvidia
```

### 3. ffmpeg 설치

```bash
conda install ffmpeg -c conda-forge
```

### 4. Python 패키지 설치

```bash
pip install -r requirements.txt
```

---

## 실행

### Worker 시작

```bash
cd /home/llm-server/Desktop/beomjun/machine-vision-ut

nohup celery -A celery_app worker \
  --loglevel=info \
  -Q ai_tasks \
  -c 4 \
  -n ai_worker@%h \
  > logs/worker.log 2>&1 &
```

| 옵션 | 설명 |
|------|------|
| `-Q ai_tasks` | 수신할 큐 이름 (메인 서버 설정과 반드시 일치) |
| `-c 4` | Worker 동시 실행 수 (RTX 5090 기준 VRAM 여유 고려) |
| `-n ai_worker@%h` | Worker 노드 이름 |

### 로그 확인

```bash
tail -f logs/worker.log
```

### Worker 종료

```bash
ps aux | grep "celery worker" | grep -v grep | awk '{print $2}' | xargs kill -9
```

---

## 분석 파이프라인

### AI-7: 캘리브레이션 분석 (`analyze_calibration`)

```
1. MinIO에서 캘리브레이션 영상 5개 다운로드 (로컬 임시파일)
2. MediaPipe FaceLandmarker로 포인트별 홍채 좌표(gaze_x, gaze_y) 추출
3. main_app.send_task("tasks.process_calibration_result") 로 결과 전달
4. 로컬 임시파일 삭제 (MinIO 파일 건드리지 않음)
```

**성공 기준:** 5개 포인트 중 실패 2개 미만 → 메인 서버가 `calibrated` 처리  
**실패 시:** 메인 서버가 `calib_failed` 처리 → 클라이언트 전체 재촬영 안내

### AI-8: 본 분석 (`analyze_session`)

```
1. MinIO에서 녹화 영상 다운로드 (로컬 임시파일)
2. ThreadPoolExecutor(max_workers=3)로 3종 병렬 분석:
   ├─ 표정 분석: EfficientNet-B2 + Transformer, 16프레임 배치
   ├─ 시선 분석: MediaPipe + LinearNDInterpolator 캘리브레이션 매핑
   └─ 음성 분석: Whisper medium (한국어), VAD 필터 적용
3. 혼란도 산출 (6요소 가중합, 30프레임 롤링 평균)
4. detail.json, heatmap.png MinIO 업로드
5. main_app.send_task("tasks.process_analysis_result") 로 결과 전달
6. 로컬 임시파일 삭제
```

### 혼란도 산출 공식

```
CI = 0.25 × 부정감정비율
   + 0.25 × 시선이탈비율
   + 0.15 × 발화공백
   + 0.10 × 발화속도급변
   + 0.15 × 체류시간이상치
   + 0.10 × 태스크실패율
```

가중치는 `config.py`의 `CI_W1` ~ `CI_W6`에서 조정 가능합니다.

---

## 트러블슈팅

### 1. Worker가 태스크를 받지 못하는 경우

**증상:** 클라이언트에서 영상을 올려도 `Task ... received` 로그가 안 뜸

**원인 및 해결:**

```bash
# Worker 실행 시 큐 이름 확인
celery -A celery_app worker -Q ai_tasks  # ← ai_tasks 로 실행해야 함

# 메인 서버 celery_app.py의 task_routes 큐 이름과 반드시 일치해야 함
# mainserver/background_tasks/celery_app.py
task_routes={
    'celery_app.analyze_calibration': {'queue': 'ai_tasks'},
    'celery_app.analyze_session':     {'queue': 'ai_tasks'},
}
```

---

### 2. `daemonic processes are not allowed to have children`

**증상:**
```
AssertionError: daemonic processes are not allowed to have children
```

**원인:** Celery ForkPoolWorker(데몬 프로세스) 내부에서 `ProcessPoolExecutor`로 자식 프로세스를 생성하려 할 때 파이썬이 차단

**해결:** `celery_app.py`에서 `ProcessPoolExecutor` → `ThreadPoolExecutor`로 교체

```python
# 수정 전
from concurrent.futures import ProcessPoolExecutor, as_completed
with ProcessPoolExecutor(max_workers=3) as executor:

# 수정 후
from concurrent.futures import ThreadPoolExecutor, as_completed
with ThreadPoolExecutor(max_workers=3) as executor:
```

---

### 3. `AttributeError: 'analyze_session' object has no attribute 'reject'`

**증상:**
```
AttributeError: 'analyze_session' object has no attribute 'reject'
```

**원인:** `self.reject(requeue=False)`는 Celery에 존재하지 않는 메서드

**해결:**
```python
# 수정 전
raise self.reject(requeue=False)

# 수정 후
raise
```

---

### 4. Worker `--detach` 모드에서 바로 종료되는 경우

**증상:** `--detach` 옵션으로 실행하면 즉시 종료됨

**원인:** `logs/` 디렉토리 미존재 또는 이전 `worker.pid` 충돌

**해결:**
```bash
mkdir -p logs
rm -f logs/worker.pid
ps aux | grep "celery" | grep -v grep | awk '{print $2}' | xargs kill -9 2>/dev/null
sleep 2

# detach 대신 nohup 사용 권장
nohup celery -A celery_app worker \
  --loglevel=info \
  -Q ai_tasks \
  -c 4 \
  -n ai_worker@%h \
  > logs/worker.log 2>&1 &
```

---

### 5. Whisper 음성 인식 결과가 0건인 경우

**증상:** 분석은 완료되지만 `stt_segments` 테이블에 데이터가 없음

**원인 1 — 오디오 스트림 없음:**
```bash
# 영상에 오디오 스트림이 있는지 확인
ffmpeg -i /path/to/recording.mp4 2>&1 | grep "Audio"
# Audio 스트림이 없으면 클라이언트 녹화 설정에서 마이크 포함 여부 확인
```

**원인 2 — VAD 필터 기준값이 너무 낮음:**  
`no_speech_prob` 임계값이 낮으면 실제 발화도 무음으로 처리됩니다.

```python
# whisper_analysis.py 수정
# 수정 전
if seg.get("no_speech_prob", 0.0) > 0.6:

# 수정 후
if seg.get("no_speech_prob", 0.0) > 0.8:
```

임계값을 올린 후 Worker를 재시작하면 발화 구간이 정상적으로 잡힙니다.

---

### 6. MinIO 연결 오류

**증상:**
```
urllib3.exceptions.MaxRetryError: HTTPConnectionPool(host='10.10.10.113', port=9000)
Failed to establish a new connection: [Errno 111] Connection refused
```

**해결:**
```bash
# 메인 서버에서 MinIO 실행 상태 확인
curl http://10.10.10.113:9000/minio/health/live

# MinIO가 꺼져 있으면 재시작 (메인 서버에서)
docker start minio  # 또는 해당 실행 방식에 맞게
```

---

### 7. `DuplicateNodenameWarning` 경고

**증상:**
```
DuplicateNodenameWarning: Received multiple replies from node name: ai_worker@...
```

**원인:** 이전 Worker 프로세스가 완전히 종료되지 않은 상태에서 새 Worker를 실행

**해결:**
```bash
# celery 관련 프로세스 전체 종료 후 재시작
ps aux | grep "celery" | grep -v grep | awk '{print $2}' | xargs kill -9 2>/dev/null
sleep 3
# 이후 Worker 재시작
```
