"""
test_step2_modules.py
2단계: 더미 영상으로 각 분석 모듈 단독 동작 확인
- MediaPipe 홍채 추출 (calibration_analysis)
- Whisper STT (whisper_analysis)
- 표정 분석 (emotion_analysis)
- 시선 분석 (gaze_analysis)
- 혼란도 산출 (confusion_index)

실행: python test_step2_modules.py

※ 실제 영상 없이도 동작하지만 분석 결과는 비어있을 수 있음
   실제 영상 경로를 아래 TEST_VIDEO_PATH에 지정하면 정확한 결과 확인 가능
"""

import os
import sys
import json
import tempfile
import subprocess
import numpy as np

GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
RESET  = "\033[0m"

def ok(msg):   print(f"{GREEN}[OK]{RESET}   {msg}")
def fail(msg): print(f"{RED}[FAIL]{RESET} {msg}")
def info(msg): print(f"{YELLOW}[INFO]{RESET} {msg}")
def sep():     print("-" * 55)


# ── 테스트용 설정 ────────────────────────────────────────────────
# 실제 영상이 있으면 아래 경로로 교체
TEST_VIDEO_PATH = None   # None이면 더미 영상 자동 생성

# 더미 캘리브레이션 데이터 (AI 서버가 DB 없이 직접 사용)
DUMMY_CALIBRATIONS = [
    {"point_no": 1, "screen_x": 0.1, "screen_y": 0.1, "gaze_x": 0.15, "gaze_y": 0.13},
    {"point_no": 2, "screen_x": 0.9, "screen_y": 0.1, "gaze_x": 0.87, "gaze_y": 0.12},
    {"point_no": 3, "screen_x": 0.5, "screen_y": 0.5, "gaze_x": 0.50, "gaze_y": 0.49},
    {"point_no": 4, "screen_x": 0.1, "screen_y": 0.9, "gaze_x": 0.14, "gaze_y": 0.88},
    {"point_no": 5, "screen_x": 0.9, "screen_y": 0.9, "gaze_x": 0.88, "gaze_y": 0.87},
]

DUMMY_PAGE_LOGS = [
    {"page_no": 1, "url": "https://example.com/test",
     "start_video_ts": 0.0, "end_video_ts": 10.0, "screenshot_path": None},
]

DUMMY_TASK_RESULTS = [
    {"task_order": 1, "result": "success", "duration_sec": 5.0},
    {"task_order": 2, "result": "fail",    "duration_sec": 9.0},
]

DUMMY_SESSION_ID = 999


def _make_dummy_video(path: str, duration_sec: int = 5, fps: int = 10):
    """ffmpeg로 더미 컬러 영상 생성 (소리 없음)"""
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", f"color=c=blue:size=640x480:rate={fps}:duration={duration_sec}",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        path
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(f"더미 영상 생성 실패: {result.stderr.decode()[:200]}")


# ════════════════════════════════════════════════════════════════
# 테스트 2-1: config 로드
# ════════════════════════════════════════════════════════════════
def test_config():
    sep()
    info("2-1: config.py 로드 및 주요 값 확인")
    try:
        from config import (
            REDIS_HOST_A, REDIS_PORT_A,
            REDIS_HOST, REDIS_PORT_B,
            MINIO_ENDPOINT, CELERY_BROKER,
            EMOTION_MODEL_PATH,
        )
        ok(f"큐A Broker : {CELERY_BROKER}")
        ok(f"큐B Redis  : {REDIS_HOST}:{REDIS_PORT_B}")
        ok(f"MinIO      : {MINIO_ENDPOINT}")
        ok(f"모델 경로  : {EMOTION_MODEL_PATH}")

        # DB 설정이 없는지 확인
        import config
        assert not hasattr(config, "DB_HOST"), "DB_HOST가 남아있음 — config.py 수정 필요"
        ok("DB 설정 없음 확인 (AI 서버 DB 미접근)")
        return True
    except Exception as e:
        fail(f"config 오류: {e}")
        return False


