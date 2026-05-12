import asyncio
import time
import httpx
from minio import Minio

# 설정값
CONCURRENT_USERS = 30
FILE_PATH = "dummy_50mb.mp4"

# 1. Before: 가상의 FastAPI 직접 수신 엔드포인트로 쏘기
async def test_before_direct_upload():
    print(f"\\n[Before] AI 서버 직접 전송 테스트 시작 ({CONCURRENT_USERS}명 동시)...")
    async with httpx.AsyncClient(timeout=60.0) as client:
        start_time = time.time()
        
        async def upload_task(user_id):
            try:
                with open(FILE_PATH, "rb") as f:
                    # AI 서버의 가상 수신 엔드포인트 가정 (실제 AI 서버 업로드 주소에 맞게 변경 가능)
                    resp = await client.post(
                        "http://10.10.10.128:8001/api/upload", 
                        files={"file": f}
                    )
                return resp.status_code
            except Exception as e:
                return str(e)
                
        tasks = [upload_task(i) for i in range(CONCURRENT_USERS)]
        results = await asyncio.gather(*tasks)
        
        elapsed = time.time() - start_time
        errors = [r for r in results if r != 200]
        
        print(f"✅ 총 소요 시간: {elapsed:.2f}초")
        print(f"❌ 실패/타임아웃 발생 건수: {len(errors)}건")

# 2. After: MinIO 클라이언트를 통한 직접 업로드
def upload_to_minio_task(user_id):
    client = Minio("10.10.10.113:9000", access_key="minioadmin", secret_key="minioadmin", secure=False)
    try:
        client.fput_object("ut-platform", f"test/dummy_{user_id}.mp4", FILE_PATH)
        return 200
    except Exception as e:
        return str(e)

async def test_after_minio_upload():
    print(f"\\n[After] MinIO 분산 업로드 테스트 시작 ({CONCURRENT_USERS}명 동시)...")
    start_time = time.time()
    
    # MinIO 업로드는 동기 라이브러리를 쓰므로 스레드풀로 병렬 실행
    results = await asyncio.gather(*[
        asyncio.to_thread(upload_to_minio_task, i) for i in range(CONCURRENT_USERS)
    ])
    
    elapsed = time.time() - start_time
    errors = [r for r in results if r != 200]
    
    print(f"✅ 총 소요 시간: {elapsed:.2f}초")
    print(f"❌ 실패/타임아웃 발생 건수: {len(errors)}건")

if __name__ == "__main__":
    asyncio.run(test_before_direct_upload())
    asyncio.run(test_after_minio_upload())
