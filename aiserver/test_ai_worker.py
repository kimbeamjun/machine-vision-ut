"""
test_ai_worker.py
AI 서버 단독 테스트: Redis 큐A에 직접 태스크를 밀어넣어 Worker 동작 검증

실행 방법:
    # AI 서버(10.10.10.128)에서 실행
    python test_ai_worker.py

    # 또는 외부에서 실행 (Redis 접근 가능한 환경)
    python test_ai_worker.py

사전 조건:
    - AI 서버 Celery Worker 실행 중:
        celery -A celery_app worker --loglevel=info -Q ai_queue -c 4
    - Redis 실행 중 (10.10.10.113:6379, 10.10.10.128:6380)
    - MinIO에 테스트용 영상 파일 존재
    - pip install celery redis
"""

import json
import time
import redis
from celery import Celery

# ── 설정 ────────────────────────────────────────────────────────
REDIS_HOST_MAIN = "10.10.10.113"
REDIS_PORT_A    = 6379   # 큐A: 메인서버 → AI Worker
REDIS_HOST_AI   = "10.10.10.128"
REDIS_PORT_B    = 6380   # 큐B: AI Worker → 메인서버

CELERY_BROKER = f"redis://{REDIS_HOST_MAIN}:{REDIS_PORT_A}/0"

# 테스트용 session_id (실제 DB에 존재하는 session_id로 교체)
TEST_SESSION_ID = 999

# 테스트용 MinIO 경로 (실제 존재하는 파일로 교체)
TEST_VIDEO_OBJECT_KEY = f"sessions/session_{TEST_SESSION_ID}/recording.mp4"
TEST_CALIB_OBJECT_KEY = f"sessions/session_{TEST_SESSION_ID}/calibration_{{n}}.mp4"

# ── 색상 출력 ────────────────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
RESET  = "\033[0m"

def ok(msg):   print(f"{GREEN}[PASS]{RESET} {msg}")
def fail(msg): print(f"{RED}[FAIL]{RESET} {msg}")
def info(msg): print(f"{YELLOW}[INFO]{RESET} {msg}")
def sep():     print("-" * 60)


# ════════════════════════════════════════════════════════════════
# 테스트 1: Redis 연결 확인
# ════════════════════════════════════════════════════════════════
def test_redis_connection():
    sep()
    info("테스트 1: Redis 연결 확인")
    results = {}

    # 큐A 연결
    try:
        r_a = redis.Redis(host=REDIS_HOST_MAIN, port=REDIS_PORT_A, db=0, socket_timeout=3)
        r_a.ping()
        ok(f"큐A 연결 성공 ({REDIS_HOST_MAIN}:{REDIS_PORT_A})")
        results["queue_a"] = True
    except Exception as e:
        fail(f"큐A 연결 실패: {e}")
        results["queue_a"] = False

    # 큐B 연결
    try:
        r_b = redis.Redis(host=REDIS_HOST_AI, port=REDIS_PORT_B, db=0, socket_timeout=3)
        r_b.ping()
        ok(f"큐B 연결 성공 ({REDIS_HOST_AI}:{REDIS_PORT_B})")
        results["queue_b"] = True
    except Exception as e:
        fail(f"큐B 연결 실패: {e}")
        results["queue_b"] = False

    return results


# ════════════════════════════════════════════════════════════════
# 테스트 2: Celery Worker 활성화 확인
# ════════════════════════════════════════════════════════════════
def test_worker_alive():
    sep()
    info("테스트 2: Celery Worker 활성화 확인")
    try:
        app = Celery("ai_server", broker=CELERY_BROKER)
        inspect = app.control.inspect(timeout=5)
        active  = inspect.active()

        if not active:
            fail("활성 Worker 없음 — celery worker가 실행 중인지 확인하세요")
            return False

        for worker_name, tasks in active.items():
            ok(f"Worker 활성: {worker_name} (현재 처리 중인 태스크: {len(tasks)}개)")
        return True

    except Exception as e:
        fail(f"Worker 확인 실패: {e}")
        return False


