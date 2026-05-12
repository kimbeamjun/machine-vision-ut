# UDT : UT 자동화 AI (Main Server)

본 프로젝트는 **UT(사용성 테스트, Usability Test)를 자동화하고 AI로 분석하는 시스템의 메인 백엔드 서버**입니다. 
사용자의 행동 비디오 영상과 메타데이터(스크린샷, 캘리브레이션 데이터 등)를 수집하여 **MinIO 객체 스토리지**에 저장하고, 무거운 영상 분석 처리 및 LLM 리포트 생성 작업을 **Redis/Celery 기반의 분산 큐(Message Queue)** 아키텍처를 통해 비동기적으로 처리하여 서버 가용성을 극대화합니다.

## 🛠️ 기술 스택 (Tech Stack)
- **Web Framework**: FastAPI, Uvicorn
- **Database / ORM**: MySQL(MariaDB), SQLAlchemy (비동기 `aiomysql`)
- **Storage**: MinIO (S3 호환 객체 스토리지)
- **Message Queue**: Redis, Celery
- **AI & Document**: OpenAI/Gemini(LLM), ReportLab/PDFKit 등 (PDF 생성)
- **Language**: Python 3.10+

## 📁 주요 디렉토리 구조
```text
main_server/
├── main_server_app.py      # FastAPI 어플리케이션 진입점 및 생명주기(Lifespan) 관리
├── api_endpoints/          # API 라우터 (세션 관리, 웹훅 등)
│   ├── router_sessions.py  # 세션 생성, 메타데이터 수집, 분석 요청 및 리포트 조회 API
│   └── router_webhooks.py  # MinIO 영상 업로드 이벤트 수신용 웹훅 (DB에 파일 경로 갱신)
├── database_tables/        # SQLAlchemy ORM 모델 정의 (DB 스키마)
│   └── db_orm_models.py    # Session, PageLog, Report 등
├── api_data_formats/       # Pydantic을 이용한 요청/응답 검증(Schema)
├── app_settings/           # 데이터베이스 연결(DB Connection) 및 MinIO 설정
├── background_tasks/       # 비동기 백그라운드 작업 (Celery 워커)
│   ├── celery_app.py       # Celery 앱 인스턴스 및 라우팅 설정
│   ├── tasks.py            # AI 연산 결과 수신 및 DB 최종 저장 태스크
│   ├── llm_service.py      # LLM 분석을 통한 사용성 테스트(UT) 텍스트 리포트 생성 로직
│   └── pdf_service.py      # LLM 텍스트 결과를 기반으로 PDF 리포트 파일 생성 로직
└── requirements.txt        # 파이썬 패키지 의존성
```

## ✨ 핵심 기능 (Core Features)

### 1. 세션(Session) 기반 데이터 관리
- **`POST /sessions`**: 새로운 UT 세션을 생성하고 상태를 관리합니다.
- **`POST /sessions/{id}/presigned-url`**: 무거운 미디어 파일(영상, 스크린샷)을 API 서버를 거치지 않고 **MinIO에 직접 업로드(Direct Upload)**할 수 있도록 일회성 Presigned URL을 발급합니다. 서버 네트워크 부하를 최소화합니다.
- **`POST /sessions/{id}/metadata`**: 테스트 종료 후 페이지 이동 로그, 태스크(Task) 수행 결과 등을 DB에 저장합니다.

### 2. 고가용성 비동기 AI 분석 파이프라인 (Celery + Redis)
- **`POST /sessions/{id}/calibrate/start`**: 업로드된 캘리브레이션 영상 분석 작업을 AI 서버의 큐(`ai_tasks`)로 전달하여 비동기 처리합니다.
- **`POST /sessions/{id}/analyze`**: 본 세션 영상에 대한 무거운 AI 분석(STT, 감정 인식, 시선 추적 등) 작업을 큐에 밀어넣고 클라이언트에게 즉시 202 Accepted를 반환하여 API 스레드의 병목을 원천 방지합니다.
- **AI 연산 결과 수신 (`tasks.py`)**: AI 서버의 연산이 완료되면 메인 서버 큐(`main_tasks`)로 결과를 전달하고, 백그라운드 워커가 이를 데이터베이스에 안전하게 기록합니다.

### 3. LLM 자동화 분석 및 PDF 리포트 생성
- 통합 데이터(STT, 시선 데이터, 표정 감정, 태스크 지표 등)가 수집되면 메인 서버 워커가 자동으로 **LLM에 분석 데이터를 주입하여 인사이트가 담긴 상세 텍스트 리포트를 작성**합니다.
- 작성된 LLM 리포트를 시각적인 **최종 PDF 파일**로 포맷팅하여 생성한 후, 클라이언트 배포를 위해 MinIO 객체 스토리지에 업로드합니다.
- **`GET /sessions/{id}/report`**: 완성된 PDF 리포트 파일의 경로와 텍스트 분석 결과를 클라이언트에 제공합니다.

### 4. MinIO 웹훅 (Webhook) 연동
- **`POST /webhook/video-upload`**: MinIO에 실제 세션 비디오 파일의 생성이 정상적으로 완료되면 MinIO 자체 서버에서 웹훅을 트리거합니다. 메인 서버는 객체의 파일 경로(`object_key`)를 가로채어 DB 레코드와 동기화합니다.

## 💾 데이터베이스 스키마 (ERD Overview)
- **SessionModel**: 전체 사용성 테스트(UT) 세션 메타정보
- **CalibrationPointModel / CalibrationModel**: MinIO 캘리브레이션 파일 경로 및 추출된 시선 보정 좌표
- **PageLogModel & PageSummaryModel**: 페이지 단위 이동 이벤트 로그 및 페이지별 AI 분석 통계 지표
- **TaskResultModel**: UT의 개별 태스크 수행 결과 (성공 여부, 소요 시간)
- **SttSegmentModel**: 음성 인식(STT)을 통한 유저 발화 스크립트 및 침묵 구간 데이터
- **ReportModel**: 최종 AI 분석 상태 및 파이프라인 추적, 생성된 LLM 분석 전문, 최종 PDF 파일 매핑 경로

## 🚀 실행 방법 (How to Run)

### 1. 환경 설정 및 스토리지(MinIO/Redis/MySQL) 준비
데이터베이스, 메시지 브로커, 오브젝트 스토리지가 모두 구동되어 있어야 합니다.
MinIO 스토리지는 프로젝트 외부 디렉토리에서 다음과 같이 실행할 수 있습니다:
```bash
./minio server ./data
```

### 2. 패키지 설치
```bash
python -m venv venv
source venv/bin/activate  # Windows의 경우 venv\Scripts\activate
pip install -r requirements.txt
```

### 3. 서버 실행
```bash
uvicorn main_server_app:app --host 0.0.0.0 --port 8000 --reload
```
서버 구동 후 `http://localhost:8000/docs` 경로에서 Swagger UI를 통해 모든 엔드포인트를 확인 및 테스트할 수 있습니다.

### 4. Celery 워커 실행 (백그라운드 처리용)
큐에 적재된 분석 결과 저장 및 PDF 생성 로직을 수행할 워커 프로세스를 구동합니다.
```bash
celery -A background_tasks.celery_app worker -Q main_tasks -l info
```
