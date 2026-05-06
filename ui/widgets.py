import os

from PySide6.QtCore import QPointF, QRectF, Qt, Signal, QTimer
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget, QLabel, QFrame, QVBoxLayout

# UI 보조 함수 및 위젯 정의

def muted(text: str) -> QLabel:
    """회색조의 보조 텍스트 레이블 생성"""
    label = QLabel(text)
    label.setStyleSheet("color: #8b949e; font-size: 12px;")
    return label

def section_label(text: str) -> QLabel:
    """섹션 구분을 위한 강조 레이블 생성"""
    label = QLabel(text)
    label.setStyleSheet("font-weight: bold; color: #58a6ff; margin-top: 10px; margin-bottom: 4px;")
    return label

def panel(title: str, content_widget: QWidget) -> QFrame:
    """제목이 있는 테두리 박스 패널 생성"""
    frame = QFrame()
    frame.setObjectName("PanelFrame")
    frame.setStyleSheet("#PanelFrame { border: 1px solid #30363d; border-radius: 6px; background: #0d1117; }")
    layout = QVBoxLayout(frame)
    
    title_label = QLabel(title)
    title_label.setStyleSheet("font-weight: bold; color: #c9d1d9; margin-bottom: 4px;")
    layout.addWidget(title_label)
    layout.addWidget(content_widget)
    return frame

class RegionPreview(QFrame):
    """현재 선택된 영역을 시각적으로 미리 보여주는 위젯"""
    def __init__(self, region_state, parent=None):
        super().__init__(parent)
        self.region = region_state
        self.setFixedSize(220, 140)
        self.setStyleSheet("background: #161b22; border: 1px solid #30363d;")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        # 비율 좌표를 픽셀로 변환
        rect = QRectF(self.region.x * w, self.region.y * h, self.region.w * w, self.region.h * h)
        painter.setPen(QPen(QColor("#58a6ff"), 2))
        painter.setBrush(QColor(88, 166, 255, 30))
        painter.drawRect(rect)

class CalibrationCanvas(QWidget):
    """5점 캘리브레이션 진행 상태를 시각화하는 캔버스[cite: 4]"""
    point_captured = Signal(int, float, float)
    calibration_finished = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.active_index = 0
        self.progress_val = 0.0
        self.points = [
            QPointF(0.1, 0.1), QPointF(0.9, 0.1),
            QPointF(0.5, 0.5),
            QPointF(0.1, 0.9), QPointF(0.9, 0.9)
        ]
        self.setMinimumHeight(340)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._update_progress)
        self.is_running = False

    def start_calibration(self):
        self.active_index = 0
        self.progress_val = 0
        self.is_running = True
        self.timer.start(50)
        self.update()

    def _update_progress(self):
        if not self.is_running: return
        self.progress_val += 2.0
        if self.progress_val >= 100:
            p = self.points[self.active_index]
            self.point_captured.emit(self.active_index + 1, p.x(), p.y())
            self.active_index += 1
            self.progress_val = 0
            if self.active_index >= len(self.points):
                self.is_running = False
                self.timer.stop()
                self.calibration_finished.emit()
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#0d1117"))

        for i, pt in enumerate(self.points):
            x, y = pt.x() * self.width(), pt.y() * self.height()
            if i < self.active_index:
                color = QColor("#639922") # 완료된 포인트
            elif i == self.active_index:
                color = QColor("#ef9f27") # 현재 진행 포인트
                painter.setPen(QPen(Qt.GlobalColor.white, 1))
                painter.drawArc(QRectF(x-20, y-20, 40, 40), 90*16, int(-self.progress_val*3.6*16))
            else:
                color = QColor("#2d3748") # 대기 포인트

            painter.setPen(QPen(color, 2))
            painter.drawEllipse(QPointF(x, y), 15, 15)
            painter.drawText(QRectF(x-10, y-10, 20, 20), Qt.AlignmentFlag.AlignCenter, str(i+1))