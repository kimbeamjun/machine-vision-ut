from PySide6.QtWidgets import QWidget, QApplication
from PySide6.QtCore import Qt, QRect, Signal, QPoint
from PySide6.QtGui import QPainter, QColor, QPen

class RegionSelector(QWidget):
    """
    CL-REQ-11~13: 화면 전체에 투명 오버레이를 띄워 녹화 영역을 드래그로 선택함
    """
    region_selected = Signal(QRect) # 선택 완료 시 픽셀 좌표(QRect)를 전달함

    def __init__(self) -> None:
        super().__init__()
        # 최상단 고정, 프레임 제거, 투명 배경 설정
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | 
                            Qt.WindowType.WindowStaysOnTopHint | 
                            Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setCursor(Qt.CursorShape.CrossCursor)
        
        # 전체 화면 크기로 설정
        self.setGeometry(QApplication.primaryScreen().geometry())
        
        self.begin = QPoint()
        self.end = QPoint()
        self.is_selecting = False

    def paintEvent(self, event) -> None:
        """
        CL-REQ-13: 사용자가 드래그 중인 영역을 반투명 박스로 시각화함
        """
        painter = QPainter(self)
        # 배경을 살짝 어둡게 (선택 영역만 밝게 보임)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 100))
        
        if self.is_selecting:
            rect = QRect(self.begin, self.end).normalized()
            # 선택 영역은 투명하게 뚫어줌
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
            painter.fillRect(rect, Qt.GlobalColor.transparent)
            
            # 테두리 그리기
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            pen = QPen(QColor("#7eb8f7"), 2, Qt.PenStyle.SolidLine)
            painter.setPen(pen)
            painter.drawRect(rect)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.begin = event.pos()
            self.end = self.begin
            self.is_selecting = True
            self.update()

    def mouseMoveEvent(self, event) -> None:
        if self.is_selecting:
            self.end = event.pos()
            self.update()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.is_selecting:
            self.is_selecting = False
            rect = QRect(self.begin, self.end).normalized()
            
            # 너무 작은 영역은 무시하거나 처리 (요구사항 3.2: w, h > 0)
            if rect.width() > 5 and rect.height() > 5:
                self.region_selected.emit(rect)
            
            self.close()

    def keyPressEvent(self, event) -> None:
        # ESC 키를 누르면 취소하고 닫음
        if event.key() == Qt.Key.Key_Escape:
            self.close()