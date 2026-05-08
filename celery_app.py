# celery_app.py
# AI 서버 Celery Worker — 명세서 v5 (PDF) 기준 최종본
#
# AI-7: 큐B 송신 — 캘리브레이션 분석 결과 (calibrations 배열 포함)
# AI-8: 큐B 송신 — 본 분석 결과 (stt_segments + page_summaries)
# DB 접근 없음. MinIO 파일 삭제 없음. 로컬 임시파일만 삭제.

import os
import sys

# Celery prefork Worker는 별도 프로세스로 뜨므로
# 프로젝트 루트를 sys.path에 명시적으로 추가
_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import json
import tempfile
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed

import redis
from celery import Celery

from config import (
    CELERY_BROKER, CELERY_BACKEND,
    REDIS_HOST, REDIS_PORT_B, REDIS_PASSWORD_B,
)
from minio_client import download_video, download_calibration_video

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

_redis_b = redis.Redis(
    host=REDIS_HOST,
    port=REDIS_PORT_B,
    password=REDIS_PASSWORD_B if REDIS_PASSWORD_B else None,
    db=0,
    decode_responses=False,
)


def _push_result_to_queue_b(payload: dict):
    """큐B(result_queue)에 JSON 페이로드 적재"""
    _redis_b.rpush("result_queue", json.dumps(payload, ensure_ascii=False))


# ── ProcessPoolExecutor 래퍼 함수 ────────────────────────────────
# 별도 프로세스에서 실행되므로 sys.path 보정 필수

def _run_emotion(args):
    import sys, os
    _r = os.path.dirname(os.path.abspath(__file__))
    if _r not in sys.path:
        sys.path.insert(0, _r)
    video_path, session_id = args
    from emotion_analysis import run_emotion_analysis
    return run_emotion_analysis(video_path, session_id)


def _run_gaze(args):
    import sys, os
    _r = os.path.dirname(os.path.abspath(__file__))
    if _r not in sys.path:
        sys.path.insert(0, _r)
    video_path, session_id, calibrations, viewport_region, page_logs = args
    from gaze_analysis import run_gaze_analysis
    return run_gaze_analysis(video_path, session_id, calibrations, viewport_region, page_logs)


def _run_whisper(args):
    import sys, os
    _r = os.path.dirname(os.path.abspath(__file__))
    if _r not in sys.path:
        sys.path.insert(0, _r)
    video_path, session_id = args
    from whisper_analysis import run_whisper_analysis
    return run_whisper_analysis(video_path, session_id)