# ════════════════════════════════════════════════════════════════
# 테스트 3: 캘리브레이션 태스크 적재 + 큐B 결과 수신
# ════════════════════════════════════════════════════════════════
def test_calibration_task():
    sep()
    info("테스트 3: analyze_calibration 태스크 적재 + 큐B 결과 수신")
    info(f"  session_id={TEST_SESSION_ID} 사용")
    info("  ※ MinIO에 실제 캘리브레이션 영상이 없으면 AI Worker에서 오류 발생")

    app = Celery("ai_server", broker=CELERY_BROKER)
    app.conf.update(
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
    )

    calibration_points = [
        {"point_no": 1, "screen_x": 0.1, "screen_y": 0.1,
         "video_object_key": TEST_CALIB_OBJECT_KEY.format(n=1)},
        {"point_no": 2, "screen_x": 0.9, "screen_y": 0.1,
         "video_object_key": TEST_CALIB_OBJECT_KEY.format(n=2)},
        {"point_no": 3, "screen_x": 0.5, "screen_y": 0.5,
         "video_object_key": TEST_CALIB_OBJECT_KEY.format(n=3)},
        {"point_no": 4, "screen_x": 0.1, "screen_y": 0.9,
         "video_object_key": TEST_CALIB_OBJECT_KEY.format(n=4)},
        {"point_no": 5, "screen_x": 0.9, "screen_y": 0.9,
         "video_object_key": TEST_CALIB_OBJECT_KEY.format(n=5)},
    ]

    try:
        # 큐A에 태스크 적재
        app.send_task(
            "celery_app.analyze_calibration",
            args=[TEST_SESSION_ID],
            kwargs={"calibration_points": calibration_points},
            queue="ai_queue",
        )
        ok(f"analyze_calibration 태스크 적재 완료 → session_id={TEST_SESSION_ID}")
        info("큐B에서 결과 대기 중... (최대 120초)")

        # 큐B에서 결과 수신 대기
        result = _wait_queue_b(expected_type="calibration_result",
                               session_id=TEST_SESSION_ID,
                               timeout=120)
        if result is None:
            fail("큐B 결과 수신 타임아웃 (120초)")
            return False

        # 결과 검증
        info(f"큐B 수신 페이로드: {json.dumps(result, ensure_ascii=False, indent=2)}")

        assert result.get("type") == "calibration_result", f"type 오류: {result.get('type')}"
        assert result.get("session_id") == TEST_SESSION_ID, f"session_id 불일치"

        if result.get("success"):
            calibrations = result.get("calibrations", [])
            failed       = result.get("failed_points", [])
            ok(f"캘리브레이션 성공 — 성공: {len(calibrations)}개, 실패: {failed}")

            # 페이로드 구조 검증
            for c in calibrations:
                assert all(k in c for k in ("point_no","screen_x","screen_y","gaze_x","gaze_y")), \
                    f"calibrations 필드 누락: {c}"
            ok("calibrations 배열 구조 정상")
            return True
        else:
            info(f"캘리브레이션 실패 또는 AI 오류: {result.get('error','')}, "
                 f"failed_points={result.get('failed_points','')}")
            info("※ MinIO에 실제 영상이 없거나 얼굴 미감지 시 예상되는 결과")
            return True  # 태스크 자체는 정상 동작한 것

    except Exception as e:
        fail(f"테스트 3 실패: {e}")
        return False


# ════════════════════════════════════════════════════════════════
# 테스트 4: 본 분석 태스크 적재 + 큐B 결과 수신
# ════════════════════════════════════════════════════════════════
def test_analysis_task():
    sep()
    info("테스트 4: analyze_session 태스크 적재 + 큐B 결과 수신")
    info(f"  session_id={TEST_SESSION_ID} 사용")
    info("  ※ MinIO에 실제 녹화 영상이 없으면 AI Worker에서 오류 발생")

    app = Celery("ai_server", broker=CELERY_BROKER)
    app.conf.update(
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
    )

    # 테스트용 kwargs (실제 DB 값으로 교체 권장)
    kwargs = {
        "video_path":      TEST_VIDEO_OBJECT_KEY,
        "viewport_region": {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0},
        "calibrations": [
            {"point_no": 1, "screen_x": 0.1, "screen_y": 0.1, "gaze_x": 0.312, "gaze_y": 0.445},
            {"point_no": 2, "screen_x": 0.9, "screen_y": 0.1, "gaze_x": 0.878, "gaze_y": 0.441},
            {"point_no": 3, "screen_x": 0.5, "screen_y": 0.5, "gaze_x": 0.501, "gaze_y": 0.498},
            {"point_no": 4, "screen_x": 0.1, "screen_y": 0.9, "gaze_x": 0.134, "gaze_y": 0.881},
            {"point_no": 5, "screen_x": 0.9, "screen_y": 0.9, "gaze_x": 0.889, "gaze_y": 0.902},
        ],
        "page_logs": [
            {
                "page_no":        1,
                "url":            "https://example.com/test",
                "start_video_ts": 0.0,
                "end_video_ts":   15.5,
                "screenshot_path": None,
            }
        ],
        "task_results": [
            {"task_order": 1, "result": "success", "duration_sec": 12.3},
            {"task_order": 2, "result": "fail",    "duration_sec": 30.0},
        ],
    }

    try:
        # 큐A에 태스크 적재
        app.send_task(
            "celery_app.analyze_session",
            args=[TEST_SESSION_ID],
            kwargs=kwargs,
            queue="ai_queue",
        )
        ok(f"analyze_session 태스크 적재 완료 → session_id={TEST_SESSION_ID}")
        info("큐B에서 결과 대기 중... (최대 600초, 분석 시간에 따라 다름)")

        # 큐B에서 결과 수신 대기
        result = _wait_queue_b(expected_type="analysis_result",
                               session_id=TEST_SESSION_ID,
                               timeout=600)
        if result is None:
            fail("큐B 결과 수신 타임아웃 (600초)")
            return False

        info(f"큐B 수신 페이로드 (요약):")
        info(f"  type          : {result.get('type')}")
        info(f"  session_id    : {result.get('session_id')}")
        info(f"  skipped_stt   : {result.get('skipped_stt')}")
        info(f"  stt_segments  : {len(result.get('stt_segments', []))}개")
        info(f"  page_summaries: {len(result.get('page_summaries', []))}개")

        # 구조 검증
        assert result.get("type") == "analysis_result"
        assert result.get("session_id") == TEST_SESSION_ID
        assert "stt_segments"   in result
        assert "page_summaries" in result
        assert "skipped_stt"    in result

        # page_summaries 필드 검증
        required_fields = [
            "page_no","url","start_video_ts","end_video_ts",
            "dominant_emotion","neg_ratio","gaze_escape_ratio",
            "confusion_avg","task_confusion_json","stt_summary",
            "avg_silence_sec","task_success_rate","avg_task_duration_sec",
            "heatmap_path","detail_json_path",
        ]
        for ps in result.get("page_summaries", []):
            missing = [f for f in required_fields if f not in ps]
            assert not missing, f"page_summaries 필드 누락: {missing}"

        ok("page_summaries 구조 정상")
        ok("analyze_session 태스크 정상 완료")
        return True

    except Exception as e:
        fail(f"테스트 4 실패: {e}")
        return False


