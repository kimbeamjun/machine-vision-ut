import cv2
import numpy as np
import os
import subprocess
import threading
import time
import wave
import importlib
import queue
from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QImage

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
    preview_frame = Signal(QImage)

    # 오디오 설정 상수
    AUDIO_SAMPLE_RATE = 44100
    AUDIO_CHANNELS = 1         # 모노 (STT 분석에 충분)
    AUDIO_DTYPE = "int16"

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
        본 테스트용 사용자 캠 녹화(+오디오 녹음)를 시작합니다.

        :param pixel_region: 기존 RecordingWorker 인터페이스 호환용. 본 테스트 녹화에서는 사용하지 않습니다.
        :param output_path:  최종 출력 파일 경로 (.mp4)
        :param fps:          녹화 프레임 레이트 (기본 20)

        ※ 이 메서드는 블로킹 호출입니다. RecordingWorker 스레드에서 실행하세요.
        """
        self.is_recording = True
        self.start_time = time.time()
        self.end_time = 0.0
        self._audio_frames = []
        self.last_audio_path = ""

        # ── 1. 사용자 캠 열기 ───────────────────────────
        fourcc = cv2.VideoWriter.fourcc(*"mp4v")
        frame_width = 640
        frame_height = 480

        cap = self._open_camera()

        if not cap.isOpened():
            self.is_recording = False
            raise RuntimeError("웹캠을 열 수 없습니다. 다른 프로그램이 카메라를 사용 중인지 확인하세요.")

        cap.set(cv2.CAP_PROP_FRAME_WIDTH, frame_width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, frame_height)
        cap.set(cv2.CAP_PROP_FPS, fps)

        self.writer = cv2.VideoWriter(
            output_path,
            fourcc,
            fps,
            (frame_width, frame_height),
        )
        if not self.writer.isOpened():
            cap.release()
            self.is_recording = False
            raise RuntimeError("녹화 파일을 생성하지 못했습니다. 저장 경로 또는 코덱 상태를 확인하세요.")

        # ── 2. 오디오 녹음 스레드 시작 ──────────────────
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

        # ── 3. 사용자 캠 녹화 루프 (블로킹) ───────────────
        try:
            while self.is_recording:
                frame_start = time.perf_counter()
                ret, frame = cap.read()
                if not ret:
                    time.sleep(0.05)
                    continue

                frame = cv2.resize(frame, (frame_width, frame_height))
                self.writer.write(frame)

                preview = cv2.resize(frame, (260, 180))
                rgb = cv2.cvtColor(preview, cv2.COLOR_BGR2RGB)
                bytes_per_line = rgb.shape[1] * rgb.shape[2]
                image = QImage(
                    rgb.data,
                    rgb.shape[1],
                    rgb.shape[0],
                    bytes_per_line,
                    QImage.Format.Format_RGB888,
                ).copy()
                self.preview_frame.emit(image)

                elapsed = time.perf_counter() - frame_start
                sleep_time = (1.0 / fps) - elapsed
                if sleep_time > 0:
                    time.sleep(sleep_time)
        finally:
            cap.release()

        if self.writer:
            self.writer.release()
            self.writer = None
        print(f"📹 사용자 캠 녹화 종료: {output_path}")

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
    def _open_camera():
        """캘리브레이션 직후 카메라 드라이버가 늦게 풀리는 경우를 고려해 순차 재시도한다."""
        time.sleep(0.8)
        backends = [cv2.CAP_ANY]
        if hasattr(cv2, "CAP_MSMF"):
            backends.append(cv2.CAP_MSMF)
        if hasattr(cv2, "CAP_DSHOW"):
            backends.append(cv2.CAP_DSHOW)

        for backend in backends:
            cap = cv2.VideoCapture(0) if backend == cv2.CAP_ANY else cv2.VideoCapture(0, backend)
            if cap.isOpened():
                return cap
            cap.release()
            time.sleep(0.3)

        return cv2.VideoCapture(-1)

    def _record_audio(self, wav_path: str):
        """
        sounddevice InputStream 콜백으로 받은 오디오를 즉시 WAV 파일에 스트리밍 저장합니다.
        긴 녹음 데이터를 메모리에 쌓지 않아 사용자 캠 프레임 루프와의 간섭을 줄입니다.
        """
        audio_queue: queue.Queue[bytes] = queue.Queue(maxsize=64)
        dropped_blocks = 0

        def _callback(indata: np.ndarray, frame_count: int, time_info, status):
            nonlocal dropped_blocks
            if not self.is_recording:
                return
            try:
                audio_queue.put_nowait(indata.copy().tobytes())
            except queue.Full:
                dropped_blocks += 1

        try:
            device = self._select_audio_device()
            with wave.open(wav_path, "wb") as wf:
                wf.setnchannels(self.AUDIO_CHANNELS)
                wf.setsampwidth(2)
                wf.setframerate(self.AUDIO_SAMPLE_RATE)

                with sd.InputStream(
                    samplerate=self.AUDIO_SAMPLE_RATE,
                    channels=self.AUDIO_CHANNELS,
                    dtype=self.AUDIO_DTYPE,
                    blocksize=4096,
                    latency="high",
                    device=device,
                    callback=_callback,
                ):
                    while self.is_recording or not audio_queue.empty():
                        try:
                            wf.writeframes(audio_queue.get(timeout=0.1))
                        except queue.Empty:
                            continue
        except Exception as exc:
            print(f"⚠️  오디오 녹음 중 오류: {exc}")
            return

        if dropped_blocks:
            print(f"⚠️  오디오 버퍼 초과로 {dropped_blocks}개 블록을 건너뛰었습니다.")
        print(f"  WAV 저장 완료: {wav_path}")

    @staticmethod
    def _select_audio_device():
        """
        가능하면 웹캠 내장 마이크가 아닌 입력 장치를 사용합니다.
        웹캠 영상 캡처와 같은 장치의 마이크를 동시에 열면 일부 Windows 드라이버에서 프리뷰가 멈출 수 있습니다.
        """
        try:
            devices = sd.query_devices()
            default_input = sd.default.device[0] if isinstance(sd.default.device, (list, tuple)) else sd.default.device
            blocked_words = ("camera", "webcam", "cam", "usb video", "integrated camera", "캠", "카메라")

            def is_input_device(index: int, device_info: dict) -> bool:
                return int(device_info.get("max_input_channels", 0)) > 0

            def looks_like_camera_mic(device_info: dict) -> bool:
                name = str(device_info.get("name", "")).lower()
                return any(word in name for word in blocked_words)

            if isinstance(default_input, int) and 0 <= default_input < len(devices):
                default_device = devices[default_input]
                if is_input_device(default_input, default_device) and not looks_like_camera_mic(default_device):
                    return default_input

            for index, device_info in enumerate(devices):
                if is_input_device(index, device_info) and not looks_like_camera_mic(device_info):
                    print(f"🎙️  오디오 입력 장치 선택: {device_info.get('name')}")
                    return index
        except Exception as exc:
            print(f"⚠️  오디오 장치 선택 중 오류: {exc}")

        return None

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
