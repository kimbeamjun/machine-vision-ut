from fastapi import APIRouter, HTTPException, status, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import timedelta
import uuid

from app_settings.db_connection import get_db
from database_tables.db_orm_models import SessionModel, CalibrationModel, PageLogModel, TaskResultModel, ReportModel
from api_data_formats.api_request_schemas import SessionCreateReq, PresignedUrlReq, CalibrateReq, MetadataReq
from app_settings.storage_minio import minio_client, BUCKET_NAME

router = APIRouter(prefix="/sessions", tags=["Sessions"])

@router.post("", status_code=status.HTTP_200_OK)
async def create_session(req: SessionCreateReq, db: AsyncSession = Depends(get_db)):
    new_session = SessionModel(
        viewport_region=req.viewport_region.model_dump(),
        status="uploaded"
    )
    db.add(new_session)
    await db.commit()
    await db.refresh(new_session)
    return {"session_id": new_session.id, "status": new_session.status}

@router.post("/presigned-url", status_code=status.HTTP_200_OK)
async def get_presigned_url(req: PresignedUrlReq, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(SessionModel).where(SessionModel.id == req.session_id))
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="해당 session_id를 찾을 수 없습니다.")
    
    try:
        ext = "png" if req.file_type == "screenshot" else "mp4"
        unique_filename = f"{uuid.uuid4().hex[:8]}.{ext}"
        object_key = f"{req.file_type}s/session_{req.session_id}/{unique_filename}"
        
        url = minio_client.presigned_put_object(
            BUCKET_NAME,
            object_key,
            expires=timedelta(seconds=3600)
        )
        return {
            "presigned_url": url,
            "object_key": object_key,
            "expires_in": 3600
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"MinIO 연결 오류: {str(e)}")

@router.post("/{id}/calibrate", status_code=status.HTTP_202_ACCEPTED)
async def start_calibration(id: int, req: CalibrateReq, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(SessionModel).where(SessionModel.id == id))
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="해당 session_id를 찾을 수 없습니다.")
    
    if len(req.object_keys) != 5 or len(req.screen_xs) != 5 or len(req.screen_ys) != 5:
        raise HTTPException(status_code=400, detail="캘리브레이션 데이터는 5개여야 합니다.")
        
    print(f"Session {id}: 캘리브레이션 분석 작업 큐(Queue A) 등록 완료 (Mocking)")
    return {"message": "캘리브레이션 분석이 큐에 등록되었습니다.", "session_id": id}

@router.get("/{id}/calibrate/status")
async def get_calibration_status(id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(CalibrationModel).where(CalibrationModel.session_id == id))
    cals = result.scalars().all()
    
    if len(cals) == 5:
        return {"status": "done"}
    else:
        return {"status": "analyzing"}

@router.post("/{id}/metadata", status_code=status.HTTP_200_OK)
async def save_metadata(id: int, req: MetadataReq, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(SessionModel).where(SessionModel.id == id))
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="해당 session_id를 찾을 수 없습니다.")
    
    for log in req.page_logs:
        db.add(PageLogModel(
            session_id=id, page_no=log.page_no, url=log.url,
            start_video_ts=log.start_video_ts, end_video_ts=log.end_video_ts,
            screenshot_path=log.screenshot_path
        ))
        
    for task in req.task_results:
        db.add(TaskResultModel(
            session_id=id, task_order=task.task_order,
            result=task.result, duration_sec=task.duration_sec
        ))
        
    try:
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"데이터 저장 중 오류 발생: {str(e)}")
        
    return {"saved": True, "page_logs": len(req.page_logs), "task_results": len(req.task_results)}

@router.get("/{id}/report")
async def get_report(id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(ReportModel).where(ReportModel.session_id == id))
    report = result.scalar_one_or_none()
    
    if not report:
        raise HTTPException(status_code=400, detail="분석이 요청되지 않았습니다.")
        
    if report.status == "generating":
        return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content={"report_status": "generating"})
    elif report.status == "failed":
        return JSONResponse(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, content={"report_status": "failed", "error": "AI 분석 실패"})
    elif report.status == "done":
        return {"report_status": "done", "pdf_path": report.pdf_path, "llm_text": report.llm_text}
