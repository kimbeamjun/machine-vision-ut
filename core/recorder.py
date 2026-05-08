import cv2
import numpy as np
import time
from mss import mss
from PySide6.QtCore import QObject, Signal


class ScreenRecorder(QObject):
    recording_finished = Signal(str)

    def __init__(self):
        super().__init__()
        self.is_recording = False
        self.writer = None
        self.start_time = 0.0

    def start(self, pixel_region: dict, output_path: str, fps: int = 20):
        """
        녹화를 시작합니다.
        :param pixel_region: 물리 픽셀 좌표 dict {"left", "top", "width", "height"}
        """
        self.is_recording = True
        self.start_time = time.time()

        fourcc = cv2.VideoWriter.fourcc(*'mp4v')

        with mss() as sct:
            # 외부에서 보정된 물리 픽셀을 직접 사용
            # mss는 시스템 전체 좌표(Virtual Screen)를 기준으로 grab합니다.
            monitor = {
                "top":    pixel_region.get('top', 0),
                "left":   pixel_region.get('left', 0),
                "width":  pixel_region.get('width', 100),
                "height": pixel_region.get('height', 100),
            }

            # 실제 픽셀 크기로 영상 저장 객체 생성
            self.writer = cv2.VideoWriter(
                output_path, 
                fourcc, 
                fps, 
                (monitor["width"], monitor["height"])
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
        self.recording_finished.emit(output_path)

    def stop(self):
        self.is_recording = False

    def get_elapsed_time(self) -> float:
        """녹화 시작 후 현재까지 흐른 절대 시각(초)을 반환합니다."""
        if self.start_time == 0:
            return 0.0
        return time.time() - self.start_time