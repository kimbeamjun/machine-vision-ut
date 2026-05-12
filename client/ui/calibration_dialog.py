import os
import cv2
import time
from PySide6.QtWidgets import QDialog, QLabel
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QPainter, QColor

class CalibrationDialog(QDialog):
    """
    5개 포인트 촬영 및 데이터 수집 담당.
    모든 데이터 키값을 'screen_x', 'screen_y'로 통일하여 KeyError를 방지합니다.
    """
    calibration_finished = Signal(list)

    FIXATION_DELAY_MS = 1500
    RECORD_DURATION_MS = 2500
    FRAME_INTERVAL_MS = 50
    VIDEO_SIZE = (640, 480)

    # 기본 5점 레이아웃
    _DEFAULT_POINTS = [
        {"point_no": 1, "screen_x": 0.1, "screen_y": 0.1},
        {"point_no": 2, "screen_x": 0.9, "screen_y": 0.1},
        {"point_no": 3, "screen_x": 0.5, "screen_y": 0.5},
        {"point_no": 4, "screen_x": 0.1, "screen_y": 0.9},
        {"point_no": 5, "screen_x": 0.9, "screen_y": 0.9},
    ]

    def __init__(self, parent=None, viewport_region=None, points_to_capture=None):
        super().__init__(parent)
        
        # 1. 변수 초기화
        self.current_index = 0
        self.captured_data = []
        self.viewport_region = viewport_region
        
        # 입력받은 포인트가 없으면 기본 포인트 사용 (필드명 'screen_x' 보장)
        self.points = points_to_capture if points_to_capture else list(self._DEFAULT_POINTS)

        # 2. UI 객체(Label) 생성
        self.label = QLabel("화면에 나타나는 빨간 점을 계속 응시해 주세요.", self)
        self.label.setStyleSheet("color: white; font-size: 24px; background: transparent;")
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        # 3. 윈도우 스타일 설정
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setStyleSheet("background-color: black;")

        # 4. 카메라 및 녹화 설정
        self.cap = self._open_camera()
        self.fourcc = cv2.VideoWriter.fourcc(*'mp4v')
        self.out = None
        self._finished_emitted = False
        self.is_recording_point = False
        self.current_save_path = ""

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.record_frame)

        # 5. 화면 표시
        self.showFullScreen()

    def showEvent(self, event):
        super().showEvent(event)
        self.label.setGeometry(0, self.height() // 2 - 50, self.width(), 100)
        # 2초 뒤 보정 시작
        QTimer.singleShot(2000, self.start_calibration)

    def start_calibration(self):
        self.label.hide()
        self.next_point()

    def next_point(self):
        """다음 포인트를 먼저 표시하고, 사용자가 응시할 시간을 확보한 뒤 녹화를 시작"""
        if self.current_index < len(self.points):
            point = self.points[self.current_index]
            
            # 파일명 규칙: calib_pt_1.mp4 형식
            file_name = f"calib_pt_{point['point_no']}.mp4"
            self.current_save_path = os.path.abspath(file_name)

            # 수집 데이터 리스트에 추가 (KeyError 방지를 위해 screen_x 필드명 고정)
            self.captured_data.append({
                "point_no": point['point_no'],
                "screen_x": point['screen_x'], 
                "screen_y": point['screen_y'], 
                "path": self.current_save_path,
            })

            # 포인트를 먼저 화면에 보여주고, 1.5초간 응시 시간을 확보한 뒤 녹화 시작
            self.is_recording_point = False
            self.update()
            QTimer.singleShot(self.FIXATION_DELAY_MS, self.start_point_recording)
        else:
            self.finish()

    def start_point_recording(self):
        """현재 포인트를 충분히 응시한 뒤 웹캠 녹화를 시작"""
        if self._finished_emitted or self.current_index >= len(self.points):
            return

        if not self.cap or not self.cap.isOpened():
            self.cap = self._open_camera()
        if not self.cap.isOpened():
            self.finish()
            return

        self.out = cv2.VideoWriter(self.current_save_path, self.fourcc, 20.0, self.VIDEO_SIZE)
        if not self.out.isOpened():
            self.finish()
            return

        self.is_recording_point = True
        self.timer.start(self.FRAME_INTERVAL_MS)

        # 안정적으로 응시한 구간만 녹화하고 다음 포인트로 이동
        QTimer.singleShot(self.RECORD_DURATION_MS, self.stop_point_recording)

    def record_frame(self):
        """카메라 프레임을 비디오 파일로 저장"""
        if self.is_recording_point and self.cap.isOpened() and self.out:
            ret, frame = self.cap.read()
            if ret:
                frame = cv2.resize(frame, self.VIDEO_SIZE)
                self.out.write(frame)

    def stop_point_recording(self):
        """현재 포인트 녹화 종료 및 다음 인덱스로 이동"""
        self.timer.stop()
        self.is_recording_point = False
        if self.out:
            self.out.release()
            self.out = None
        
        # 인덱스를 올리고 다음 포인트 호출 (재귀적 반복)
        self.current_index += 1
        self.next_point()

    def paintEvent(self, event):
        """화면에 빨간색 캘리브레이션 포인트 그리기"""
        if hasattr(self, 'current_index') and self.current_index < len(self.points):
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            
            target = self.points[self.current_index]
            
            # 'x' 대신 'screen_x'를 참조하여 에러 방지
            px = target['screen_x'] * self.width()
            py = target['screen_y'] * self.height()
            
            # 시선 유도용 원 그리기
            painter.setBrush(QColor(255, 0, 0)) # 빨간색
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(int(px - 20), int(py - 20), 40, 40)
            
            painter.setBrush(QColor(255, 255, 255)) # 중앙 흰색 점
            painter.drawEllipse(int(px - 5), int(py - 5), 10, 10)

    def finish(self):
        """모든 5개 포인트 수집 완료 후 Signal 발송"""
        if self._finished_emitted:
            return

        self._finished_emitted = True
        self._release_camera_resources()
            
        # 메인 윈도우의 업로드 로직으로 데이터 전달
        self.calibration_finished.emit(self.captured_data)
        self.close()

    def closeEvent(self, event):
        """창이 닫힐 때 웹캠/파일 핸들을 반드시 해제해 본 테스트 카메라와 충돌하지 않게 한다."""
        self._release_camera_resources()
        super().closeEvent(event)

    def _release_camera_resources(self):
        """캘리브레이션 녹화에 사용한 OpenCV 리소스를 안전하게 정리한다."""
        self.is_recording_point = False
        if self.timer.isActive():
            self.timer.stop()
        if self.out:
            self.out.release()
            self.out = None
        if self.cap and self.cap.isOpened():
            self.cap.release()
        cv2.destroyAllWindows()

    @staticmethod
    def _open_camera():
        """캘리브레이션 영상은 화면 캡처가 아니라 사용자 웹캠 영상을 녹화한다."""
        backends = []
        if hasattr(cv2, "CAP_DSHOW"):
            backends.append(cv2.CAP_DSHOW)
        if hasattr(cv2, "CAP_MSMF"):
            backends.append(cv2.CAP_MSMF)
        backends.append(cv2.CAP_ANY)

        for backend in backends:
            cap = cv2.VideoCapture(0) if backend == cv2.CAP_ANY else cv2.VideoCapture(0, backend)
            if cap.isOpened():
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, CalibrationDialog.VIDEO_SIZE[0])
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CalibrationDialog.VIDEO_SIZE[1])
                cap.set(cv2.CAP_PROP_FPS, 20)
                return cap
            cap.release()

        return cv2.VideoCapture(-1)