# ════════════════════════════════════════════════════════════════
# 테스트 2-2: 더미 영상 생성
# ════════════════════════════════════════════════════════════════
def make_test_video():
    sep()
    info("2-2: 테스트 영상 준비")
    global TEST_VIDEO_PATH

    if TEST_VIDEO_PATH and os.path.exists(TEST_VIDEO_PATH):
        ok(f"실제 영상 사용: {TEST_VIDEO_PATH}")
        return TEST_VIDEO_PATH

    tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
    tmp.close()
    try:
        _make_dummy_video(tmp.name, duration_sec=5, fps=10)
        ok(f"더미 영상 생성 완료: {tmp.name} ({os.path.getsize(tmp.name)//1024}KB)")
        TEST_VIDEO_PATH = tmp.name
        return tmp.name
    except Exception as e:
        fail(f"더미 영상 생성 실패: {e}")
        info("ffmpeg가 설치되어 있는지 확인하세요: conda install ffmpeg -c conda-forge")
        return None


# ════════════════════════════════════════════════════════════════
# 테스트 2-3: calibration_analysis
# ════════════════════════════════════════════════════════════════
def test_calibration_analysis(video_path: str):
    sep()
    info("2-3: calibration_analysis — 홍채 좌표 추출")
    info("※ 더미 영상(단색)은 얼굴 미감지 → failed_points=[1~5] 예상")
    try:
        from calibration_analysis import run_calibration_analysis

        # 더미 캘리브레이션 비디오 데이터
        calib_videos = [
            {"point_no": i, "screen_x": p["screen_x"],
             "screen_y": p["screen_y"], "local_path": video_path}
            for i, p in enumerate(DUMMY_CALIBRATIONS, start=1)
        ]

        result = run_calibration_analysis(DUMMY_SESSION_ID, calib_videos)

        ok(f"함수 실행 완료")
        info(f"  success       : {result['success']}")
        info(f"  failed_points : {result['failed_points']}")
        info(f"  calibrations  : {len(result['calibrations'])}개 성공")

        # 구조 검증
        assert "success"       in result
        assert "calibrations"  in result
        assert "failed_points" in result
        assert isinstance(result["calibrations"], list)

        ok("반환값 구조 정상")
        return True
    except Exception as e:
        fail(f"calibration_analysis 오류: {e}")
        import traceback; traceback.print_exc()
        return False


# ════════════════════════════════════════════════════════════════
# 테스트 2-4: whisper_analysis
# ════════════════════════════════════════════════════════════════
def test_whisper_analysis(video_path: str):
    sep()
    info("2-4: whisper_analysis — STT 분석")
    info("※ 더미 영상(소리 없음)은 stt_segments=[] / skipped=True 예상")
    try:
        from whisper_analysis import run_whisper_analysis

        result = run_whisper_analysis(video_path, DUMMY_SESSION_ID)

        ok("함수 실행 완료")
        info(f"  stt_segments : {len(result['stt_segments'])}개")
        info(f"  skipped      : {result['skipped']}")

        # 구조 검증
        assert "stt_segments" in result
        assert "skipped"      in result
        assert isinstance(result["stt_segments"], list)

        # stt_segments가 있으면 필드 검증
        for seg in result["stt_segments"]:
            assert all(k in seg for k in ("start_ts","end_ts","text","silence_sec")), \
                f"stt_segments 필드 누락: {seg}"

        ok("반환값 구조 정상")
        return True
    except Exception as e:
        fail(f"whisper_analysis 오류: {e}")
        import traceback; traceback.print_exc()
        return False


