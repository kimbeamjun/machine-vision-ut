# celery_app.py
# Celery Worker — 캘리브레이션 분석 + 본 분석 파이프라인

import os
import json
import tempfile
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed

import redis
from celery import Celery

from config import (
    CELERY_BROKER, CELERY_BACKEND,
    REDIS_HOST, REDIS_PORT_B,
)
from db import (
    load_session, load_calibrations, load_page_logs, load_task_results,
    save_page_summaries, update_session_status, update_report_status,
)
from minio_client import download_video, download_calibration_video, delete_object

app = Celery("ai_server", broker=CELERY_BROKER, backend=CELERY_BACKEND)
app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_reject_on_worker_lost=True,
    task_routes={
        "celery_app.analyze_calibration": {"queue": "ai_queue"},
        "celery_app.analyze_session":     {"queue": "ai_queue"},
    },
)

_redis_b = redis.Redis(host=REDIS_HOST, port=REDIS_PORT_B, db=0, decode_responses=False)


def _push_result_to_queue_b(payload: dict):
    _redis_b.rpush("result_queue", json.dumps(payload, ensure_ascii=False))


# ── 프로세스 풀 래퍼 함수 ────────────────────────────────────────
def _run_emotion(args):
    video_path, session_id = args
    from emotion_analysis import run_emotion_analysis
    return run_emotion_analysis(video_path, session_id)


def _run_gaze(args):
    video_path, session_id, calibrations, viewport_region, page_logs = args
    from gaze_analysis import run_gaze_analysis
    return run_gaze_analysis(video_path, session_id, calibrations, viewport_region, page_logs)


def _run_whisper(args):
    video_path, session_id = args
    from whisper_analysis import run_whisper_analysis
    return run_whisper_analysis(video_path, session_id)


# ══════════════════════════════════════════════════════════════
# [태스크 1] 캘리브레이션 분석
# 메인 서버가 캘리브레이션 영상 5개 업로드 완료 후 호출
# ══════════════════════════════════════════════════════════════
@app.task(
    name="celery_app.analyze_calibration",
    bind=True,
    max_retries=0,
    queue="ai_queue",
    acks_late=True,
)
def analyze_calibration(self, session_id: int, calibration_points: list[dict]):
    """
    캘리브레이션 영상 분석 태스크

    Args:
        session_id: 세션 ID
        calibration_points: [
            {
                "point_no": 1,
                "screen_x": 0.1,
                "screen_y": 0.1,
                "video_object_key": "calibrations/session_1/point_1.mp4"
            }, ...
        ]
    """
    print(f"[Calibration Worker] session_id={session_id} 캘리브레이션 분석 시작")

    tmp_paths = []
    calibration_videos = []

    try:
        # MinIO에서 캘리브레이션 영상 5개 다운로드
        for point in calibration_points:
            tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
            tmp.close()
            tmp_paths.append(tmp.name)

            download_calibration_video(point["video_object_key"], tmp.name)
            calibration_videos.append({
                "point_no":  point["point_no"],
                "screen_x":  point["screen_x"],
                "screen_y":  point["screen_y"],
                "local_path": tmp.name,
            })

        # 캘리브레이션 분석 실행
        from calibration_analysis import run_calibration_analysis
        result = run_calibration_analysis(session_id, calibration_videos)

        # 결과를 큐 B로 메인 서버에 전달
        _push_result_to_queue_b({
            "type":           "calibration_result",
            "session_id":     session_id,
            "success":        result["success"],
            "failed_points":  result["failed_points"],
        })

        print(f"[Calibration Worker] session_id={session_id} 완료 "
              f"(실패 포인트: {result['failed_points']})")

    except Exception as e:
        print(f"[Calibration Worker] session_id={session_id} 오류: {e}")
        traceback.print_exc()
        _push_result_to_queue_b({
            "type":       "calibration_result",
            "session_id": session_id,
            "success":    False,
            "error":      str(e),
        })

    finally:
        for path in tmp_paths:
            if os.path.exists(path):
                os.remove(path)


