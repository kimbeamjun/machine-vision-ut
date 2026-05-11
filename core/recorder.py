import cv2
import numpy as np
import os
import subprocess
import threading
import time
import wave
import importlib
from mss import mss
from PySide6.QtCore import QObject, Signal
from typing import Any

# sounddevice는 선택적 의존성 — 없으면 영상만 녹화하고 경고 출력
try:
    import sounddevice as sd
    _AUDIO_AVAILABLE = True
except ImportError:
    _AUDIO_AVAILABLE = False
    print("⚠️  sounddevice 패키지가 없습니다. 오디오 녹음 없이 영상만 녹화합니다.")
    print("    설치: pip install sounddevice")

try:
    _imageio_ffmpeg = importlib.import_module("imageio_ffmpeg")
    _FFMPEG_EXE = _imageio_ffmpeg.get_ffmpeg_exe()
except ImportError:
    _FFMPEG_EXE = "ffmpeg"


class ScreenRecorder(QObject):
    recording_finished = Signal(str)

    # 오디오 설정 상수
    AUDIO_SAMPLE_RATE = 44100
    AUDIO_CHANNELS = 1         # 모노 (STT 분석에 충분)
    AUDIO_DTYPE = "float32"

    def __init__(self):
        super().__init__()
        self.is_recording = False
        self.writer = None
        self.start_time = 0.0
        self.end_time = 0.0

        # 오디오 관련 내부 상태
        self._audio_frames: list = []
        self._audio_thread: threading.Thread | None = None
        self._audio_wav_path: str = ""
        self.last_audio_path: str = ""

    # ──────────────────────────────────────────────
    # 공개 인터페이스
    # ──────────────────────────────────────────────

    def start(self, pixel_region: dict, output_path: str, fps: int = 20):
        """
        화면 녹화(+오디오 녹음)를 시작합니다.

        :param pixel_region: 물리 픽셀 좌표 {"left", "top", "width", "height"}
                             또는 비율 좌표 {"x", "y", "w", "h"}
        :param output_path:  최종 출력 파일 경로 (.mp4)
        :param fps:          녹화 프레임 레이트 (기본 20)

        ※ 이 메서드는 블로킹 호출입니다. RecordingWorker 스레드에서 실행하세요.
        """
        self.is_recording = True
        self.start_time = time.time()
        self.end_time = 0.0
        self._audio_frames = []
        self.last_audio_path = ""

        # ── 1. 오디오 녹음 스레드 시작 ──────────────────
        self._audio_wav_path = output_path.replace(".mp4", "_audio_tmp.wav")
        if _AUDIO_AVAILABLE:
            self._audio_thread = threading.Thread(
                target=self._record_audio,
                args=(self._audio_wav_path,),
                daemon=True,
                name="AudioRecorder",
            )
            self._audio_thread.start()
            print("🎙️  오디오 녹음 시작")
        else:
            self._audio_thread = None

        # ── 2. 화면 녹화 루프 (블로킹) ──────────────────
        video_only_path = output_path  # 기본값: 오디오가 없으면 이게 최종 파일
        fourcc = cv2.VideoWriter.fourcc(*"mp4v")

        with mss() as sct:
            monitor = self._resolve_capture_region(pixel_region, sct)

            self.writer = cv2.VideoWriter(
                output_path,
                fourcc,
                fps,
                (monitor["width"], monitor["height"]),
            )

            while self.is_recording:
                frame_start = time.perf_counter()
                img = sct.grab(monitor)
                frame = np.array(img)
                frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
                self.writer.write(frame)

                elapsed = time.perf_counter() - frame_start
                sleep_time = (1.0 / fps) - elapsed
                if sleep_time > 0:
                    time.sleep(sleep_time)

        if self.writer:
            self.writer.release()
            self.writer = None
        print(f"📹 화면 녹화 종료: {output_path}")

        # ── 3. 오디오 스레드 종료 대기 ──────────────────
        if self._audio_thread is not None:
            self._audio_thread.join(timeout=5.0)
            print(f"🎙️  오디오 녹음 종료: {self._audio_wav_path}")

        # ── 4. 영상 + 오디오 병합 ────────────────────────
        final_path = self._merge_audio_video(output_path, self._audio_wav_path)

        self.recording_finished.emit(final_path)

    def stop(self):
        """녹화를 중지합니다. 오디오 스레드도 자동으로 멈춥니다."""
        if self.is_recording and self.start_time:
            self.end_time = time.time()
        self.is_recording = False

    def get_elapsed_time(self) -> float:
        """
        녹화 시작 시점부터 현재까지 경과된 시간(초)을 반환합니다.
        로그 기록 시 start_video_ts 계산에 사용됩니다.
        """
        if self.start_time == 0.0:
            return 0.0
        if self.is_recording:
            return time.time() - self.start_time
        if self.end_time:
            return self.end_time - self.start_time
        return time.time() - self.start_time

    # ──────────────────────────────────────────────
    # 내부 메서드
    # ──────────────────────────────────────────────

    @staticmethod
    def _resolve_capture_region(region: dict, sct: Any) -> dict:
        """
        UI에서 들어오는 비율 좌표 또는 픽셀 좌표를 mss 캡처용 물리 픽셀 영역으로 변환합니다.
        mp4 인코더가 홀수 크기에서 가장자리를 잘라내는 경우가 있어 폭/높이는 짝수로 맞춥니다.
        """
        primary = sct.monitors[1]
        screen_left = int(primary["left"])
        screen_top = int(primary["top"])
        screen_width = int(primary["width"])
        screen_height = int(primary["height"])

        if {"left", "top", "width", "height"}.issubset(region.keys()):
            left = int(region["left"])
            top = int(region["top"])
            width = int(region["width"])
            height = int(region["height"])
        else:
            left = screen_left + int(float(region.get("x", 0.0)) * screen_width)
            top = screen_top + int(float(region.get("y", 0.0)) * screen_height)
            width = int(float(region.get("w", 1.0)) * screen_width)
            height = int(float(region.get("h", 1.0)) * screen_height)

        left = max(screen_left, min(left, screen_left + screen_width - 2))
        top = max(screen_top, min(top, screen_top + screen_height - 2))
        right = max(left + 2, min(left + width, screen_left + screen_width))
        bottom = max(top + 2, min(top + height, screen_top + screen_height))

        width = right - left
        height = bottom - top
        if width % 2:
            width -= 1
        if height % 2:
            height -= 1

        return {
            "left": left,
            "top": top,
            "width": max(2, width),
            "height": max(2, height),
        }

    def _record_audio(self, wav_path: str):
        """
        sounddevice InputStream 콜백 방식으로 기본 마이크를 녹음합니다.
        is_recording 플래그가 False가 될 때까지 계속 캡처합니다.
        """
        frames: list[np.ndarray] = []

        def _callback(indata: np.ndarray, frame_count: int, time_info, status):
            if status:
                print(f"  [오디오 콜백] {status}")
            if self.is_recording:
                frames.append(indata.copy())

        try:
            with sd.InputStream(
                samplerate=self.AUDIO_SAMPLE_RATE,
                channels=self.AUDIO_CHANNELS,
                dtype=self.AUDIO_DTYPE,
                callback=_callback,
            ):
                while self.is_recording:
                    time.sleep(0.05)
        except Exception as exc:
            print(f"⚠️  오디오 녹음 중 오류: {exc}")
            return

        if not frames:
            print("⚠️  녹음된 오디오 프레임이 없습니다.")
            return

        # float32 → int16 변환 후 WAV 파일 저장
        audio_data = np.concatenate(frames, axis=0)
        pcm_int16 = (audio_data * 32767.0).clip(-32768, 32767).astype(np.int16)

        try:
            with wave.open(wav_path, "wb") as wf:
                wf.setnchannels(self.AUDIO_CHANNELS)
                wf.setsampwidth(2)   # 16-bit
                wf.setframerate(self.AUDIO_SAMPLE_RATE)
                wf.writeframes(pcm_int16.tobytes())
            print(f"  WAV 저장 완료: {wav_path} ({len(pcm_int16)} 샘플)")
        except Exception as exc:
            print(f"⚠️  WAV 저장 실패: {exc}")

    def _merge_audio_video(self, video_path: str, wav_path: str) -> str:
        """
        ffmpeg으로 MP4(영상) + WAV(오디오)를 병합합니다.
        ffmpeg이 없거나 오디오 파일이 없으면 영상만 반환합니다.

        :return: 최종 파일 경로 (병합 성공 시 video_path 그대로 덮어씀)
        """
        # 오디오 없이 녹화된 경우 또는 오디오 파일이 없는 경우 → 영상만 반환
        if not _AUDIO_AVAILABLE or not os.path.exists(wav_path):
            return video_path

        self.last_audio_path = wav_path

        # ffmpeg 사용 가능 여부 확인
        try:
            subprocess.run(
                [_FFMPEG_EXE, "-version"],
                capture_output=True,
                check=True,
                timeout=5,
            )
        except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            print("⚠️  ffmpeg를 찾을 수 없습니다. MP4 병합 없이 WAV 오디오를 별도 보관합니다.")
            print("    설치: https://ffmpeg.org/download.html")
            return video_path

        # 임시 병합 파일 경로
        base, ext = os.path.splitext(video_path)
        merged_path = f"{base}_merged{ext}"

        try:
            cmd = [
                _FFMPEG_EXE, "-y",
                "-i", video_path,         # 영상 입력
                "-i", wav_path,            # 오디오 입력
                "-c:v", "copy",            # 영상 재인코딩 없이 복사
                "-c:a", "aac",             # 오디오 AAC 인코딩
                "-b:a", "128k",
                "-shortest",               # 짧은 스트림 기준으로 맞춤
                merged_path,
            ]
            print(f"🔗 영상+오디오 병합 중... ({os.path.basename(video_path)})")
            result = subprocess.run(cmd, capture_output=True, timeout=120)

            if result.returncode == 0:
                # 병합 성공 → 원본 파일을 병합 파일로 교체
                os.replace(merged_path, video_path)
                self._cleanup_temp_wav(wav_path)
                self.last_audio_path = ""
                print(f"✅ 병합 완료: {video_path}")
            else:
                err = result.stderr.decode(errors="replace")
                print(f"❌ ffmpeg 병합 실패 (code {result.returncode}):\n{err[:500]}")
                if os.path.exists(merged_path):
                    os.remove(merged_path)
                print(f"    WAV 오디오는 보관합니다: {wav_path}")

        except subprocess.TimeoutExpired:
            print(f"⚠️  ffmpeg 병합 타임아웃 (120초). WAV 오디오는 보관합니다: {wav_path}")
        except Exception as exc:
            print(f"⚠️  병합 중 예외: {exc}")
            print(f"    WAV 오디오는 보관합니다: {wav_path}")

        return video_path

    @staticmethod
    def _cleanup_temp_wav(wav_path: str):
        """임시 WAV 파일을 삭제합니다."""
        try:
            if os.path.exists(wav_path):
                os.remove(wav_path)
        except OSError:
            pass
