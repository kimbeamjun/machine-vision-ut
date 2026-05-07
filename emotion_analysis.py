# emotion_analysis.py
# 표정 분석: EfficientNet-B2 → MinIO JSON 저장
# 학습 클래스: negative / positive / surprise (3개)

import json
import numpy as np
import cv2
import torch
import torch.nn.functional as F

from config import EMOTION_MODEL_PATH, FRAME_BATCH_SIZE
from emotion_model import EmotionModel, TRAINED_CLASSES, load_model
from minio_client import upload_json

# 이미지 전처리 상수
MEAN     = np.array([0.485, 0.456, 0.406], dtype=np.float32)
STD      = np.array([0.229, 0.224, 0.225], dtype=np.float32)
IMG_SIZE = 260

# negative 판단 임계값: 최고 확률이 이 미만이면 neutral로 후처리
NEUTRAL_THRESHOLD = 0.5


def _preprocess_frame(frame_bgr: np.ndarray) -> np.ndarray:
    """BGR 프레임 → 정규화된 float32 (C, H, W)"""
    rgb     = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
    resized = cv2.resize(rgb, (IMG_SIZE, IMG_SIZE))
    norm    = (resized.astype(np.float32) / 255.0 - MEAN) / STD
    return norm.transpose(2, 0, 1)   # (C, H, W)


def _infer_batch(model, frames: list, timestamps: list, device) -> list[dict]:
    """
    frames: [(C,H,W), ...] 길이 FRAME_BATCH_SIZE
    반환: [{"timestamp", "emotion", "confidence"}, ...]
    """
    # (1, seq_len, C, H, W)
    tensor = torch.tensor(np.stack(frames)).unsqueeze(0).to(device)
    with torch.no_grad():
        logits = model(tensor)              # (1, seq_len, 3)
    probs = F.softmax(logits[0], dim=-1).cpu().numpy()  # (seq_len, 3)

    results = []
    for ts, prob in zip(timestamps, probs):
        idx        = int(prob.argmax())
        confidence = float(prob[idx])
        emotion    = TRAINED_CLASSES[idx]

        # neutral 후처리: 확신도 낮은 negative → neutral
        if emotion == "negative" and confidence < NEUTRAL_THRESHOLD:
            emotion = "neutral"

        results.append({
            "timestamp":  round(ts, 3),
            "emotion":    emotion,
            "confidence": round(confidence, 4),
        })
    return results


def run_emotion_analysis(video_local_path: str, session_id: int) -> dict:
    """
    표정 분석 메인 함수

    Args:
        video_local_path : 로컬에 다운로드된 영상 경로
        session_id       : 세션 ID (MinIO 경로 생성용)

    Returns:
        {
            "detail_json_path": "raw/session_1/emotion_details.json",
            "frame_emotions"  : [{"timestamp","emotion","confidence"}, ...],
        }
    """
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model  = load_model(EMOTION_MODEL_PATH, device)

    cap = cv2.VideoCapture(video_local_path)
    if not cap.isOpened():
        raise RuntimeError(f"영상을 열 수 없습니다: {video_local_path}")

    fps       = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frame_emotions = []
    batch_frames   = []
    batch_ts       = []
    frame_idx      = 0

    while True:
        ret, frame = cap.read()
        if not ret:
            break
        ts = frame_idx / fps
        batch_frames.append(_preprocess_frame(frame))
        batch_ts.append(ts)
        frame_idx += 1

        if len(batch_frames) == FRAME_BATCH_SIZE:
            frame_emotions.extend(_infer_batch(model, batch_frames, batch_ts, device))
            batch_frames, batch_ts = [], []

    # 마지막 남은 프레임 처리 (마지막 프레임으로 패딩)
    if batch_frames:
        pad   = FRAME_BATCH_SIZE - len(batch_frames)
        valid = len(batch_frames)
        batch_frames += [batch_frames[-1]] * pad
        batch_ts     += [batch_ts[-1]]     * pad
        results = _infer_batch(model, batch_frames, batch_ts, device)
        frame_emotions.extend(results[:valid])   # 패딩 부분 제거

    cap.release()

    # ── MinIO JSON 저장 ─────────────────────────────────────────
    object_key = f"sessions/session_{session_id}/detail.json"
    payload    = json.dumps(frame_emotions, ensure_ascii=False).encode("utf-8")
    upload_json(object_key, payload)

    print(f"[Emotion] session_id={session_id} | "
          f"총 {len(frame_emotions)}프레임 분석 완료 → {object_key}")

    return {
        "detail_json_path": object_key,
        "frame_emotions":   frame_emotions,
    }
