# gaze_analysis.py
# 시선 분석: MediaPipe FaceLandmarker (0.10.x) + scipy calibration → 히트맵 생성

import json
import io
import numpy as np
import cv2
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy.interpolate import LinearNDInterpolator, NearestNDInterpolator
import mediapipe as mp
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision as mp_vision

from minio_client import upload_json, upload_image, download_screenshot

# ── MediaPipe 랜드마크 인덱스 (홍채 중심) ────────────────────────
# FaceMesh 기준: 왼쪽 홍채 중심=473, 오른쪽 홍채 중심=468
LEFT_IRIS_CENTER  = 473
RIGHT_IRIS_CENTER = 468


def _build_gaze_mapper(calibrations: list[dict]):
    """
    캘리브레이션 5쌍 → (gaze_x, gaze_y) → (screen_x, screen_y) 매핑 함수
    LinearNDInterpolator + NearestNDInterpolator (외삽 fallback)
    """
    gaze_pts   = np.array([[c["gaze_x"],   c["gaze_y"]]   for c in calibrations])
    screen_pts = np.array([[c["screen_x"], c["screen_y"]] for c in calibrations])

    lin_mapper     = LinearNDInterpolator(gaze_pts, screen_pts)
    nearest_mapper = NearestNDInterpolator(gaze_pts, screen_pts)

    def mapper(gx: float, gy: float) -> tuple[float, float]:
        result = lin_mapper([[gx, gy]])[0]
        if np.any(np.isnan(result)):
            result = nearest_mapper([[gx, gy]])[0]
        sx = float(np.clip(result[0], 0.0, 1.0))
        sy = float(np.clip(result[1], 0.0, 1.0))
        return sx, sy

    return mapper


def _get_page_no(ts: float, page_logs: list[dict]) -> int | None:
    """프레임 타임스탬프 → 해당 page_no 결정"""
    for pl in page_logs:
        start = pl["start_video_ts"]
        end   = pl["end_video_ts"]
        if end is None:
            end = float("inf")
        if start <= ts < end:
            return pl["page_no"]
    return None


def _generate_heatmap(
    gaze_points: list[dict],
    page_no: int,
    screenshot_bytes: bytes | None,
) -> bytes:
    """페이지별 시선 히트맵 생성 → PNG bytes 반환"""
    fig, ax = plt.subplots(figsize=(12, 7), dpi=100)

    if screenshot_bytes:
        nparr  = np.frombuffer(screenshot_bytes, np.uint8)
        bg     = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        bg_rgb = cv2.cvtColor(bg, cv2.COLOR_BGR2RGB)
        ax.imshow(bg_rgb, extent=[0, 1, 1, 0], aspect="auto", zorder=0)
    else:
        ax.set_facecolor("white")

    pts = [(p["x"], p["y"]) for p in gaze_points if p.get("page_no") == page_no]
    if pts:
        xs, ys = zip(*pts)
        ax.scatter(xs, ys, c="red", s=5, alpha=0.3, zorder=1)
        try:
            from scipy.stats import gaussian_kde
            xy     = np.vstack([xs, ys])
            kde    = gaussian_kde(xy, bw_method=0.08)
            xi     = np.linspace(0, 1, 200)
            yi     = np.linspace(0, 1, 200)
            Xi, Yi = np.meshgrid(xi, yi)
            Zi     = kde(np.vstack([Xi.ravel(), Yi.ravel()])).reshape(Xi.shape)
            ax.contourf(Xi, Yi, Zi, levels=15, cmap="hot", alpha=0.5, zorder=2)
        except Exception:
            pass

    ax.set_xlim(0, 1)
    ax.set_ylim(1, 0)
    ax.set_title(f"Page {page_no} Gaze Heatmap")
    ax.axis("off")

    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", pad_inches=0)
    plt.close(fig)
    buf.seek(0)
    return buf.read()


