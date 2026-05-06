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
        """ 녹화를 시작합니다. """
        self.is_recording = True
        self.start_time = time.time() # 절대 타임스탬프 기준점 설정
        
        # VideoWriter_fourcc 대신 VideoWriter.fourcc 또는 직접 인자 전달 방식 사용
        # mp4v 코덱 설정
        fourcc = cv2.VideoWriter.fourcc(*'mp4v') 
        
        # region에서 실제 정수형 크기 추출
        width = int(region.get('w', 1920))
        height = int(region.get('h', 1080))
        
        # 영상 저장 객체 생성
        self.writer = cv2.VideoWriter(output_path, fourcc, fps, (width, height))

        with mss() as sct:
            # mss 모니터 설정
            monitor = {
                "top": int(region.get('y', 0)), 
                "left": int(region.get('x', 0)), 
                "width": width, 
                "height": height
            }
            
            while self.is_recording:
                # 화면 캡처 및 변환
                img = sct.grab(monitor)
                frame = np.array(img)
                # BGRA -> BGR 변환 (OpenCV 기본 포맷)
                frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)

                self.writer.write(frame)
                
                # FPS 유지를 위한 대기 시간 계산
                time.sleep(1 / fps)

        # 자원 해제
        if self.writer:
            self.writer.release()
        self.recording_finished.emit(output_path)

    def stop(self):
        self.is_recording = False

    def get_elapsed_time(self):
        """ 녹화 시작 후 현재까지 흐른 절대 시각(초)을 반환합니다. """
        if self.start_time == 0:
            return 0.0
        return time.time() - self.start_time