# ════════════════════════════════════════════════════════════════
# 테스트 2-5: emotion_analysis
# ════════════════════════════════════════════════════════════════
def test_emotion_analysis(video_path: str):
    sep()
    info("2-5: emotion_analysis — 표정 분석")
    info("※ 모델 파일(emotion_model.pth)이 없으면 실패")
    try:
        from config import EMOTION_MODEL_PATH
        if not os.path.exists(EMOTION_MODEL_PATH):
            fail(f"모델 파일 없음: {EMOTION_MODEL_PATH}")
            info("모델 파일을 해당 경로에 복사 후 재시도하세요")
            return False

        from emotion_analysis import run_emotion_analysis

        # MinIO 업로드는 skip (모듈 로직만 테스트)
        # run_emotion_analysis 내부에서 upload_json 호출 → MinIO 미연결 시 실패
        # → minio_client를 mock으로 교체
        import unittest.mock as mock
        with mock.patch("emotion_analysis.upload_json", return_value="sessions/session_999/detail.json"):
            result = run_emotion_analysis(video_path, DUMMY_SESSION_ID)

        ok("함수 실행 완료")
        info(f"  frame_emotions  : {len(result['frame_emotions'])}개 프레임")
        info(f"  detail_json_path: {result['detail_json_path']}")

        # 구조 검증
        assert "frame_emotions"   in result
        assert "detail_json_path" in result
        assert isinstance(result["frame_emotions"], list)

        if result["frame_emotions"]:
            sample = result["frame_emotions"][0]
            assert all(k in sample for k in ("timestamp","emotion","confidence")), \
                f"frame_emotions 필드 누락: {sample}"
            info(f"  샘플 결과: {sample}")

        ok("반환값 구조 정상")
        return True
    except Exception as e:
        fail(f"emotion_analysis 오류: {e}")
        import traceback; traceback.print_exc()
        return False


# ════════════════════════════════════════════════════════════════
# 테스트 2-6: gaze_analysis
# ════════════════════════════════════════════════════════════════
def test_gaze_analysis(video_path: str):
    sep()
    info("2-6: gaze_analysis — 시선 분석 + 히트맵")
    info("※ 더미 영상은 얼굴 미감지 → gaze_points=[] 예상 (로직은 정상 동작)")
    try:
        import unittest.mock as mock
        from gaze_analysis import run_gaze_analysis

        # MinIO 업로드 mock
        with mock.patch("gaze_analysis.upload_json",  return_value="sessions/session_999/detail.json"), \
             mock.patch("gaze_analysis.upload_image", return_value="sessions/session_999/heatmap_page_1.png"), \
             mock.patch("gaze_analysis.download_screenshot", return_value=None):

            result = run_gaze_analysis(
                video_path,
                DUMMY_SESSION_ID,
                DUMMY_CALIBRATIONS,
                {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0},
                DUMMY_PAGE_LOGS,
            )

        ok("함수 실행 완료")
        info(f"  gaze_points               : {len(result['gaze_points'])}개")
        info(f"  gaze_escape_ratio_per_page: {result['gaze_escape_ratio_per_page']}")
        info(f"  heatmap_paths             : {result['heatmap_paths']}")
        info(f"  detail_json_path          : {result['detail_json_path']}")

        # 구조 검증
        assert "gaze_points"                in result
        assert "gaze_escape_ratio_per_page" in result
        assert "heatmap_paths"              in result
        assert "detail_json_path"           in result

        # 히트맵 경로가 페이지별로 생성되는지 확인
        for pno, path in result["heatmap_paths"].items():
            assert f"heatmap_page_{pno}" in path, \
                f"히트맵 경로에 page_no 없음: {path}"
        ok("히트맵 페이지별 경로 분리 정상")

        ok("반환값 구조 정상")
        return True
    except Exception as e:
        fail(f"gaze_analysis 오류: {e}")
        import traceback; traceback.print_exc()
        return False