# ══════════════════════════════════════════════════════════════
# [태스크 2] 본 분석 파이프라인 (기존과 동일)
# ══════════════════════════════════════════════════════════════
@app.task(
    name="celery_app.analyze_session",
    bind=True,
    max_retries=0,
    queue="ai_queue",
    acks_late=True,
)
def analyze_session(self, session_id: int):
    """
    본 분석 태스크 — 캘리브레이션 완료 후 메인 서버가 호출
    """
    print(f"[Worker] session_id={session_id} 분석 시작")
    update_session_status(session_id, "analyzing")

    tmp_video = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
    tmp_video.close()
    video_local_path = tmp_video.name

    try:
        # DB 메타데이터 로드
        session      = load_session(session_id)
        calibrations = load_calibrations(session_id)   # AI 서버가 저장한 캘리브레이션
        page_logs    = load_page_logs(session_id)
        task_results = load_task_results(session_id)

        video_path      = session["video_path"]
        viewport_region = session["viewport_region"]

        if isinstance(viewport_region, str):
            import json as _json
            viewport_region = _json.loads(viewport_region)

        # MinIO에서 본 영상 다운로드
        download_video(video_path, video_local_path)
        print(f"[Worker] session_id={session_id} 영상 다운로드 완료")

        # 3종 병렬 분석
        emotion_result = None
        gaze_result    = None
        whisper_result = None

        with ProcessPoolExecutor(max_workers=3) as executor:
            future_emotion = executor.submit(_run_emotion, (video_local_path, session_id))
            future_gaze    = executor.submit(_run_gaze, (video_local_path, session_id,
                                             calibrations, viewport_region, page_logs))
            future_whisper = executor.submit(_run_whisper, (video_local_path, session_id))

            futures = {
                future_emotion: "emotion",
                future_gaze:    "gaze",
                future_whisper: "whisper",
            }
            for future in as_completed(futures):
                name = futures[future]
                result = future.result()
                if name == "emotion":
                    emotion_result = result
                elif name == "gaze":
                    gaze_result = result
                elif name == "whisper":
                    whisper_result = result
                print(f"[Worker] session_id={session_id} {name} 완료")

        # 혼란도 점수 산출
        from confusion_index import calc_confusion_index
        summaries = calc_confusion_index(
            frame_emotions=emotion_result["frame_emotions"],
            gaze_points=gaze_result["gaze_points"],
            gaze_escape_ratio_per_page=gaze_result["gaze_escape_ratio_per_page"],
            stt_segments=whisper_result["stt_segments"],
            page_logs=page_logs,
            task_results=task_results,
            heatmap_paths=gaze_result["heatmap_paths"],
            emotion_detail_json_path=emotion_result["detail_json_path"],
            gaze_detail_json_path=gaze_result["detail_json_path"],
        )

        # DB 저장
        save_page_summaries(session_id, summaries)
        update_session_status(session_id, "done")

        # 큐 B 적재
        _push_result_to_queue_b({
            "type":           "analysis_result",
            "session_id":     session_id,
            "stt_segments":   whisper_result["stt_segments"],
            "page_summaries": summaries,
            "skipped_stt":    whisper_result.get("skipped", False),
        })
        print(f"[Worker] session_id={session_id} 결과 큐 B 적재 완료")

        # MinIO 영상 즉시 삭제
        delete_object(video_path)
        print(f"[Worker] session_id={session_id} 영상 삭제 완료")

    except Exception as e:
        print(f"[Worker] session_id={session_id} 오류: {e}")
        traceback.print_exc()
        update_session_status(session_id, "failed")
        update_report_status(session_id, "failed")
        raise self.reject(requeue=False)

    finally:
        if os.path.exists(video_local_path):
            os.remove(video_local_path)