# ════════════════════════════════════════════════════════════════
# 큐B 결과 수신 대기 헬퍼
# ════════════════════════════════════════════════════════════════
def _wait_queue_b(expected_type: str, session_id: int, timeout: int = 120) -> dict | None:
    """
    큐B(result_queue)에서 특정 type + session_id 메시지 수신 대기
    다른 session_id 메시지는 다시 큐에 넣음
    """
    r_b    = redis.Redis(host=REDIS_HOST_AI, port=REDIS_PORT_B, db=0)
    start  = time.time()
    popped = []  # 다른 세션 메시지 임시 보관

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
            fail(f"큐B 메시지 JSON 파싱 실패: {message[:100]}")
            continue

        # 내 세션 + 원하는 type이면 반환
        if payload.get("session_id") == session_id and payload.get("type") == expected_type:
            # 임시 보관했던 다른 세션 메시지 다시 큐에 복원
            for msg in popped:
                r_b.rpush("result_queue", msg)
            return payload

        # 다른 세션 메시지는 임시 보관
        info(f"  다른 메시지 수신 (session_id={payload.get('session_id')}, "
             f"type={payload.get('type')}) → 다시 큐에 복원 예정")
        popped.append(message)

    # 타임아웃 시 임시 보관 메시지 복원
    for msg in popped:
        r_b.rpush("result_queue", msg)
    return None


# ════════════════════════════════════════════════════════════════
# 메인 실행
# ════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("=" * 60)
    print("UDT AI 서버 단독 테스트")
    print(f"큐A: {REDIS_HOST_MAIN}:{REDIS_PORT_A}  큐B: {REDIS_HOST_AI}:{REDIS_PORT_B}")
    print(f"테스트 session_id: {TEST_SESSION_ID}")
    print("=" * 60)

    import sys
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"

    results = {}

    # 테스트 1: Redis 연결
    conn = test_redis_connection()
    results["Redis 연결"] = all(conn.values())
    if not results["Redis 연결"]:
        fail("Redis 연결 실패 — 이후 테스트 중단")
        exit(1)

    # 테스트 2: Worker 활성화
    results["Worker 활성"] = test_worker_alive()
    if not results["Worker 활성"]:
        fail("Worker 비활성 — 이후 테스트 중단")
        info("Worker 시작 명령: celery -A celery_app worker --loglevel=info -Q ai_queue -c 4")
        exit(1)

    # 테스트 3: 캘리브레이션 (all 또는 calib 모드)
    if mode in ("all", "calib"):
        results["캘리브레이션 태스크"] = test_calibration_task()

    # 테스트 4: 본 분석 (all 또는 analysis 모드)
    if mode in ("all", "analysis"):
        results["본 분석 태스크"] = test_analysis_task()

    # 결과 요약
    sep()
    print("\n결과 요약")
    sep()
    all_pass = True
    for name, passed in results.items():
        if passed:
            ok(name)
        else:
            fail(name)
            all_pass = False

    sep()
    if all_pass:
        print(f"{GREEN}모든 테스트 통과{RESET}")
    else:
        print(f"{RED}일부 테스트 실패{RESET}")

    print("""
사용법:
    python test_ai_worker.py          # 전체 테스트
    python test_ai_worker.py calib    # 캘리브레이션만
    python test_ai_worker.py analysis # 본 분석만
""")
