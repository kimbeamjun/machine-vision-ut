# tasks.py
import asyncio
from celery_app import app
from sqlalchemy import select
from sqlalchemy.dialects.mysql import insert
from app_settings.db_connection import DATABASE_URL
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool

# Celery 전용 비동기 엔진 (이벤트 루프 충돌 방지를 위해 NullPool 사용)
celery_engine = create_async_engine(DATABASE_URL, poolclass=NullPool)
CeleryAsyncSessionLocal = async_sessionmaker(bind=celery_engine, expire_on_commit=False)

from database_tables.db_orm_models import SessionModel, CalibrationModel, PageSummaryModel, SttSegmentModel, ReportModel

async def _process_calibration_result_async(payload):
    session_id = payload.get("session_id")
    success = payload.get("success", False)
    failed_points = payload.get("failed_points", [])
    calibrations = payload.get("calibrations", [])

    async with CeleryAsyncSessionLocal() as db:
        result = await db.execute(select(SessionModel).where(SessionModel.id == session_id))
        session = result.scalar_one_or_none()
        if not session:
            return

        if not success:
            session.status = "error"
        elif len(failed_points) >= 2:
            session.status = "calib_failed"
        else:
            session.status = "calibrated"
            for calib in calibrations:
                stmt = insert(CalibrationModel).values(
                    session_id=session_id,
                    point_no=calib["point_no"],
                    screen_x=calib["screen_x"],
                    screen_y=calib["screen_y"],
                    gaze_x=calib["gaze_x"],
                    gaze_y=calib["gaze_y"]
                ).on_duplicate_key_update(
                    gaze_x=calib["gaze_x"],
                    gaze_y=calib["gaze_y"]
                )
                await db.execute(stmt)
        await db.commit()
        print(f"✅ [SUCCESS] Session {session_id}: 캘리브레이션 결과 DB 저장 완료 (status: {session.status})")

@app.task(bind=True)
def process_calibration_result(self, payload):
    print(f"[CELERY] [START] process_calibration_result for session_id={payload.get('session_id')}")
    asyncio.run(_process_calibration_result_async(payload))
    print(f"[CELERY] [DONE] process_calibration_result completed.")
    return {'status': 'success'}

async def _process_analysis_result_async(payload):
    session_id = payload.get("session_id")
    stt_segments = payload.get("stt_segments", [])
    page_summaries = payload.get("page_summaries", [])
    skipped_stt = payload.get("skipped_stt", False)

    async with CeleryAsyncSessionLocal() as db:
        result = await db.execute(select(SessionModel).where(SessionModel.id == session_id))
        session = result.scalar_one_or_none()
        if not session:
            return

        try:
            if not skipped_stt:
                for stt in stt_segments:
                    db.add(SttSegmentModel(
                        session_id=session_id,
                        start_ts=stt["start_ts"],
                        end_ts=stt["end_ts"],
                        text=stt["text"],
                        silence_sec=stt.get("silence_sec", 0.0)
                    ))
            
            for page in page_summaries:
                db.add(PageSummaryModel(
                    session_id=session_id,
                    page_no=page["page_no"],
                    url=page.get("url"),
                    start_video_ts=page["start_video_ts"],
                    end_video_ts=page.get("end_video_ts"),
                    dominant_emotion=page.get("dominant_emotion"),
                    neg_ratio=page.get("neg_ratio"),
                    gaze_escape_ratio=page.get("gaze_escape_ratio"),
                    confusion_avg=page.get("confusion_avg"),
                    task_confusion_json=page.get("task_confusion_json"),
                    stt_summary=page.get("stt_summary"),
                    avg_silence_sec=page.get("avg_silence_sec"),
                    task_success_rate=page.get("task_success_rate"),
                    avg_task_duration_sec=page.get("avg_task_duration_sec"),
                    heatmap_path=page.get("heatmap_path"),
                    detail_json_path=page.get("detail_json_path")
                ))

            session.status = "done"
            
            # TODO: MS-5 (LLM API) and MS-6 (PDF) can be triggered here or as another Celery task
            
            await db.commit()
        except Exception as e:
            await db.rollback()
            session.status = "failed"
            res_rep = await db.execute(select(ReportModel).where(ReportModel.session_id == session_id))
            report = res_rep.scalar_one_or_none()
            if report:
                report.status = "failed"
            await db.commit()
            print(f"[CELERY] [ERROR] process_analysis_result failed: {e}")

@app.task(bind=True)
def process_analysis_result(self, payload):
    print(f"[CELERY] [START] process_analysis_result for session_id={payload.get('session_id')}")
    asyncio.run(_process_analysis_result_async(payload))
    print(f"[CELERY] [DONE] process_analysis_result completed.")
    return {'status': 'success'}