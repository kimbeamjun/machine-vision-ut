from fastapi import APIRouter, HTTPException, status, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.dialects.mysql import insert
from datetime import timedelta
import uuid

from app_settings.db_connection import get_db
from database_tables.db_orm_models import SessionModel, CalibrationModel, PageLogModel, TaskResultModel, ReportModel, CalibrationPointModel
from api_data_formats.api_request_schemas import SessionCreateReq, PresignedUrlReq, CalibPresignedUrlReq, MetadataReq
from app_settings.storage_minio import minio_client, BUCKET_NAME
from background_tasks.celery_app import app as celery_app
import asyncio

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
    return {"session_id": new_session.id}

@router.post("/{id}/calibrate/presigned-url", status_code=status.HTTP_200_OK)
async def get_calibration_presigned_url(id: int, req: CalibPresignedUrlReq, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(SessionModel).where(SessionModel.id == id))
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="해당 session_id를 찾을 수 없습니다.")
    
    try:
        object_key = f"sessions/session_{id}/calibration_{req.point_no}.mp4"
        
        url = minio_client.presigned_put_object(
            BUCKET_NAME,
            object_key,
            expires=timedelta(seconds=3600)
        )
        
        # ON DUPLICATE KEY UPDATE (Upsert)
        stmt = insert(CalibrationPointModel).values(
            session_id=id,
            point_no=req.point_no,
            screen_x=req.screen_x,
            screen_y=req.screen_y,
            object_key=object_key
        )
        stmt = stmt.on_duplicate_key_update(
            screen_x=stmt.inserted.screen_x,
            screen_y=stmt.inserted.screen_y,
            object_key=stmt.inserted.object_key
        )
        await db.execute(stmt)
        await db.commit()
        
        return {
            "presigned_url": url,
            "object_key": object_key
        }
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"MinIO/DB 연결 오류: {str(e)}")

@router.post("/{id}/presigned-url", status_code=status.HTTP_200_OK)
async def get_presigned_url(id: int, req: PresignedUrlReq, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(SessionModel).where(SessionModel.id == id))
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="해당 session_id를 찾을 수 없습니다.")
    
    try:
        if req.file_type == "recording":
            object_key = f"sessions/session_{id}/recording.mp4"
        else:
            ext = "png" if req.file_type in ["screenshot"] else "mp4"
            unique_filename = f"{uuid.uuid4().hex[:8]}.{ext}"
            folder_name = f"{req.file_type}s"
            object_key = f"{folder_name}/session_{id}/{unique_filename}"
            
        url = minio_client.presigned_put_object(
            BUCKET_NAME,
            object_key,
            expires=timedelta(seconds=3600)
        )
        return {
            "presigned_url": url,
            "object_key": object_key
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"MinIO 연결 오류: {str(e)}")

@router.post("/{id}/calibrate/start", status_code=status.HTTP_200_OK)
async def start_calibration(id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(SessionModel).where(SessionModel.id == id))
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="해당 session_id를 찾을 수 없습니다.")
    
    # Fetch calibration points
    calib_result = await db.execute(select(CalibrationPointModel).where(CalibrationPointModel.session_id == id))
    points = calib_result.scalars().all()
    
    points_list = []
    for p in points:
        points_list.append({
            "point_no": p.point_no,
            "screen_x": p.screen_x,
            "screen_y": p.screen_y,
            "video_object_key": p.object_key
        })
        
    print(f"[FASTAPI] Session {id}: 캘리브레이션 분석 작업 큐(Queue A) 적재")
    celery_app.send_task(
        "celery_app.analyze_calibration", 
        args=[id], 
        kwargs={"calibration_points": points_list}
    )
    
    # 동기 대기 로직 (최대 5분 대기)
    print(f"[FASTAPI] Session {id}: 캘리브레이션 분석 결과 대기 중...")
    max_wait_sec = 300
    for _ in range(max_wait_sec):
        await db.commit()  # DB 트랜잭션을 갱신하여 최신 상태를 읽어올 수 있도록 함
        await db.refresh(session)
        if session.status == "calibrated":
            return {"status": "success"}
        elif session.status == "calib_failed":
            # 실제로는 failed_points를 돌려줘야 하나, 현재 모델에 필드가 없으므로 생략 또는 추가 쿼리 필요
            return {"status": "failed", "failed_points": []} 
        elif session.status == "error":
            return {"status": "error"}
        await asyncio.sleep(1)
        
    raise HTTPException(status_code=504, detail="AI 서버 응답 시간 초과")

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

@router.post("/{id}/analyze", status_code=status.HTTP_202_ACCEPTED)
async def analyze_session(id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(SessionModel).where(SessionModel.id == id))
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="해당 session_id를 찾을 수 없습니다.")
        
    # 데이터 조회 (DB에서 수집된 모든 메타데이터를 끌어모음)
    calib_res = await db.execute(select(CalibrationModel).where(CalibrationModel.session_id == id))
    calibrations = [{"point_no": c.point_no, "screen_x": c.screen_x, "screen_y": c.screen_y, "gaze_x": c.gaze_x, "gaze_y": c.gaze_y} for c in calib_res.scalars().all()]
    
    page_logs_res = await db.execute(select(PageLogModel).where(PageLogModel.session_id == id))
    page_logs = [{"page_no": p.page_no, "url": p.url, "start_video_ts": p.start_video_ts, "end_video_ts": p.end_video_ts, "screenshot_path": p.screenshot_path} for p in page_logs_res.scalars().all()]
    
    task_res = await db.execute(select(TaskResultModel).where(TaskResultModel.session_id == id))
    task_results = [{"task_order": t.task_order, "result": t.result, "duration_sec": t.duration_sec} for t in task_res.scalars().all()]
    
    kwargs = {
        "video_path": session.video_path,
        "viewport_region": session.viewport_region,
        "calibrations": calibrations,
        "page_logs": page_logs,
        "task_results": task_results
    }
    
    celery_app.send_task(
        "celery_app.analyze_session", 
        args=[id], 
        kwargs=kwargs
    )
    session.status = "analyzing"
    
    res_rep = await db.execute(select(ReportModel).where(ReportModel.session_id == id))
    report = res_rep.scalar_one_or_none()
    if not report:
        report = ReportModel(session_id=id, status="generating")
        db.add(report)
    else:
        report.status = "generating"
        
    await db.commit()
    print(f"[FASTAPI] Session {id}: 본 분석 작업 큐(Queue A) 적재 완료")
    
    return {"status": "accepted"}

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
