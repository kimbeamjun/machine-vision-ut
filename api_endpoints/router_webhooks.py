from fastapi import APIRouter, status, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import re
import urllib.parse

from app_settings.db_connection import get_db
from database_tables.db_orm_models import SessionModel, ReportModel

router = APIRouter(prefix="/webhook", tags=["Webhooks"])

@router.post("/minio", status_code=status.HTTP_200_OK)
async def minio_webhook(req: Request, db: AsyncSession = Depends(get_db)):
    try:
        payload = await req.json()
    except Exception:
        return {"status": "ignored", "reason": "invalid_json"}
    
    records = payload.get("Records", [])
    for record in records:
        event_name = record.get("eventName", "")
        
        if event_name.startswith("s3:ObjectCreated:"):
            object_key = record.get("s3", {}).get("object", {}).get("key", "")
            object_key = urllib.parse.unquote(object_key)
            print(f"👉 [Debug] 웹훅 수신됨 - 파일 경로: {object_key}")
            
            match = re.search(r"session_(\d+)", object_key)
            if match and object_key.startswith("videos/"):
                session_id = int(match.group(1))
                
                result = await db.execute(select(SessionModel).where(SessionModel.id == session_id))
                session = result.scalar_one_or_none()
                if session:
                    session.video_path = object_key
                    session.status = "analyzing"
                    
                    res_rep = await db.execute(select(ReportModel).where(ReportModel.session_id == session_id))
                    report = res_rep.scalar_one_or_none()
                    if not report:
                        report = ReportModel(session_id=session_id, status="generating")
                        db.add(report)
                    else:
                        report.status = "generating"
                        
                    await db.commit()
                    print(f"✅ [WEBHOOK] Session {session_id}: 영상 업로드 감지 -> 분석 작업 큐 등록 완료 (Mocking)")
                else:
                    print(f"❌ [Debug] DB에서 session_id={session_id} 를 찾을 수 없습니다. (먼저 POST /sessions 로 생성 필요)")
            else:
                print(f"⚠️ [Debug] 조건 불일치 무시됨: 경로가 'videos/' 로 시작하지 않거나 'session_숫자' 패턴이 없습니다.")
                    
    return {"status": "processed"}
