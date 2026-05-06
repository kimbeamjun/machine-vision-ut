# calibration_analysis.py
# 캘리브레이션 영상 5개에서 MediaPipe로 홍채 좌표 추출 → calibrations 테이블 저장

import cv2
import numpy as np
import mediapipe as mp
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision as mp_vision
import os
import urllib.request

from db import save_calibrations

LEFT_IRIS_CENTER  = 473
RIGHT_IRIS_CENTER = 468

MODEL_PATH = "/tmp/face_landmarker.task"


def _ensure_model():
    if not os.path.exists(MODEL_PATH):
        print("[Calibration] face_landmarker.task 다운로드 중...")
        urllib.request.urlretrieve(
            "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task",
            MODEL_PATH,
        )
        print("[Calibration] 다운로드 완료")


def _extract_iris_from_video(video_path: str) -> tuple[float, float] | None:
    """
    짧은 캘리브레이션 영상에서 홍채 좌표 평균값 추출
    중간 프레임들의 홍채 좌표를 평균내어 안정적인 값 반환
    """
    _ensure_model()

    base_options = mp_python.BaseOptions(model_asset_path=MODEL_PATH)
    face_options = mp_vision.FaceLandmarkerOptions(
        base_options=base_options,
        output_face_blendshapes=False,
        output_facial_transformation_matrixes=False,
        num_faces=1,
        min_face_detection_confidence=0.5,
        min_face_presence_confidence=0.5,
        min_tracking_confidence=0.5,
        running_mode=mp_vision.RunningMode.VIDEO,
    )
    face_landmarker = mp_vision.FaceLandmarker.create_from_options(face_options)

    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    # 앞뒤 20% 프레임 제외, 중간 60% 구간만 사용 (눈 깜빡임 등 노이즈 제거)
    start_frame = int(total_frames * 0.2)
    end_frame   = int(total_frames * 0.8)

    gaze_xs, gaze_ys = [], []
    frame_idx = 0

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        if start_frame <= frame_idx <= end_frame:
            ts_ms  = int((frame_idx / fps) * 1000)
            rgb    = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            mp_img = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
            result = face_landmarker.detect_for_video(mp_img, ts_ms)

            if result.face_landmarks:
                lm = result.face_landmarks[0]
                lx = lm[LEFT_IRIS_CENTER].x
                ly = lm[LEFT_IRIS_CENTER].y
                rx = lm[RIGHT_IRIS_CENTER].x
                ry = lm[RIGHT_IRIS_CENTER].y
                gaze_xs.append((lx + rx) / 2)
                gaze_ys.append((ly + ry) / 2)

        frame_idx += 1

    cap.release()
    face_landmarker.close()

    if not gaze_xs:
        return None  # 얼굴 미감지

    return float(np.mean(gaze_xs)), float(np.mean(gaze_ys))


def run_calibration_analysis(
    session_id: int,
    calibration_videos: list[dict],
) -> dict:
    """
    캘리브레이션 영상 5개 분석

    Args:
        session_id: 세션 ID
        calibration_videos: [
            {
                "point_no": 1,          # 1~5
                "screen_x": 0.1,        # 화면 기준점 x (비율)
                "screen_y": 0.1,        # 화면 기준점 y (비율)
                "local_path": "/tmp/cal_1.mp4"  # 로컬 다운로드 경로
            }, ...
        ]

    Returns:
        {
            "success": True,
            "calibrations": [{"point_no", "screen_x", "screen_y", "gaze_x", "gaze_y"}, ...]
            "failed_points": [2, 4]  # 얼굴 미감지된 포인트 번호
        }
    """
    calibrations  = []
    failed_points = []

    for cal in sorted(calibration_videos, key=lambda x: x["point_no"]):
        point_no  = cal["point_no"]
        screen_x  = cal["screen_x"]
        screen_y  = cal["screen_y"]
        video_path = cal["local_path"]

        print(f"[Calibration] point {point_no} 분석 중...")
        result = _extract_iris_from_video(video_path)

        if result is None:
            print(f"[Calibration] point {point_no} 얼굴 미감지 → 실패")
            failed_points.append(point_no)
            continue

        gaze_x, gaze_y = result
        calibrations.append({
            "point_no": point_no,
            "screen_x": round(screen_x, 4),
            "screen_y": round(screen_y, 4),
            "gaze_x":   round(gaze_x, 6),
            "gaze_y":   round(gaze_y, 6),
        })
        print(f"[Calibration] point {point_no} 완료: gaze=({gaze_x:.4f}, {gaze_y:.4f})")

    # DB 저장
    if calibrations:
        save_calibrations(session_id, calibrations)

    return {
        "success":        len(failed_points) == 0,
        "calibrations":   calibrations,
        "failed_points":  failed_points,
    }
