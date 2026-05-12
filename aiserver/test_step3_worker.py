"""
test_step3_worker.py
3단계: Celery Worker 구동 + 큐A→Worker→큐B 전체 흐름 테스트

실행 순서:
  [터미널 1] celery -A celery_app worker --loglevel=info -Q ai_queue -c 1
  [터미널 2] python test_step3_worker.py

테스트 내용:
  1. Redis 큐A/큐B 연결 확인
  2. Worker 활성화 확인
  3. analyze_calibration 태스크 → 큐B 결과 수신 검증
  4. analyze_session 태스크 → 큐B 결과 수신 검증 (MinIO mock)
"""

import os
import sys
import json
import time
import tempfile
import subprocess
import unittest.mock as mock

GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
RESET  = "\033[0m"

def ok(msg):   print(f"{GREEN}[OK]{RESET}   {msg}")
def fail(msg): print(f"{RED}[FAIL]{RESET} {msg}")
def info(msg): print(f"{YELLOW}[INFO]{RESET} {msg}")
def sep():     print("-" * 55)


# ── 설정 ────────────────────────────────────────────────────────
TEST_SESSION_ID = 9999

# 실제 MinIO에 존재하는 영상 경로가 있으면 지정 (없으면 mock 사용)
REAL_VIDEO_OBJECT_KEY = None
REAL_CALIB_OBJECT_KEY = None


def _make_dummy_video(duration_sec=3, fps=5) -> str:
    tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
    tmp.close()
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", f"color=c=green:size=320x240:rate={fps}:duration={duration_sec}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        tmp.name
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(f"더미 영상 생성 실패: {result.stderr.decode()[:100]}")
    return tmp.name


# ════════════════════════════════════════════════════════════════
# 테스트 3-1: Redis 연결
# ════════════════════════════════════════════════════════════════
def test_redis_connection():
    sep()
    info("3-1: Redis 연결 확인")
    import redis as redis_lib
    from config import REDIS_HOST_A, REDIS_PORT_A, REDIS_PASSWORD_A, \
                       REDIS_HOST, REDIS_PORT_B, REDIS_PASSWORD_B

    results = {}

    try:
        r_a = redis_lib.Redis(
            host=REDIS_HOST_A, port=REDIS_PORT_A,
            password=REDIS_PASSWORD_A if REDIS_PASSWORD_A else None,
            socket_timeout=3,
        )
        r_a.ping()
        ok(f"큐A 연결 성공 ({REDIS_HOST_A}:{REDIS_PORT_A})")
        results["queue_a"] = True
    except Exception as e:
        fail(f"큐A 연결 실패 ({REDIS_HOST_A}:{REDIS_PORT_A}): {e}")
        results["queue_a"] = False

    try:
        r_b = redis_lib.Redis(
            host=REDIS_HOST, port=REDIS_PORT_B,
            password=REDIS_PASSWORD_B if REDIS_PASSWORD_B else None,
            socket_timeout=3,
        )
        r_b.ping()
        ok(f"큐B 연결 성공 ({REDIS_HOST}:{REDIS_PORT_B})")
        results["queue_b"] = True
    except Exception as e:
        fail(f"큐B 연결 실패 ({REDIS_HOST}:{REDIS_PORT_B}): {e}")
        results["queue_b"] = False

    return results


# ════════════════════════════════════════════════════════════════
# 테스트 3-2: Worker 활성화 확인
# ════════════════════════════════════════════════════════════════
def test_worker_alive():
    sep()
    info("3-2: Celery Worker 활성화 확인")
    try:
        from celery_app import app
        inspect = app.control.inspect(timeout=5)
        active  = inspect.active()

        if not active:
            fail("활성 Worker 없음")
            info("Worker 시작: celery -A celery_app worker --loglevel=info -Q ai_queue -c 1")
            return False

        for worker_name, tasks in active.items():
            ok(f"Worker 활성: {worker_name}")
            info(f"  현재 처리 중: {len(tasks)}개 태스크")
        return True
    except Exception as e:
        fail(f"Worker 확인 실패: {e}")
        return False


