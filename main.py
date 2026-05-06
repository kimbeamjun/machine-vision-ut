import sys
import os

# 프로젝트 루트 경로
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
if CURRENT_DIR not in sys.path:
    sys.path.insert(0, CURRENT_DIR)

from PySide6.QtWidgets import QApplication
from ui.main_window import MainWindow

def main() -> int:
    """프로그램의 메인 진입점"""
    app = QApplication(sys.argv)
    app.setApplicationName("UT Automation Client")
    
    # 메인 윈도우 생성 및 표시
    window = MainWindow()
    window.show()
    
    return app.exec()

if __name__ == "__main__":
    # 메인 함수 실행 및 종료 코드 전달
    try:
        sys.exit(main())
    except SystemExit:
        pass