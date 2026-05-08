from fastapi import APIRouter, status, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import re
import urllib.parse
import json

from app_settings.db_connection import get_db
from database_tables.db_orm_models import SessionModel, ReportModel

router = APIRouter(prefix="/webhook", tags=["Webhooks"])

@router.post("/video-upload", status_code=status.HTTP_200_OK)
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
            size = record.get("s3", {}).get("object", {}).get("size", 0)
            
            print(f"[DEBUG] 웹훅 수신됨 - 이벤트: {event_name}, 파일 경로: {object_key}, 크기: {size} bytes")
            
            match = re.search(r"session_(\d+)", object_key)
            is_valid_video = (
                (object_key.startswith("videos/") or object_key.startswith("sessions/")) and
                ("calibration_" not in object_key) and
                (object_key.endswith(".mp4") or object_key.endswith(".webm"))
            )
            
            if match and is_valid_video:
                session_id = int(match.group(1))
                
                result = await db.execute(select(SessionModel).where(SessionModel.id == session_id))
                session = result.scalar_one_or_none()
                if session:
                    session.video_path = object_key
                    await db.commit()
                    print(f"[FASTAPI] [WEBHOOK] Session {session_id}: 영상 업로드 감지 -> 비디오 경로 저장 완료")
                else:
                    print(f"[DEBUG] DB에서 session_id={session_id} 를 찾을 수 없습니다. (먼저 POST /sessions 로 생성 필요)")
            else:
                print(f"[DEBUG] 조건 불일치 무시됨: {object_key} (비디오 파일 형식이 아니거나 캘리브레이션 영상입니다.)")
                    
    return {"status": "processed"}
