# tasks.py
import asyncio
from background_tasks.celery_app import app
from sqlalchemy import select, delete
from sqlalchemy.dialects.mysql import insert
from app_settings.db_connection import DATABASE_URL
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool

# Celery 전용 비동기 엔진 (이벤트 루프 충돌 방지를 위해 NullPool 사용)
celery_engine = create_async_engine(DATABASE_URL, poolclass=NullPool)
CeleryAsyncSessionLocal = async_sessionmaker(bind=celery_engine, expire_on_commit=False)

from database_tables.db_orm_models import SessionModel, CalibrationModel, PageSummaryModel, SttSegmentModel, ReportModel, TaskResultModel

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
            session.failed_points = failed_points
        else:
            session.status = "calibrated"
            session.failed_points = failed_points
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

@app.task(bind=True, name="tasks.process_calibration_result")
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
            # 중복 키 에러(IntegrityError) 방지: 재시도 등으로 이미 데이터가 있을 경우 기존 데이터 삭제
            await db.execute(delete(SttSegmentModel).where(SttSegmentModel.session_id == session_id))
            await db.execute(delete(PageSummaryModel).where(PageSummaryModel.session_id == session_id))
            
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
            await db.commit()
            print(f"✅ [SUCCESS] Session {session_id}: 본 분석 결과(페이지, STT) DB 저장 완료 (status: {session.status})")
            
            # ----------------------------------------------------
            # LLM 분석 및 텍스트 리포트 생성 로직
            # ----------------------------------------------------
            from background_tasks.llm_service import generate_ut_report_llm
            res_tasks = await db.execute(select(TaskResultModel).where(TaskResultModel.session_id == session_id))
            task_results_orm = res_tasks.scalars().all()
            
            res_pages = await db.execute(select(PageSummaryModel).where(PageSummaryModel.session_id == session_id))
            page_summaries_orm = res_pages.scalars().all()
            
            res_stt = await db.execute(select(SttSegmentModel).where(SttSegmentModel.session_id == session_id))
            stt_segments_orm = res_stt.scalars().all()
            
            res_rep = await db.execute(select(ReportModel).where(ReportModel.session_id == session_id))
            report = res_rep.scalar_one_or_none()
            
            if report:
                try:
                    llm_text = await generate_ut_report_llm(task_results_orm, page_summaries_orm, stt_segments_orm)
                    report.llm_text = llm_text
                    
                    # PDF 생성 및 업로드
                    from background_tasks.pdf_service import generate_and_upload_pdf
                    import asyncio
                    
                    pdf_path = await asyncio.to_thread(
                        generate_and_upload_pdf,
                        session_id,
                        llm_text,
                        page_summaries_orm
                    )
                    
                    report.pdf_path = pdf_path
                    report.status = "done"
                    await db.commit()
                    print(f"✅ [SUCCESS] Session {session_id}: LLM 리포트 및 PDF 생성 완료")
                except Exception as llm_e:
                    print(f"[CELERY] [ERROR] LLM Report generation failed: {llm_e}")
                    report.status = "failed"
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

@app.task(bind=True, name="tasks.process_analysis_result")
def process_analysis_result(self, payload):
    print(f"[CELERY] [START] process_analysis_result for session_id={payload.get('session_id')}")
    asyncio.run(_process_analysis_result_async(payload))
    print(f"[CELERY] [DONE] process_analysis_result completed.")
    return {'status': 'success'}