# ════════════════════════════════════════════════════════════════
# 테스트 3-3: analyze_calibration 태스크
# ════════════════════════════════════════════════════════════════
def test_calibration_task():
    sep()
    info("3-3: analyze_calibration 태스크 → 큐B 결과 수신")

    dummy_video = None
    try:
        # 더미 영상 생성 (MinIO 업로드 없이 Worker가 직접 다운로드하는 척)
        dummy_video = _make_dummy_video(duration_sec=3)
        info(f"  더미 영상 생성: {dummy_video}")

        # MinIO download를 더미 영상 경로로 mock
        calib_object_key = f"sessions/session_{TEST_SESSION_ID}/calibration_{{n}}.mp4"

        calibration_points = [
            {"point_no": i, "screen_x": v[0], "screen_y": v[1],
             "video_object_key": calib_object_key.format(n=i)}
            for i, v in enumerate(
                [(0.1,0.1),(0.9,0.1),(0.5,0.5),(0.1,0.9),(0.9,0.9)], start=1
            )
        ]

        # MinIO 다운로드를 mock — 더미 영상을 복사해주는 방식
        def mock_download_calib(object_key, local_path):
            import shutil
            shutil.copy(dummy_video, local_path)

        from celery_app import app
        import redis as redis_lib
        from config import REDIS_HOST, REDIS_PORT_B

        # Worker에서 MinIO 접근 mock은 불가 (별도 프로세스)
        # → MinIO가 실제로 연결되어야 함, 아니면 REAL_CALIB_OBJECT_KEY 사용
        if REAL_CALIB_OBJECT_KEY:
            pts = [
                {"point_no": i,
                 "screen_x": v[0], "screen_y": v[1],
                 "video_object_key": REAL_CALIB_OBJECT_KEY.format(n=i)}
                for i, v in enumerate(
                    [(0.1,0.1),(0.9,0.1),(0.5,0.5),(0.1,0.9),(0.9,0.9)], start=1
                )
            ]
        else:
            info("  ※ REAL_CALIB_OBJECT_KEY 미설정 — MinIO 연결 필요")
            info("    test_step3_worker.py 상단의 REAL_CALIB_OBJECT_KEY에 실제 경로 입력 후 재실행")
            info("    또는 MinIO에 더미 영상을 직접 업로드 후 테스트")
            pts = calibration_points

        # 큐A에 태스크 적재
        app.send_task(
            "celery_app.analyze_calibration",
            args=[TEST_SESSION_ID],
            kwargs={"calibration_points": pts},
            queue="ai_queue",
        )
        ok(f"analyze_calibration 태스크 적재 완료 (session_id={TEST_SESSION_ID})")
        info("큐B 결과 대기 중... (최대 120초)")

        # 큐B에서 결과 수신
        result = _wait_queue_b("calibration_result", TEST_SESSION_ID, timeout=120)
        if result is None:
            fail("큐B 타임아웃 (120초) — Worker 로그 확인하세요")
            return False

        # 결과 검증
        info(f"  수신 페이로드:")
        info(f"    success       : {result.get('success')}")
        info(f"    failed_points : {result.get('failed_points')}")
        info(f"    calibrations  : {len(result.get('calibrations', []))}개")

        assert result.get("type") == "calibration_result"
        assert result.get("session_id") == TEST_SESSION_ID
        assert "success"       in result
        assert "failed_points" in result
        assert "calibrations"  in result

        # 성공한 calibrations 구조 검증
        for c in result.get("calibrations", []):
            assert all(k in c for k in ("point_no","screen_x","screen_y","gaze_x","gaze_y")), \
                f"calibrations 필드 누락: {c}"

        ok("calibration_result 페이로드 구조 정상")
        return True

    except Exception as e:
        fail(f"캘리브레이션 태스크 테스트 실패: {e}")
        import traceback; traceback.print_exc()
        return False
    finally:
        if dummy_video and os.path.exists(dummy_video):
            os.remove(dummy_video)


# ════════════════════════════════════════════════════════════════
# 테스트 3-4: analyze_session 태스크
# ════════════════════════════════════════════════════════════════
def test_analysis_task():
    sep()
    info("3-4: analyze_session 태스크 → 큐B 결과 수신")
    info("※ MinIO에 실제 녹화 영상 필요. 없으면 REAL_VIDEO_OBJECT_KEY 설정하세요")

    if not REAL_VIDEO_OBJECT_KEY:
        info("  REAL_VIDEO_OBJECT_KEY 미설정 — 태스크 적재 후 Worker 오류 예상")
        info("  MinIO에 영상이 있으면 test_step3_worker.py 상단 경로를 수정하세요")

    try:
        from celery_app import app
        from config import REDIS_HOST, REDIS_PORT_B

        kwargs = {
            "video_path":       REAL_VIDEO_OBJECT_KEY or f"sessions/session_{TEST_SESSION_ID}/recording.mp4",
            "viewport_region":  {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0},
            "calibrations": [
                {"point_no":1,"screen_x":0.1,"screen_y":0.1,"gaze_x":0.15,"gaze_y":0.13},
                {"point_no":2,"screen_x":0.9,"screen_y":0.1,"gaze_x":0.87,"gaze_y":0.12},
                {"point_no":3,"screen_x":0.5,"screen_y":0.5,"gaze_x":0.50,"gaze_y":0.49},
                {"point_no":4,"screen_x":0.1,"screen_y":0.9,"gaze_x":0.14,"gaze_y":0.88},
                {"point_no":5,"screen_x":0.9,"screen_y":0.9,"gaze_x":0.88,"gaze_y":0.87},
            ],
            "page_logs": [
                {"page_no":1,"url":"https://example.com","start_video_ts":0.0,
                 "end_video_ts":None,"screenshot_path":None}
            ],
            "task_results": [
                {"task_order":1,"result":"success","duration_sec":5.0},
                {"task_order":2,"result":"fail",   "duration_sec":9.0},
            ],
        }

        app.send_task(
            "celery_app.analyze_session",
            args=[TEST_SESSION_ID],
            kwargs=kwargs,
            queue="ai_queue",
        )
        ok(f"analyze_session 태스크 적재 완료 (session_id={TEST_SESSION_ID})")
        info("큐B 결과 대기 중... (최대 600초, 분석 시간에 따라 다름)")

        result = _wait_queue_b("analysis_result", TEST_SESSION_ID, timeout=600)
        if result is None:
            fail("큐B 타임아웃 (600초) — Worker 로그 확인하세요")
            return False

        # 결과 검증
        info(f"  수신 페이로드 요약:")
        info(f"    session_id    : {result.get('session_id')}")
        info(f"    stt_segments  : {len(result.get('stt_segments', []))}개")
        info(f"    page_summaries: {len(result.get('page_summaries', []))}개")
        info(f"    skipped_stt   : {result.get('skipped_stt')}")

        assert result.get("type")       == "analysis_result"
        assert result.get("session_id") == TEST_SESSION_ID
        assert "stt_segments"   in result
        assert "page_summaries" in result
        assert "skipped_stt"    in result

        # page_summaries 필드 검증
        required = [
            "page_no","dominant_emotion","neg_ratio","gaze_escape_ratio",
            "confusion_avg","task_confusion_json","stt_summary",
            "avg_silence_sec","task_success_rate","avg_task_duration_sec",
            "heatmap_path","detail_json_path",
        ]
        for ps in result.get("page_summaries", []):
            missing = [f for f in required if f not in ps]
            assert not missing, f"page_summaries 필드 누락: {missing}"
            assert 0.0 <= ps["confusion_avg"] <= 1.0, \
                f"confusion_avg 범위 오류: {ps['confusion_avg']}"
            assert f"heatmap_page_{ps['page_no']}" in (ps.get("heatmap_path") or ""), \
                f"히트맵 경로에 page_no 없음: {ps.get('heatmap_path')}"

        ok("analysis_result 페이로드 구조 및 값 정상")
        return True

    except Exception as e:
        fail(f"본 분석 태스크 테스트 실패: {e}")
        import traceback; traceback.print_exc()
        return False


