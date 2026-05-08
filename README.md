# UDT : UT 자동화 AI (Main Server)

본 프로젝트는 **UT(사용성 테스트, Usability Test)를 자동화하고 AI로 분석하는 시스템의 메인 백엔드 서버**입니다. 
사용자의 행동 비디오 영상과 스크린샷, 캘리브레이션 데이터 등을 수집하여 MinIO 객체 스토리지에 저장하고, 업로드 이벤트를 웹훅(Webhook)으로 감지하여 AI 분석 프로세스를 트리거합니다.

## 🛠️ 기술 스택 (Tech Stack)
- **Web Framework**: FastAPI, Uvicorn
- **Database / ORM**: MySQL, SQLAlchemy (비동기 `aiomysql`)
- **Storage**: MinIO (S3 호환 객체 스토리지)
- **Language**: Python 3.10+

## 📁 주요 디렉토리 구조
```text
main_server/
├── main_server_app.py      # FastAPI 어플리케이션 진입점 및 생명주기(Lifespan) 관리
├── api_endpoints/          # API 라우터 (세션 관리, 웹훅 등)
│   ├── router_sessions.py  # 세션 생성, 메타데이터 수집, 리포트 조회 API
│   └── router_webhooks.py  # MinIO 영상 업로드 이벤트 수신용 웹훅
├── database_tables/        # SQLAlchemy ORM 모델 정의 (DB 스키마)
│   └── db_orm_models.py    # Session, PageLog, Report 등의 테이블 정의
├── api_data_formats/       # Pydantic을 이용한 요청/응답 검증(Schema) 스크립트
├── app_settings/           # 데이터베이스 연결(DB Connection) 및 MinIO 초기 설정
└── requirements.txt        # 파이썬 패키지 의존성
```

## ✨ 핵심 기능 (Core Features)

### 1. 세션(Session) 기반 데이터 관리
- **`POST /sessions`**: 새로운 UT 세션을 생성하고 상태를 관리합니다.
- **`POST /sessions/presigned-url`**: 클라이언트가 서버를 거치지 않고 MinIO에 직접 영상이나 이미지를 안전하게 업로드할 수 있도록 임시 발급 URL(Presigned URL)을 제공합니다.
- **`POST /sessions/{id}/metadata`**: 페이지 이동 로그, 태스크(Task) 수행 결과 등의 메타데이터를 저장합니다.
- **`POST /sessions/{id}/calibrate`**: 아이트래킹(시선 추적)을 위한 5개의 캘리브레이션 포인트 데이터를 수집 및 분석 큐에 등록합니다.

### 2. MinIO 이벤트 기반 자동 분석 파이프라인 (Webhook)
- **`POST /webhook/minio`**: 클라이언트가 비디오 영상 업로드를 완료하면 MinIO 서버에서 해당 웹훅을 트리거합니다. 
- 이벤트를 감지하면 DB의 세션 상태를 `analyzing`으로 변경하고, 보고서(`ReportModel`) 생성을 시작합니다.

### 3. AI 리포트 조회
- **`GET /sessions/{id}/report`**: 분석이 완료된 후 생성된 PDF 리포트 파일의 경로와 LLM이 요약한 텍스트 결과를 클라이언트에 반환합니다.

## 💾 데이터베이스 스키마 (ERD Overview)
- **SessionModel**: 전체 테스트 세션 정보를 담는 최상위 테이블
- **CalibrationModel**: 시선 추적을 위한 캘리브레이션 좌표 (Screen vs Gaze)
- **PageLogModel & PageSummaryModel**: 페이지 단위 이동 로그 및 페이지별 AI 분석(감정, 시선 이탈율, 정적 시간 등) 요약본
- **TaskResultModel**: UT의 개별 태스크 수행 결과 (성공 여부, 소요 시간)
- **SttSegmentModel**: 음성 인식(STT) 분석 결과 세그먼트
- **ReportModel**: 최종 AI 분석 결과 및 PDF 파일 정보 매핑

## 🚀 실행 방법 (How to Run)

### 1. 환경 설정 및 스토리지(MinIO) 준비
MinIO 서버와 MySQL 데이터베이스가 실행 중이어야 합니다. 
MinIO 서버는 프로젝트 폴더 외부에서 다음과 같이 실행할 수 있습니다.
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
서버가 정상적으로 실행되면 `http://localhost:8000/docs` 에 접속하여 Swagger UI를 통해 모든 API를 테스트해 볼 수 있습니다.



-----------

현재 구현된 기능 : MinIO - 메인서버 - db 연결

구현 예정 : Redis, Celery, LLM, PDF 관련 기능
