import os
import cv2
import time
from PySide6.QtWidgets import QDialog, QLabel
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QPainter, QColor

class CalibrationDialog(QDialog):
    """5개 포인트 촬영 및 로컬 저장 담당"""
    calibration_finished = Signal(list)

    def __init__(self, parent=None, viewport_region=None):
        super().__init__(parent)
        self.viewport_region = viewport_region
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.showFullScreen()

        # 1. 5개 포인트 정의
        self.points = [
            {"point_no": 1, "x": 0.1, "y": 0.1},
            {"point_no": 2, "x": 0.9, "y": 0.1},
            {"point_no": 3, "x": 0.5, "y": 0.5},
            {"point_no": 4, "x": 0.1, "y": 0.9},
            {"point_no": 5, "x": 0.9, "y": 0.9},
        ]

        self.current_index = 0
        self.captured_data = []

        # 2. 카메라 초기화
        self.cap = cv2.VideoCapture(0)

        fourcc_func = getattr(cv2, 'VideoWriter_fourcc', None)
        if fourcc_func:
            self.fourcc = fourcc_func(*'mp4v')
        else:
            self.fourcc = cv2.VideoWriter.fourcc(*'mp4v')

        self.out = None

        # 3. 타이머 설정
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.record_frame)

        self.label = QLabel("화면에 나타나는 빨간 점을 계속 응시해 주세요.", self)
        self.label.setStyleSheet("color: white; font-size: 24px; background: transparent;")
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet("background-color: black;")

    def showEvent(self, event):
        super().showEvent(event)
        self.label.setGeometry(0, self.height() // 2 - 50, self.width(), 100)
        QTimer.singleShot(2000, self.start_calibration)

    def start_calibration(self):
        self.label.hide()
        self.next_point()

    def next_point(self):
        if self.current_index < len(self.points):
            point = self.points[self.current_index]
            file_name = f"calib_pt_{point['point_no']}.mp4"
            save_path = os.path.abspath(file_name)

            # 녹화 시작 (640x480, 20fps)
            self.out = cv2.VideoWriter(save_path, self.fourcc, 20.0, (640, 480))

            # [수정] CalibrationWorker가 기대하는 키 이름으로 통일: x / y (screen_x/screen_y 제거)
            self.captured_data.append({
                "point_no": point['point_no'],
                "x": point['x'],   # 비율 좌표 — CalibrationWorker에서 screen_x로 변환
                "y": point['y'],   # 비율 좌표 — CalibrationWorker에서 screen_y로 변환
                "path": save_path,
            })

            self.update()
            self.timer.start(50)
            QTimer.singleShot(3000, self.stop_point_recording)
        else:
            self.finish()

    def record_frame(self):
        if self.cap.isOpened() and self.out:
            ret, frame = self.cap.read()
            if ret:
                frame = cv2.resize(frame, (640, 480))
                self.out.write(frame)

    def stop_point_recording(self):
        self.timer.stop()
        if self.out:
            self.out.release()
            self.out = None
        self.current_index += 1
        self.next_point()

    def paintEvent(self, event):
        if self.current_index < len(self.points):
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            target = self.points[self.current_index]
            px = target['x'] * self.width()
            py = target['y'] * self.height()
            painter.setBrush(QColor(255, 0, 0))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(int(px - 15), int(py - 15), 30, 30)
            painter.setBrush(QColor(255, 255, 255))
            painter.drawEllipse(int(px - 3), int(py - 3), 6, 6)

    def finish(self):
        if self.cap.isOpened():
            self.cap.release()
        self.calibration_finished.emit(self.captured_data)
        self.close()
        
