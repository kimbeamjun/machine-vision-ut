"""
test_integration.py
전체 흐름 통합 테스트: CL-1 ~ CL-8 순서대로 메인 서버에 실제 HTTP 요청

실행 방법:
    python test_integration.py

사전 조건:
    - 메인 서버 실행 중 (10.10.10.113:8000)
    - MinIO 실행 중 (10.10.10.113:9000)
    - pip install requests minio
"""

import requests
import time
import os
import tempfile

# ── 설정 ────────────────────────────────────────────────────────
MAIN_SERVER = "http://10.10.10.113:8000"
MINIO_HOST  = "10.10.10.113:9000"

# 테스트용 더미 영상 생성 (실제 테스트 시 실제 영상 경로로 교체)
DUMMY_VIDEO_PATH    = None   # None이면 더미 바이너리 사용
DUMMY_CALIB_PATH    = None   # None이면 더미 바이너리 사용

# ── 색상 출력 ────────────────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
RESET  = "\033[0m"

def ok(msg):   print(f"{GREEN}[PASS]{RESET} {msg}")
def fail(msg): print(f"{RED}[FAIL]{RESET} {msg}")
def info(msg): print(f"{YELLOW}[INFO]{RESET} {msg}")
def sep():     print("-" * 60)


def get_dummy_video_bytes(size_kb=10):
    """테스트용 더미 바이너리 (실제 영상 없을 때 사용)"""
    return b"\x00" * (size_kb * 1024)


# ════════════════════════════════════════════════════════════════
# CL-1. 세션 생성
# ════════════════════════════════════════════════════════════════
def test_cl1_create_session():
    sep()
    info("CL-1: 세션 생성")
    try:
        resp = requests.post(
            f"{MAIN_SERVER}/api/v1/sessions",
            json={"viewport_region": {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}},
            timeout=10,
        )
        assert resp.status_code == 200, f"status={resp.status_code}"
        data = resp.json()
        assert "session_id" in data, f"응답에 session_id 없음: {data}"
        session_id = data["session_id"]
        ok(f"세션 생성 완료 → session_id={session_id}")
        return session_id
    except Exception as e:
        fail(f"CL-1 실패: {e}")
        return None


# ════════════════════════════════════════════════════════════════
# CL-2 + CL-3. 캘리브레이션 Presigned URL 발급 + MinIO 업로드
# ════════════════════════════════════════════════════════════════
def test_cl2_cl3_calibration_upload(session_id):
    sep()
    info("CL-2/3: 캘리브레이션 영상 Presigned URL 발급 + 업로드 (5개 포인트)")
    
    # 5개 포인트 좌표 (화면 비율)
    points = [
        {"point_no": 1, "screen_x": 0.1, "screen_y": 0.1},
        {"point_no": 2, "screen_x": 0.9, "screen_y": 0.1},
        {"point_no": 3, "screen_x": 0.5, "screen_y": 0.5},
        {"point_no": 4, "screen_x": 0.1, "screen_y": 0.9},
        {"point_no": 5, "screen_x": 0.9, "screen_y": 0.9},
    ]

    object_keys = []

    for pt in points:
        try:
            # CL-2: Presigned URL 발급
            resp = requests.post(
                f"{MAIN_SERVER}/api/v1/sessions/{session_id}/calibrate/presigned-url",
                json=pt,
                timeout=10,
            )
            assert resp.status_code == 200, f"status={resp.status_code} body={resp.text}"
            data = resp.json()
            assert "presigned_url" in data and "object_key" in data, f"응답 형식 오류: {data}"

            presigned_url = data["presigned_url"]
            object_key    = data["object_key"]
            ok(f"  point {pt['point_no']} Presigned URL 발급 → {object_key}")

            # CL-3: MinIO 직접 업로드
            video_bytes = (
                open(DUMMY_CALIB_PATH, "rb").read()
                if DUMMY_CALIB_PATH and os.path.exists(DUMMY_CALIB_PATH)
                else get_dummy_video_bytes()
            )
            upload_resp = requests.put(
                presigned_url,
                data=video_bytes,
                headers={"Content-Type": "video/mp4"},
                timeout=30,
            )
            assert upload_resp.status_code in (200, 204), \
                f"MinIO 업로드 실패: status={upload_resp.status_code}"
            ok(f"  point {pt['point_no']} MinIO 업로드 완료")

            object_keys.append({**pt, "object_key": object_key})

        except Exception as e:
            fail(f"  point {pt['point_no']} 실패: {e}")
            return None

    return object_keys


# ════════════════════════════════════════════════════════════════
# CL-4. 캘리브레이션 분석 시작 (동기 응답 대기)
# ════════════════════════════════════════════════════════════════
def test_cl4_calibration_start(session_id, object_keys):
    sep()
    info("CL-4: 캘리브레이션 분석 시작 요청 (AI 분석 완료까지 대기)")
    try:
        payload = {
            "calibration_points": [
                {
                    "point_no":   p["point_no"],
                    "screen_x":   p["screen_x"],
                    "screen_y":   p["screen_y"],
                    "object_key": p["object_key"],
                }
                for p in object_keys
            ]
        }
        # 동기 응답 — AI 분석 완료까지 대기 (타임아웃 넉넉하게)
        resp = requests.post(
            f"{MAIN_SERVER}/api/v1/sessions/{session_id}/calibrate/start",
            json=payload,
            timeout=120,
        )
        assert resp.status_code == 200, f"status={resp.status_code} body={resp.text}"
        data = resp.json()
        status = data.get("status")

        if status == "success":
            failed = data.get("failed_points", [])
            ok(f"캘리브레이션 성공 (실패 포인트: {failed})")
            return True
        elif status == "failed":
            fail(f"캘리브레이션 실패 → 재촬영 필요. failed_points={data.get('failed_points')}")
            return False
        else:
            fail(f"예상치 못한 status: {data}")
            return False

    except requests.Timeout:
        fail("CL-4 타임아웃 — AI 서버 응답 없음 (120초 초과)")
        return False
    except Exception as e:
        fail(f"CL-4 실패: {e}")
        return False