# ════════════════════════════════════════════════════════════════
# 큐B 수신 헬퍼
# ════════════════════════════════════════════════════════════════
def _wait_queue_b(expected_type: str, session_id: int, timeout: int) -> dict | None:
    import redis as redis_lib
    from config import REDIS_HOST, REDIS_PORT_B, REDIS_PASSWORD_B

    r_b = redis_lib.Redis(
        host=REDIS_HOST, port=REDIS_PORT_B,
        password=REDIS_PASSWORD_B if REDIS_PASSWORD_B else None,
        db=0,
    )
    start  = time.time()
    popped = []

    while time.time() - start < timeout:
        remaining = int(timeout - (time.time() - start))
        raw = r_b.blpop("result_queue", timeout=min(remaining, 5))

        if raw is None:
            elapsed = int(time.time() - start)
            info(f"  대기 중... ({elapsed}초/{timeout}초)")
            continue

        _, message = raw
        try:
            payload = json.loads(message)
        except json.JSONDecodeError:
            fail(f"JSON 파싱 실패: {message[:80]}")
            continue

        if payload.get("session_id") == session_id and payload.get("type") == expected_type:
            for msg in popped:
                r_b.rpush("result_queue", msg)
            return payload

        info(f"  다른 메시지 보관 (session={payload.get('session_id')}, type={payload.get('type')})")
        popped.append(message)

    for msg in popped:
        r_b.rpush("result_queue", msg)
    return None


# ════════════════════════════════════════════════════════════════
# 메인
# ════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("=" * 55)
    print("3단계: Celery Worker 전체 흐름 테스트")
    print(f"테스트 session_id: {TEST_SESSION_ID}")
    print("=" * 55)
    info("전제: celery -A celery_app worker --loglevel=info -Q ai_queue -c 1")
    print()

    import sys
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"

    results = {}

    # 3-1: Redis
    conn = test_redis_connection()
    results["Redis 연결"] = all(conn.values())
    if not results["Redis 연결"]:
        fail("Redis 연결 실패 — 이후 테스트 중단")
        sys.exit(1)

    # 3-2: Worker
    results["Worker 활성"] = test_worker_alive()
    if not results["Worker 활성"]:
        sys.exit(1)

    # 3-3: 캘리브레이션 (all 또는 calib)
    if mode in ("all", "calib"):
        results["캘리브레이션 태스크"] = test_calibration_task()

    # 3-4: 본 분석 (all 또는 analysis)
    if mode in ("all", "analysis"):
        results["본 분석 태스크"] = test_analysis_task()

    # 요약
    sep()
    print("결과 요약")
    sep()
    all_pass = True
    for name, passed in results.items():
        if passed: ok(name)
        else:      fail(name); all_pass = False

    sep()
    if all_pass:
        print(f"{GREEN}모든 테스트 통과{RESET}")
    else:
        print(f"{RED}일부 실패 — Worker 로그 확인{RESET}")
        print("  Worker 로그: celery -A celery_app worker --loglevel=debug -Q ai_queue -c 1")

    print("""
사용법:
    python test_step3_worker.py          # 전체
    python test_step3_worker.py calib    # 캘리브레이션만
    python test_step3_worker.py analysis # 본 분석만
""")
