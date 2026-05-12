# UDT — UT 결과분석 자동화 AI 플랫폼

사용자 테스트(UT) 중 수집된 녹화 영상을 AI로 분석하여  
**표정 · 시선 · 음성** 데이터를 기반으로 혼란도 점수를 산출하고  
LLM이 UX 개선안 PDF 보고서를 자동 생성하는 플랫폼입니다.

---

## 목차

1. [팀 구성](#팀-구성)
2. [시스템 구성도](#시스템-구성도)
3. [레포지토리 구조](#레포지토리-구조)
4. [인프라 설정](#인프라-설정)
5. [설치 및 실행](#설치-및-실행)
6. [전체 비즈니스 흐름](#전체-비즈니스-흐름)
7. [API 명세 요약](#api-명세-요약)
8. [DB 스키마](#db-스키마)
9. [AI 분석 상세](#ai-분석-상세)
10. [기술 스택](#기술-스택)

---

## 팀 구성

| 이름 | 담당 | 브랜치 |
|------|------|--------|
| 김민기 | 클라이언트 (PySide6) | `client` |
| 김희석 | 메인 서버 (FastAPI) | `mainserver` |
| 김범준 | AI 서버 (Celery Worker) | `aiserver` |

---

## 시스템 구성도

```
[클라이언트 PySide6]
    │  HTTP REST (port 8000)
    ▼
[메인 서버 FastAPI]  ◄──────────────────────────────┐
    │  Celery send_task                              │
    │  큐: ai_tasks                                 │ Celery send_task
    ▼                                              │ 큐: main_tasks
[Redis 큐A] ──► [AI 서버 Celery Worker ×4]         │
 10.10.10.113:6379   │  MinIO SDK                  │
                     ▼                             │
                  [MinIO] ─────────────────────────┘
              10.10.10.113:9000

[MariaDB] ← 메인 서버만 접근 (port 3306)
```

### 서버 IP 정리

| 서버 | IP | 포트 |
|------|----|------|
| 메인 서버 (FastAPI) | 10.10.10.113 | 8000 |
| AI 서버 (Celery) | 10.10.10.128 | — |
| MariaDB | 10.10.10.113 | 3306 |
| MinIO | 10.10.10.113 | 9000 |
| Redis (Celery Broker) | 10.10.10.113 | 6379 |

---

## 레포지토리 구조

```
machine-vision-ut/
├── client/                         # 클라이언트 (PySide6)
│   ├── main.py                     # 진입점
│   ├── requirements.txt
│   ├── .env
│   ├── config/
│   │   └── tasks_config.json       # UT 시나리오 태스크 목록
│   ├── core/
│   │   ├── api_client.py           # 메인 서버 HTTP 통신
│   │   ├── eye_tracker.py          # 시선 추적 보조
│   │   └── recorder.py             # 화면 녹화 (mss + ffmpeg)
│   ├── models/
│   │   └── models.py               # 데이터 모델
│   └── ui/
│       ├── main_window.py          # 메인 윈도우
│       ├── calibration_dialog.py   # 캘리브레이션 UI
│       ├── overlay.py              # 오버레이
│       ├── widgets.py              # 공통 위젯
│       └── styles.py               # QSS 스타일
│
├── mainserver/                     # 메인 서버 (FastAPI)
│   ├── main_server_app.py          # FastAPI 앱 진입점
│   ├── requirements.txt
│   ├── api_data_formats/
│   │   └── api_request_schemas.py  # Pydantic 요청/응답 스키마
│   ├── api_endpoints/
│   │   ├── router_sessions.py      # CL-1 ~ CL-8 엔드포인트
│   │   └── router_webhooks.py      # MinIO 이벤트 웹훅
│   ├── app_settings/
│   │   ├── db_connection.py        # SQLAlchemy 비동기 엔진
│   │   └── storage_minio.py        # MinIO 클라이언트
│   ├── background_tasks/
│   │   ├── celery_app.py           # Celery 앱 설정
│   │   ├── tasks.py                # MS-3, MS-4 큐 소비 태스크
│   │   ├── llm_service.py          # MS-5 LLM API 호출
│   │   └── pdf_service.py          # MS-6 PDF 생성
│   └── database_tables/
│       └── db_orm_models.py        # SQLAlchemy ORM 모델
│
└── aiserver/                       # AI 서버 (Celery Worker)
    ├── celery_app.py               # Worker 태스크 정의 (진입점)
    ├── config.py                   # 환경변수 및 하이퍼파라미터
    ├── requirements.txt
    ├── calibration_analysis.py     # MediaPipe 홍채 좌표 추출
    ├── emotion_analysis.py         # 표정 분석
    ├── emotion_model.py            # EfficientNet-B2 + Transformer
    ├── gaze_analysis.py            # 시선 매핑 + 히트맵 생성
    ├── whisper_analysis.py         # STT + silence_sec
    ├── confusion_index.py          # 혼란도 산출
    ├── minio_client.py             # MinIO 유틸리티
    └── models/weights/
        ├── emotion_model_v3.pth
        └── thresholds_v3.json
```

---

## 인프라 설정

### Redis (메인 서버에서 실행)

```bash
docker run -d --name redis \
  -p 6379:6379 \
  redis:7 redis-server --requirepass 1234
```

### MinIO (메인 서버에서 실행)

```bash
docker run -d --name minio \
  -p 9000:9000 -p 9001:9001 \
  -e MINIO_ROOT_USER=minioadmin \
  -e MINIO_ROOT_PASSWORD=minioadmin \
  minio/minio server /data --console-address ":9001"
```

MinIO 웹 콘솔(`http://10.10.10.113:9001`)에서 버킷 `ut-platform` 생성 후  
**웹훅 이벤트** 설정: `PUT` 이벤트 → `http://10.10.10.113:8000/webhook/minio`

### MariaDB

```sql
CREATE DATABASE UDT CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;
```

```bash
mysql -u root -p UDT < UDT_schema.sql
```

---

## 설치 및 실행

### 1. 클라이언트 (Windows / macOS)

```bash
cd client
pip install -r requirements.txt
python main.py
```

**태스크 설정** — `config/tasks_config.json`에서 UT 시나리오 태스크를 수정합니다.  
앱 재시작 없이 다음 테스트부터 반영됩니다.

```json
{
  "tasks": [
    { "task_order": 1, "name": "메인 로고 클릭" },
    { "task_order": 2, "name": "검색바 입력" },
    { "task_order": 3, "name": "상세 페이지 이동" }
  ]
}
```

### 2. 메인 서버 (10.10.10.113)

`.env` 파일을 `mainserver/` 안에 생성합니다.

```env
DB_URL=mysql+aiomysql://root:password@10.10.10.113:3306/UDT
MINIO_ENDPOINT=10.10.10.113:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin
MINIO_BUCKET=ut-platform
GROQ_API_KEY=gsk_...
```

```bash
cd mainserver
pip install -r requirements.txt

# FastAPI 서버
uvicorn main_server_app:app --host 0.0.0.0 --port 8000

# Celery Worker (별도 터미널)
celery -A background_tasks.celery_app worker \
  --loglevel=info \
  -Q main_tasks \
  -n main_worker@%h
```

### 3. AI 서버 (10.10.10.128)

```bash
cd aiserver

# Conda 환경 생성
conda create -n udt python=3.12 -y
conda activate udt

# PyTorch (CUDA 12.x)
conda install pytorch torchvision torchaudio pytorch-cuda=12.1 -c pytorch -c nvidia

# ffmpeg
conda install ffmpeg -c conda-forge

pip install -r requirements.txt

# Worker 실행
mkdir -p logs
nohup celery -A celery_app worker \
  --loglevel=info \
  -Q ai_tasks \
  -c 4 \
  -n ai_worker@%h \
  > logs/worker.log 2>&1 &

tail -f logs/worker.log
```

---

## 전체 비즈니스 흐름

### 1단계 — 캘리브레이션

```
클라이언트                메인 서버                        AI 서버
──────────────────────────────────────────────────────────────────
CL-1 세션 생성 ──────────► sessions INSERT
                           session_id 반환 ◄──────────────────────

CL-2 × 5 URL 발급 ────────► calibration_points INSERT
                           presigned_url 반환 ◄───────────────────

CL-3 × 5 MinIO 업로드 ───► 웹훅으로 object_key 자동 저장

CL-4 분석 시작 ───────────► send_task(analyze_calibration)
  (동기 대기)                                  │
                                               ▼
                                     MediaPipe 홍채 추출
                                               │
                           ◄── send_task(process_calibration_result)
                           failed < 2  → status=calibrated
                           failed ≥ 2  → status=calib_failed (재촬영)
응답 수신 ◄───────────────
```

### 2단계 — 본 테스트 및 분석

```
클라이언트                메인 서버                        AI 서버
──────────────────────────────────────────────────────────────────
CL-5 메타데이터 전송 ──────► page_logs, task_results INSERT

CL-6 URL 발급 / CL-7 업로드

CL-8 분석 시작 ───────────► send_task(analyze_session)
즉시 accepted 반환 ◄──────  (DB 전체 데이터 kwargs로 구성)
                                               │
                                               ▼
                                   ThreadPoolExecutor(max=3)
                                   ├─ 표정 분석 (EfficientNet-B2)
                                   ├─ 시선 분석 (MediaPipe)
                                   └─ 음성 분석 (Whisper medium)
                                   혼란도 산출 (6요소 가중합)
                                   detail.json / heatmap MinIO 업로드
                                               │
                           ◄── send_task(process_analysis_result)
                           stt_segments INSERT
                           page_summaries INSERT
                           status = done
                           LLM 호출 → PDF 생성 → MinIO 저장
```

---

## API 명세 요약

| ID | 메서드 | URL | 설명 | 방식 |
|----|--------|-----|------|------|
| CL-1 | POST | `/api/v1/sessions` | 세션 생성 | 동기 |
| CL-2 | POST | `/api/v1/sessions/{id}/calibrate/presigned-url` | 캘리브레이션 URL 발급 | 동기 |
| CL-3 | PUT | `{presigned_url}` | 캘리브레이션 영상 업로드 | 동기 |
| CL-4 | POST | `/api/v1/sessions/{id}/calibrate/start` | 캘리브레이션 분석 시작 | **동기 (완료 대기)** |
| CL-5 | POST | `/api/v1/sessions/{id}/metadata` | 메타데이터 전송 | 동기 |
| CL-6 | POST | `/api/v1/sessions/{id}/presigned-url` | 녹화 영상 URL 발급 | 동기 |
| CL-7 | PUT | `{presigned_url}` | 녹화 영상 업로드 | 동기 |
| CL-8 | POST | `/api/v1/sessions/{id}/analyze` | 본 분석 시작 | **즉시 반환** |

### sessions.status 전이

```
uploaded → analyzing → calibrated → analyzing → done
                     → calib_failed             → failed
                     → error
```

### MinIO 경로 규칙

| 파일 | 경로 |
|------|------|
| 본 녹화 영상 | `sessions/session_{id}/recording.mp4` |
| 캘리브레이션 영상 | `sessions/session_{id}/calibration_{n}.mp4` |
| 페이지 스크린샷 | `sessions/session_{id}/screenshot_{page_no}.png` |
| 시선·표정 JSON | `sessions/session_{id}/detail.json` |
| 시선 히트맵 | `sessions/session_{id}/heatmap_page_{page_no}.png` |
| PDF 보고서 | `sessions/session_{id}/report.pdf` |

---

## DB 스키마

| 테이블 | 역할 | 저장 주체 |
|--------|------|-----------|
| `sessions` | 세션 상태 관리 | 메인 서버 |
| `calibration_points` | 캘리브레이션 화면 좌표 + MinIO 경로 | 메인 서버 (CL-2 수신 시) |
| `calibrations` | AI가 추출한 홍채 좌표 | 메인 서버 (큐 수신 후) |
| `page_logs` | 페이지별 URL / 체류시간 / 스크린샷 경로 | 메인 서버 (CL-5 수신 시) |
| `task_results` | 태스크 완료/실패 (`"success"` \| `"fail"`) | 메인 서버 (CL-5 수신 시) |
| `stt_segments` | Whisper STT 발화 구간 | 메인 서버 (큐 수신 후) |
| `page_summaries` | 페이지별 혼란도 / 감정 / 시선 분석 요약 | 메인 서버 (큐 수신 후) |
| `reports` | LLM 텍스트 + PDF 경로 + 상태 | 메인 서버 (LLM/PDF 완료 후) |

---

## AI 분석 상세

### 표정 분석
- 모델: EfficientNet-B2 + Transformer Encoder (CK+ 학습)
- 입력: 16프레임 배치
- 출력: `neutral` / `negative` / `positive` / `confusion` / `surprise` + 확신도
- VRAM: ~4GB per Worker

### 시선 분석
- 홍채 추출: MediaPipe FaceLandmarker 0.10.x (랜드마크 473/468)
- 캘리브레이션 매핑: LinearNDInterpolator + NearestNDInterpolator (외삽 fallback)
- 출력: 화면 비율 좌표 (0.0~1.0), 시선 이탈 여부
- 히트맵: 페이지별 `heatmap_page_{n}.png` 생성

### 음성 분석
- 모델: Whisper medium (한국어)
- VAD: `no_speech_prob > 0.8` 구간 필터
- 출력: `start_ts`, `end_ts`, `text`, `silence_sec`
- VRAM: ~2GB per Worker

### 혼란도 산출

```
CI = 0.25 × 부정감정비율
   + 0.25 × 시선이탈비율
   + 0.15 × 발화공백
   + 0.10 × 발화속도급변
   + 0.15 × 체류시간이상치
   + 0.10 × 태스크실패율

스무딩: 30프레임 롤링 평균
출력: 페이지별 confusion_avg (0.0~1.0)
      태스크별 task_confusion_json {"1":0.45, "2":0.81}
```

### Worker 구성
- Worker 수: 4개 (VRAM 합산 ~26GB / RTX 5090 32GB)
- 3종 분석: `ThreadPoolExecutor(max_workers=3)` 병렬 실행

---

## 기술 스택

| 구분 | 기술 |
|------|------|
| 클라이언트 | PySide6 6.11, OpenCV, mss, sounddevice, requests |
| 메인 서버 | FastAPI 0.136, SQLAlchemy 2.0 (async), Celery 5.4, MinIO |
| AI 서버 | Celery 5.4, PyTorch, EfficientNet (timm 0.9), MediaPipe 0.10, Whisper medium, scipy |
| DB | MariaDB 10.11 |
| 스토리지 | MinIO |
| 메시지 큐 | Redis 7 + Celery |
| LLM | Groq API |
| PDF | WeasyPrint + Markdown |
| GPU | NVIDIA RTX 5090 (32GB VRAM) |
| Python | 3.12 (서버 / AI), 3.13 (클라이언트) |
