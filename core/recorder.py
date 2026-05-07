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

    def start(self, region: dict, output_path: str, fps: int = 20):
        """
        녹화를 시작합니다.
        :param region: 비율 좌표 dict {"x", "y", "w", "h"} (0.0 ~ 1.0)
        """
        self.is_recording = True
        self.start_time = time.time()

        fourcc = cv2.VideoWriter.fourcc(*'mp4v')

        with mss() as sct:
            # 비율 좌표 → 실제 픽셀 좌표 변환
            # monitors[1]: 주 모니터 (monitors[0]은 모든 모니터의 합산 영역)
            primary = sct.monitors[1]
            sw = primary['width']
            sh = primary['height']

            pixel_x = int(region.get('x', 0.0) * sw)
            pixel_y = int(region.get('y', 0.0) * sh)
            pixel_w = int(region.get('w', 1.0) * sw)
            pixel_h = int(region.get('h', 1.0) * sh)

            # mss 캡처 영역
            monitor = {
                "top":    pixel_y,
                "left":   pixel_x,
                "width":  pixel_w,
                "height": pixel_h,
            }

            # 영상 저장 객체 생성 (실제 픽셀 크기로 초기화)
            self.writer = cv2.VideoWriter(output_path, fourcc, fps, (pixel_w, pixel_h))

            while self.is_recording:
                # [FIX] 프레임 시작 시각을 기록해 캡처/인코딩 소요 시간을 제거하고 대기
                frame_start = time.perf_counter()
                img = sct.grab(monitor)
                frame = np.array(img)
                # BGRA → BGR 변환 (OpenCV 기본 포맷)
                frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
                self.writer.write(frame)

                # 처리에 걸린 시간만큼 sleep을 줄여 실제 FPS를 목표에 근접시킴
                elapsed = time.perf_counter() - frame_start
                sleep_time = (1.0 / fps) - elapsed
                if sleep_time > 0:
                    time.sleep(sleep_time)

        # 자원 해제
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