# ══════════════════════════════════════════════════════════════
# [AI-7] 캘리브레이션 분석 태스크
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
    캘리브레이션 영상 분석 (AI-1 + AI-7)

    Args:
        session_id        : 세션 ID
        calibration_points: 메인 서버가 DB calibration_points 조회 후 전달
            [
                {
                    "point_no":        1,
                    "screen_x":        0.1,
                    "screen_y":        0.1,
                    "video_object_key":"sessions/session_1/calibration_1.mp4"
                }, ... (총 5개)
            ]

    큐B 페이로드 (AI-7):
        성공: {type, session_id, success=true, failed_points, calibrations}
        오류: {type, session_id, success=false, error}

    ※ 큐B 적재 완료 후 로컬 임시파일 삭제. MinIO 파일 건드리지 않음.
    """
    print(f"[AI-7] session_id={session_id} 캘리브레이션 분석 시작")

    tmp_paths          = []
    calibration_videos = []

    try:
        # AI-1: MinIO에서 캘리브레이션 영상 다운로드 → 로컬 임시파일
        for point in calibration_points:
            tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
            tmp.close()
            tmp_paths.append(tmp.name)

            download_calibration_video(point["video_object_key"], tmp.name)
            calibration_videos.append({
                "point_no":   point["point_no"],
                "screen_x":   point["screen_x"],
                "screen_y":   point["screen_y"],
                "local_path": tmp.name,
            })

        # 홍채 좌표 추출
        import sys as _sys, os as _os
        _r = _os.path.dirname(_os.path.abspath(__file__))
        if _r not in _sys.path:
            _sys.path.insert(0, _r)
        from calibration_analysis import run_calibration_analysis

        result = run_calibration_analysis(session_id, calibration_videos)

        # AI-7: 큐B 적재 — calibrations 배열 포함 (성공한 포인트만)
        # 메인 서버(MS-3)가 수신 후 calibrations 테이블 INSERT
        _push_result_to_queue_b({
            "type":          "calibration_result",
            "session_id":    session_id,
            "success":       result["success"],
            "failed_points": result["failed_points"],
            "calibrations":  result["calibrations"],
        })

        print(f"[AI-7] session_id={session_id} 완료 "
              f"(실패 포인트: {result['failed_points']})")

    except Exception as e:
        print(f"[AI-7] session_id={session_id} 오류: {e}")
        traceback.print_exc()
        _push_result_to_queue_b({
            "type":       "calibration_result",
            "session_id": session_id,
            "success":    False,
            "error":      str(e),
        })

    finally:
        # 로컬 임시파일 삭제 (MinIO 파일 건드리지 않음)
        for path in tmp_paths:
            if os.path.exists(path):
                os.remove(path)
                print(f"[AI-7] 로컬 임시파일 삭제: {path}")


# ══════════════════════════════════════════════════════════════
# [AI-8] 본 분석 파이프라인 태스크
# ══════════════════════════════════════════════════════════════
@app.task(
    name="celery_app.analyze_session",
    bind=True,
    max_retries=0,
    queue="ai_queue",
    acks_late=True,
)
def analyze_session(
    self,
    session_id:      int,
    video_path:      str,
    viewport_region: dict,
    calibrations:    list[dict],
    page_logs:       list[dict],
    task_results:    list[dict],
):
    """
    본 분석 태스크 — 표정/시선/음성 3종 병렬 분석 (AI-2~AI-5 + AI-8)

    Args:
        session_id      : 세션 ID
        video_path      : MinIO 녹화 영상 경로
                          (sessions/session_{id}/recording.mp4)
        viewport_region : {"x":0.0,"y":0.0,"w":1.0,"h":1.0}
        calibrations    : calibration_points JOIN calibrations (메인 서버 조회)
                          [{"point_no","screen_x","screen_y","gaze_x","gaze_y"}, ...]
        page_logs       : [{"page_no","url","start_video_ts","end_video_ts",
                            "screenshot_path"}, ...]
        task_results    : [{"task_order","result","duration_sec"}, ...]
                          result 값: "success" | "fail"

    큐B 페이로드 (AI-8):
        성공: {type, session_id, stt_segments, page_summaries, skipped_stt}
        오류: {type, session_id, success=false, error}

    ※ 큐B 적재 완료 후 로컬 임시파일 삭제. MinIO 파일 건드리지 않음.
    """
    print(f"[AI-8] session_id={session_id} 본 분석 시작")

    tmp_video        = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
    tmp_video.close()
    video_local_path = tmp_video.name

    try:
        # AI-2: MinIO에서 녹화 영상 다운로드 → 로컬 임시파일
        download_video(video_path, video_local_path)
        print(f"[AI-8] session_id={session_id} 영상 다운로드 완료")

        # 3종 병렬 분석
        # AI-3 (스크린샷 다운로드): gaze_analysis 내부에서 처리
        # AI-4 (JSON 업로드): emotion_analysis, gaze_analysis 내부에서 처리
        # AI-5 (히트맵 업로드): gaze_analysis 내부에서 처리
        emotion_result = None
        gaze_result    = None
        whisper_result = None

        with ProcessPoolExecutor(max_workers=3) as executor:
            future_emotion = executor.submit(
                _run_emotion, (video_local_path, session_id)
            )
            future_gaze = executor.submit(
                _run_gaze, (video_local_path, session_id,
                            calibrations, viewport_region, page_logs)
            )
            future_whisper = executor.submit(
                _run_whisper, (video_local_path, session_id)
            )

            futures = {
                future_emotion: "emotion",
                future_gaze:    "gaze",
                future_whisper: "whisper",
            }
            for future in as_completed(futures):
                name   = futures[future]
                result = future.result()
                if name == "emotion":
                    emotion_result = result
                elif name == "gaze":
                    gaze_result = result
                elif name == "whisper":
                    whisper_result = result
                print(f"[AI-8] session_id={session_id} {name} 분석 완료")

        # 혼란도 산출
        import sys as _sys, os as _os
        _r = _os.path.dirname(_os.path.abspath(__file__))
        if _r not in _sys.path:
            _sys.path.insert(0, _r)
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

        # AI-8: 큐B 적재
        # 메인 서버(MS-4)가 수신 후 stt_segments + page_summaries DB 저장
        _push_result_to_queue_b({
            "type":           "analysis_result",
            "session_id":     session_id,
            "stt_segments":   whisper_result["stt_segments"],
            "page_summaries": summaries,
            "skipped_stt":    whisper_result.get("skipped", False),
        })
        print(f"[AI-8] session_id={session_id} 큐B 적재 완료")

    except Exception as e:
        print(f"[AI-8] session_id={session_id} 오류: {e}")
        traceback.print_exc()
        _push_result_to_queue_b({
            "type":       "analysis_result",
            "session_id": session_id,
            "success":    False,
            "error":      str(e),
        })
        raise self.reject(requeue=False)

    finally:
        # 로컬 임시파일 삭제 (MinIO 파일 건드리지 않음)
        if os.path.exists(video_local_path):
            os.remove(video_local_path)
            print(f"[AI-8] session_id={session_id} 로컬 임시파일 삭제 완료")