# ════════════════════════════════════════════════════════════════
# 테스트 2-7: confusion_index
# ════════════════════════════════════════════════════════════════
def test_confusion_index():
    sep()
    info("2-7: confusion_index — 혼란도 산출")
    try:
        from confusion_index import calc_confusion_index

        # 더미 분석 결과 데이터
        frame_emotions = [
            {"timestamp": t * 0.1, "emotion": "negative", "confidence": 0.8}
            for t in range(50)
        ] + [
            {"timestamp": (50 + t) * 0.1, "emotion": "neutral", "confidence": 0.7}
            for t in range(50)
        ]

        gaze_points = [
            {"timestamp": t * 0.1, "x": 0.5, "y": 0.5, "page_no": 1}
            for t in range(100)
        ]

        gaze_escape_ratio = {1: 0.22}

        stt_segments = [
            {"start_ts": 1.0, "end_ts": 3.0, "text": "버튼이 어디있지?", "silence_sec": 0.0},
            {"start_ts": 6.0, "end_ts": 8.0, "text": "모르겠다.",         "silence_sec": 3.0},
        ]

        result = calc_confusion_index(
            frame_emotions=frame_emotions,
            gaze_points=gaze_points,
            gaze_escape_ratio_per_page=gaze_escape_ratio,
            stt_segments=stt_segments,
            page_logs=DUMMY_PAGE_LOGS,
            task_results=DUMMY_TASK_RESULTS,
            heatmap_paths={1: "sessions/session_999/heatmap_page_1.png"},
            emotion_detail_json_path="sessions/session_999/detail.json",
            gaze_detail_json_path="sessions/session_999/detail.json",
        )

        ok("함수 실행 완료")
        assert isinstance(result, list) and len(result) > 0, "결과 리스트 비어있음"

        page = result[0]
        info(f"  page_no          : {page['page_no']}")
        info(f"  dominant_emotion : {page['dominant_emotion']}")
        info(f"  neg_ratio        : {page['neg_ratio']}")
        info(f"  confusion_avg    : {page['confusion_avg']}")
        info(f"  task_success_rate: {page['task_success_rate']}")
        info(f"  task_confusion   : {page['task_confusion_json']}")

        # 필드 검증
        required = [
            "page_no","url","start_video_ts","end_video_ts",
            "dominant_emotion","neg_ratio","gaze_escape_ratio",
            "confusion_avg","task_confusion_json","stt_summary",
            "avg_silence_sec","task_success_rate","avg_task_duration_sec",
            "heatmap_path","detail_json_path",
        ]
        missing = [f for f in required if f not in page]
        assert not missing, f"누락 필드: {missing}"

        # confusion_avg 범위 검증
        assert 0.0 <= page["confusion_avg"] <= 1.0, \
            f"confusion_avg 범위 오류: {page['confusion_avg']}"

        # result 영어 확인 (task_success_rate = 0.5 이어야 함, fail 1개/2개)
        assert page["task_success_rate"] == 0.5, \
            f"task_success_rate 오류: {page['task_success_rate']} (예상: 0.5)"
        ok("result 영어값('success'/'fail') 정상 처리 확인")

        ok("반환값 구조 및 값 범위 정상")
        return True
    except Exception as e:
        fail(f"confusion_index 오류: {e}")
        import traceback; traceback.print_exc()
        return False


# ════════════════════════════════════════════════════════════════
# 메인 실행
# ════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("=" * 55)
    print("2단계: AI 서버 모듈 단독 동작 테스트")
    print("=" * 55)

    results = {}

    # 2-1: config
    results["config"] = test_config()
    if not results["config"]:
        fail("config 오류 — 이후 테스트 중단")
        sys.exit(1)

    # 2-2: 더미 영상
    video_path = make_test_video()
    results["dummy_video"] = video_path is not None
    if not video_path:
        fail("영상 없음 — 분석 모듈 테스트 중단")
        sys.exit(1)

    # 2-3 ~ 2-7
    results["calibration_analysis"] = test_calibration_analysis(video_path)
    results["whisper_analysis"]     = test_whisper_analysis(video_path)
    results["emotion_analysis"]     = test_emotion_analysis(video_path)
    results["gaze_analysis"]        = test_gaze_analysis(video_path)
    results["confusion_index"]      = test_confusion_index()

    # 더미 영상 정리
    if video_path and "dummy" in video_path:
        os.remove(video_path)

    # 결과 요약
    sep()
    print("결과 요약")
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
        print(f"{GREEN}모든 모듈 정상{RESET} — 3단계로 진행하세요")
        print("  python test_step3_worker.py")
    else:
        print(f"{RED}일부 모듈 실패{RESET} — 오류 메시지 확인 후 수정하세요")
