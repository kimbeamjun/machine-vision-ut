# ai_server/analysis/whisper_analysis.py
# 음성 분석: ffmpeg 오디오 추출 → Whisper medium STT → stt_segments DB 저장

import os
import subprocess
import tempfile
import whisper

from db import save_stt_segments

# Whisper 모델 싱글턴 (프로세스당 1회 로드)
_whisper_model = None

def _get_whisper_model():
    global _whisper_model
    if _whisper_model is None:
        _whisper_model = whisper.load_model("medium")
    return _whisper_model


def _extract_audio(video_path: str, audio_path: str):
    """ffmpeg로 영상에서 오디오 트랙 추출 → wav 변환"""
    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-vn",                     # 비디오 스트림 제외
        "-acodec", "pcm_s16le",    # Whisper가 선호하는 16-bit PCM
        "-ar", "16000",            # 16kHz 샘플레이트
        "-ac", "1",                # 모노
        audio_path,
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=300)
    if result.returncode != 0 or not os.path.exists(audio_path) or os.path.getsize(audio_path) == 0:
        raise RuntimeError(f"ffmpeg 오디오 추출 실패: {result.stderr.decode()}")


def _calculate_silence(segments: list[dict]) -> list[dict]:
    """
    발화 구간 간 공백 시간(silence_sec) 계산
    첫 번째 발화 silence_sec = 0.0 (팀 내 합의 사항)
    """
    result = []
    for i, seg in enumerate(segments):
        if i == 0:
            silence = 0.0
        else:
            silence = max(0.0, seg["start_ts"] - segments[i - 1]["end_ts"])
        result.append({**seg, "silence_sec": round(silence, 3)})
    return result


def run_whisper_analysis(
    video_local_path: str,
    session_id: int,
    timeout_sec: int = 600,
) -> dict:
    """
    음성 분석 메인 함수

    Args:
        video_local_path: 로컬 영상 경로
        session_id: 세션 ID
        timeout_sec: Whisper 타임아웃 (초)

    Returns:
        {
            "stt_segments": [{"start_ts", "end_ts", "text", "silence_sec"}, ...],
            "skipped": bool  # 타임아웃 시 True
        }
    """
    # 임시 오디오 파일
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_audio:
        audio_path = tmp_audio.name

    try:
        # 1. ffmpeg 오디오 추출
        _extract_audio(video_local_path, audio_path)

        # 2. Whisper STT
        model = _get_whisper_model()
        result = model.transcribe(
            audio_path,
            language="ko",
            word_timestamps=False,
            no_speech_threshold=0.6,   # VAD: no_speech_prob > 0.6 필터
            condition_on_previous_text=True,
            verbose=False,
        )

        # 3. 발화 구간 파싱
        raw_segments = []
        for seg in result.get("segments", []):
            # no_speech_prob 필터 (VAD)
            if seg.get("no_speech_prob", 0.0) > 0.6:
                continue
            raw_segments.append({
                "start_ts": round(float(seg["start"]), 3),
                "end_ts":   round(float(seg["end"]),   3),
                "text":     seg.get("text", "").strip(),
            })

        # 4. silence_sec 계산
        stt_segments = _calculate_silence(raw_segments)

        # 5. DB 저장 (텍스트 기반 — 행 수 적어 DB 유지)
        save_stt_segments(session_id, stt_segments)

        return {
            "stt_segments": stt_segments,
            "skipped":      False,
        }

    except subprocess.TimeoutExpired:
        print(f"[Whisper] session_id={session_id} 타임아웃 — 음성 분석 생략")
        save_stt_segments(session_id, [])   # 빈 값으로 저장
        return {"stt_segments": [], "skipped": True}

    except Exception as e:
        print(f"[Whisper] session_id={session_id} 오류: {e}")
        save_stt_segments(session_id, [])
        return {"stt_segments": [], "skipped": True}

    finally:
        if os.path.exists(audio_path):
            os.remove(audio_path)
