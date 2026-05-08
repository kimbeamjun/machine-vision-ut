from fastapi import FastAPI
from contextlib import asynccontextmanager

from app_settings.db_connection import engine
from app_settings.storage_minio import minio_client, BUCKET_NAME
from api_endpoints import router_sessions, router_webhooks

@asynccontextmanager
async def lifespan(app: FastAPI):
    # MinIO 초기화
    try:
        if not minio_client.bucket_exists(BUCKET_NAME):
            minio_client.make_bucket(BUCKET_NAME)
        print("[CONNECTION] MinIO 연결 성공")
    except Exception as e:
        print(f"[CONNECTION ERROR] MinIO 초기화 중 오류 발생: {e}")

    # DB 커넥션 풀 확인
    try:
        from sqlalchemy import text
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync_conn: sync_conn.execute(text("SELECT 1")))
            print("[CONNECTION] 데이터베이스 커넥션 풀 연결 성공")
    except Exception as e:
        print(f"[CONNECTION ERROR] 데이터베이스 연결 실패. 서버는 계속 실행되나 기능이 제한될 수 있습니다: {e}")
        
    yield
    # 종료 시 DB 엔진 해제
    await engine.dispose()
    print("[CONNECTION] 데이터베이스 커넥션 풀 종료")

app = FastAPI(title="UDT : UT 자동화 AI", lifespan=lifespan)

# 라우터 조립
app.include_router(router_sessions.router, prefix="/api/v1")
app.include_router(router_webhooks.router)

if __name__ == "__main__":
    import uvicorn
    # uvicorn.run() 에서는 항상 파일명:app 을 문자열로 줘야 리로드가 잘 됨
    uvicorn.run("main_server_app:app", host="0.0.0.0", port=8000, reload=True)