# ════════════════════════════════════════════════════════════════
# CL-5. 메타데이터 전송
# ════════════════════════════════════════════════════════════════
def test_cl5_metadata(session_id):
    sep()
    info("CL-5: 테스트 메타데이터 전송")
    try:
        resp = requests.post(
            f"{MAIN_SERVER}/api/v1/sessions/{session_id}/metadata",
            json={
                "page_logs": [
                    {
                        "page_no":         1,
                        "url":             "https://example.com/test",
                        "start_video_ts":  0.0,
                        "end_video_ts":    15.5,
                        "screenshot_path": f"sessions/session_{session_id}/screenshot_1.png",
                    }
                ],
                "task_results": [
                    {"task_order": 1, "result": "success", "duration_sec": 12.3},
                    {"task_order": 2, "result": "fail",    "duration_sec": 30.0},
                ],
            },
            timeout=10,
        )
        assert resp.status_code == 200, f"status={resp.status_code} body={resp.text}"
        data = resp.json()
        assert data.get("saved") == True or data.get("status") == "ok", f"응답 오류: {data}"
        ok("메타데이터 전송 완료")
        return True
    except Exception as e:
        fail(f"CL-5 실패: {e}")
        return False


# ════════════════════════════════════════════════════════════════
# CL-6 + CL-7. 녹화 영상 Presigned URL 발급 + 업로드
# ════════════════════════════════════════════════════════════════
def test_cl6_cl7_recording_upload(session_id):
    sep()
    info("CL-6/7: 녹화 영상 Presigned URL 발급 + 업로드")
    try:
        # CL-6: Presigned URL 발급
        resp = requests.post(
            f"{MAIN_SERVER}/api/v1/sessions/{session_id}/presigned-url",
            json={"file_type": "recording"},
            timeout=10,
        )
        assert resp.status_code == 200, f"status={resp.status_code} body={resp.text}"
        data = resp.json()
        assert "presigned_url" in data and "object_key" in data, f"응답 형식 오류: {data}"
        ok(f"Presigned URL 발급 → {data['object_key']}")

        # CL-7: MinIO 직접 업로드
        video_bytes = (
            open(DUMMY_VIDEO_PATH, "rb").read()
            if DUMMY_VIDEO_PATH and os.path.exists(DUMMY_VIDEO_PATH)
            else get_dummy_video_bytes(size_kb=100)
        )
        upload_resp = requests.put(
            data["presigned_url"],
            data=video_bytes,
            headers={"Content-Type": "video/mp4"},
            timeout=60,
        )
        assert upload_resp.status_code in (200, 204), \
            f"MinIO 업로드 실패: status={upload_resp.status_code}"
        ok("녹화 영상 MinIO 업로드 완료")
        return True

    except Exception as e:
        fail(f"CL-6/7 실패: {e}")
        return False


# ════════════════════════════════════════════════════════════════
# CL-8. 본 분석 시작 요청
# ════════════════════════════════════════════════════════════════
def test_cl8_analyze(session_id):
    sep()
    info("CL-8: 본 분석 시작 요청")
    try:
        resp = requests.post(
            f"{MAIN_SERVER}/api/v1/sessions/{session_id}/analyze",
            timeout=10,
        )
        assert resp.status_code == 202, f"status={resp.status_code} body={resp.text}"
        data = resp.json()
        assert data.get("status") == "accepted", f"응답 오류: {data}"
        ok("분석 시작 요청 완료 → 백그라운드 처리 중")
        return True
    except Exception as e:
        fail(f"CL-8 실패: {e}")
        return False


# ════════════════════════════════════════════════════════════════
# 메인 실행
# ════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("=" * 60)
    print("UDT 통합 테스트 — 클라이언트 → 메인 서버 전체 흐름")
    print(f"대상 서버: {MAIN_SERVER}")
    print("=" * 60)

    results = {}

    # CL-1
    session_id = test_cl1_create_session()
    results["CL-1"] = session_id is not None
    if not session_id:
        fail("session_id 없음 — 이후 테스트 중단")
        exit(1)

    # CL-2 + CL-3
    object_keys = test_cl2_cl3_calibration_upload(session_id)
    results["CL-2/3"] = object_keys is not None
    if not object_keys:
        fail("캘리브레이션 업로드 실패 — 이후 테스트 중단")
        exit(1)

    # CL-4
    calib_ok = test_cl4_calibration_start(session_id, object_keys)
    results["CL-4"] = calib_ok
    if not calib_ok:
        fail("캘리브레이션 실패 — 재촬영 필요. 이후 테스트 중단")
        exit(1)

    # CL-5
    results["CL-5"] = test_cl5_metadata(session_id)

    # CL-6 + CL-7
    results["CL-6/7"] = test_cl6_cl7_recording_upload(session_id)

    # CL-8
    results["CL-8"] = test_cl8_analyze(session_id)

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
        print(f"{GREEN}모든 테스트 통과{RESET} — session_id={session_id}")
    else:
        print(f"{RED}일부 테스트 실패{RESET}")

    print(f"\n※ 본 분석(CL-8)은 백그라운드 처리 중입니다.")
    print(f"  AI 서버 Worker 로그에서 session_id={session_id} 진행 상황 확인하세요.")
