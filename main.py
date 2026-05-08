import sys
import os

# 프로젝트 루트(최상위) 경로를 sys.path에 추가
# main.py가 프로젝트 루트에 있다고 가정할 때
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
if CURRENT_DIR not in sys.path:
    sys.path.insert(0, CURRENT_DIR)

from PySide6.QtWidgets import QApplication
# ui 폴더 안의 main_window 파일에서 MainWindow 클래스를 가져옴
from ui.main_window import MainWindow

def main():
    app = QApplication(sys.argv)
    app.setApplicationName("UT Automation Client")
    
    try:
        window = MainWindow() # 여기서 오류가 난다면 MainWindow의 __init__(self) 확인!
        window.show()
        return app.exec()
    except Exception as e:
        print(f"프로그램 실행 중 오류 발생: {e}")
        return 1

if __name__ == "__main__":
    sys.exit(main())