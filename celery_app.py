# ai_server/workers/celery_app.py
# Celery Worker 4개 — Redis 큐 A 수신 → 3종 병렬 분석 → 결과 큐 B 적재

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
from minio_client import download_video, delete_object

# ── Celery 앱 설정 ────────────────────────────────────────────────
app = Celery("ai_server", broker=CELERY_BROKER, backend=CELERY_BACKEND)
app.conf.update(
    task_serializer      = "json",
    result_serializer    = "json",
    accept_content       = ["json"],
    task_acks_late       = True,       # 처리 완료 후 ACK
    worker_prefetch_multiplier = 1,    # Worker당 1개씩 수신 (공정 분배)
    task_reject_on_worker_lost = True,
    task_routes          = {"workers.celery_app.analyze_session": {"queue": "ai_queue"}},
    # Dead Letter Queue
    task_queues          = {
        "ai_queue": {"exchange": "ai_queue", "routing_key": "ai_queue"},
        "dlq":      {"exchange": "dlq",      "routing_key": "dlq"},
    },
)

# ── Redis 큐 B (분석 결과 송신용) ────────────────────────────────
_redis_b = redis.Redis(host=REDIS_HOST, port=REDIS_PORT_B, db=0, decode_responses=False)


def _push_result_to_queue_b(payload: dict):
    """분석 결과를 Redis 큐 B에 JSON으로 적재"""
    _redis_b.rpush("result_queue", json.dumps(payload, ensure_ascii=False))


# ── 프로세스 풀 래퍼 함수 (최상위 함수여야 multiprocessing 직렬화 가능) ─
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


# ── 메인 Celery 태스크 ────────────────────────────────────────────
@app.task(
    name="celery_app.analyze_session",
    bind=True,
    max_retries=0,               # 재시도 없이 DLQ로
    queue="ai_queue",
    acks_late=True,
)
def analyze_session(self, session_id: int):
    """
    session_id를 받아 전체 분석 파이프라인 실행

    1. DB에서 메타데이터 로드
    2. MinIO에서 영상 다운로드
    3. ProcessPoolExecutor로 표정·시선·음성 병렬 분석
    4. 혼란도 점수 산출
    5. DB 저장 + 큐 B 적재
    6. MinIO 영상 삭제
    """
    print(f"[Worker] session_id={session_id} 분석 시작")
    update_session_status(session_id, "analyzing")

    # 임시 영상 파일 경로
    tmp_video = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
    tmp_video.close()
    video_local_path = tmp_video.name

    try:
        # ── 1. DB 메타데이터 로드 ────────────────────────────────
        session      = load_session(session_id)
        calibrations = load_calibrations(session_id)
        page_logs    = load_page_logs(session_id)
        task_results = load_task_results(session_id)

        video_path      = session["video_path"]
        viewport_region = session["viewport_region"]

        # viewport_region이 JSON 문자열로 저장된 경우 파싱
        if isinstance(viewport_region, str):
            viewport_region = json.loads(viewport_region)

        # ── 2. MinIO 영상 다운로드 ──────────────────────────────
        download_video(video_path, video_local_path)
        print(f"[Worker] session_id={session_id} 영상 다운로드 완료")

        # ── 3. ProcessPoolExecutor 병렬 분석 (max_workers=3) ────
        emotion_result = None
        gaze_result    = None
        whisper_result = None

        with ProcessPoolExecutor(max_workers=3) as executor:
            future_emotion = executor.submit(
                _run_emotion,
                (video_local_path, session_id),
            )
            future_gaze = executor.submit(
                _run_gaze,
                (video_local_path, session_id, calibrations, viewport_region, page_logs),
            )
            future_whisper = executor.submit(
                _run_whisper,
                (video_local_path, session_id),
            )

            futures = {
                future_emotion:  "emotion",
                future_gaze:     "gaze",
                future_whisper:  "whisper",
            }

            for future in as_completed(futures):
                name = futures[future]
                try:
                    result = future.result()
                    if name == "emotion":
                        emotion_result = result
                    elif name == "gaze":
                        gaze_result = result
                    elif name == "whisper":
                        whisper_result = result
                    print(f"[Worker] session_id={session_id} {name} 분석 완료")
                except Exception as e:
                    print(f"[Worker] session_id={session_id} {name} 분석 실패: {e}")
                    traceback.print_exc()
                    raise

        # ── 4. 혼란도 점수 산출 ──────────────────────────────────
        from confusion_index import calc_confusion_index
        summaries = calc_confusion_index(
            frame_emotions                = emotion_result["frame_emotions"],
            gaze_points                   = gaze_result["gaze_points"],
            gaze_escape_ratio_per_page    = gaze_result["gaze_escape_ratio_per_page"],
            stt_segments                  = whisper_result["stt_segments"],
            page_logs                     = page_logs,
            task_results                  = task_results,
            heatmap_paths                 = gaze_result["heatmap_paths"],
            emotion_detail_json_path      = emotion_result["detail_json_path"],
            gaze_detail_json_path         = gaze_result["detail_json_path"],
        )

        # ── 5. DB 저장 ──────────────────────────────────────────
        save_page_summaries(session_id, summaries)
        update_session_status(session_id, "done")
        print(f"[Worker] session_id={session_id} DB 저장 완료")

        # ── 6. 큐 B 적재 (메인 서버 → LLM + PDF 생성용) ─────────
        payload = {
            "session_id":    session_id,
            "stt_segments":  whisper_result["stt_segments"],
            "page_summaries": summaries,
            "skipped_stt":   whisper_result.get("skipped", False),
        }
        _push_result_to_queue_b(payload)
        print(f"[Worker] session_id={session_id} 결과 큐 B 적재 완료")

        # ── 7. MinIO 영상 즉시 삭제 (개인정보 보호) ──────────────
        delete_object(video_path)
        print(f"[Worker] session_id={session_id} 영상 삭제 완료")

    except Exception as e:
        print(f"[Worker] session_id={session_id} 치명적 오류: {e}")
        traceback.print_exc()
        update_session_status(session_id, "failed")
        update_report_status(session_id, "failed")
        # DLQ로 이동 (Celery reject)
        raise self.reject(requeue=False)

    finally:
        if os.path.exists(video_local_path):
            os.remove(video_local_path)