def run_gaze_analysis(
    video_local_path: str,
    session_id: int,
    calibrations: list[dict],
    viewport_region: dict,
    page_logs: list[dict],
) -> dict:
    """
    시선 분석 메인 함수 (mediapipe 0.10.x 호환)

    Returns:
        {
            "detail_json_path": str,
            "heatmap_paths": {page_no: str},
            "gaze_points": [...],
            "gaze_escape_ratio_per_page": {page_no: float},
        }
    """
    mapper = _build_gaze_mapper(calibrations)

    vx = viewport_region.get("x", 0.0)
    vy = viewport_region.get("y", 0.0)
    vw = viewport_region.get("w", 1.0)
    vh = viewport_region.get("h", 1.0)

    # ── MediaPipe 0.10.x FaceLandmarker 초기화 ───────────────────
    # 모델 파일 자동 다운로드
    import urllib.request, os
    model_path = "/tmp/face_landmarker.task"
    if not os.path.exists(model_path):
        print("[Gaze] face_landmarker.task 다운로드 중...")
        urllib.request.urlretrieve(
            "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task",
            model_path,
        )
        print("[Gaze] 다운로드 완료")

    base_options    = mp_python.BaseOptions(model_asset_path=model_path)
    face_options    = mp_vision.FaceLandmarkerOptions(
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

    cap = cv2.VideoCapture(video_local_path)
    if not cap.isOpened():
        raise RuntimeError(f"영상을 열 수 없습니다: {video_local_path}")

    fps         = cap.get(cv2.CAP_PROP_FPS) or 30.0
    gaze_points = []
    frame_idx   = 0

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        ts      = frame_idx / fps
        page_no = _get_page_no(ts, page_logs)
        frame_idx += 1

        # mediapipe 0.10.x: mp.Image 형식으로 변환
        rgb        = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mp_image   = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        timestamp_ms = int(ts * 1000)

        result = face_landmarker.detect_for_video(mp_image, timestamp_ms)

        if not result.face_landmarks:
            continue

        landmarks = result.face_landmarks[0]

        # 홍채 중심 (왼쪽 + 오른쪽 평균)
        lx = landmarks[LEFT_IRIS_CENTER].x
        ly = landmarks[LEFT_IRIS_CENTER].y
        rx = landmarks[RIGHT_IRIS_CENTER].x
        ry = landmarks[RIGHT_IRIS_CENTER].y
        gaze_raw_x = (lx + rx) / 2
        gaze_raw_y = (ly + ry) / 2

        # 캘리브레이션 매핑 → 화면 비율 좌표
        sx, sy = mapper(gaze_raw_x, gaze_raw_y)

        # 시선 이탈 판단
        in_viewport = (vx <= sx <= vx + vw) and (vy <= sy <= vy + vh)

        gaze_points.append({
            "timestamp": round(ts, 3),
            "x":         round(sx, 4),
            "y":         round(sy, 4),
            "page_no":   page_no,
            "escaped":   not in_viewport,
        })

    cap.release()
    face_landmarker.close()

    # ── 히트맵 생성 (페이지별) ────────────────────────────────────
    heatmap_paths = {}
    for pl in page_logs:
        pno       = pl["page_no"]
        scr_path  = pl.get("screenshot_path")
        try:
            scr_bytes = download_screenshot(scr_path) if scr_path else None
        except Exception:
            scr_bytes = None

        heatmap_bytes = _generate_heatmap(gaze_points, pno, scr_bytes)
        obj_key       = f"heatmaps/session_{session_id}/page_{pno}_heatmap.png"
        upload_image(obj_key, heatmap_bytes)
        heatmap_paths[pno] = obj_key

    # ── 페이지별 시선 이탈 비율 ──────────────────────────────────
    gaze_escape_ratio_per_page = {}
    for pl in page_logs:
        pno  = pl["page_no"]
        pts  = [g for g in gaze_points if g.get("page_no") == pno]
        if pts:
            escaped = sum(1 for g in pts if g["escaped"])
            gaze_escape_ratio_per_page[pno] = round(escaped / len(pts), 4)
        else:
            gaze_escape_ratio_per_page[pno] = 0.0

    # ── MinIO JSON 저장 ──────────────────────────────────────────
    detail_key = f"raw/session_{session_id}/gaze_details.json"
    payload    = json.dumps(gaze_points, ensure_ascii=False).encode("utf-8")
    upload_json(detail_key, payload)

    # DB용 (escaped 키 제거)
    gaze_for_db = [
        {"timestamp": g["timestamp"], "x": g["x"], "y": g["y"], "page_no": g["page_no"]}
        for g in gaze_points
    ]

    print(f"[Gaze] session_id={session_id} | "
          f"총 {len(gaze_for_db)}개 시선 포인트 → {detail_key}")

    return {
        "detail_json_path":           detail_key,
        "heatmap_paths":              heatmap_paths,
        "gaze_points":                gaze_for_db,
        "gaze_escape_ratio_per_page": gaze_escape_ratio_per_page,
    }
