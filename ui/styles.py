APP_QSS = """
* {
    font-family: Consolas, "Malgun Gothic", monospace;
    font-size: 12px;
}

QMainWindow, QWidget {
    background: #12161e;
    color: #c8d4ea;
}

QFrame#AppFrame {
    background: #12161e;
    border: 1px solid #2a3040;
    border-radius: 8px;
}

QFrame#TitleBar, QFrame#BottomBar {
    background: #1a1f2c;
    border: 0;
}

QFrame#Sidebar, QFrame#TaskPanel {
    background: #161c28;
    border: 0;
}

QLabel#Muted {
    color: #6a7a9a;
}

QLabel#SectionLabel {
    color: #4a5878;
    font-size: 10px;
    letter-spacing: 1px;
}

QLineEdit {
    background: #1f2636;
    border: 1px solid #2d3748;
    border-radius: 5px;
    padding: 7px 10px;
    color: #c8d4ea;
}

QLineEdit:focus {
    border-color: #3a5fa8;
}

QPushButton {
    background: #1f2636;
    border: 1px solid #2d3748;
    border-radius: 5px;
    padding: 7px 12px;
    color: #8a9ab5;
}

QPushButton:hover {
    background: #242b38;
}

QPushButton#PrimaryButton {
    background: #1d4ed8;
    border-color: #1d4ed8;
    color: #dbeafe;
}

QPushButton#SuccessButton {
    background: #14532d;
    border-color: #14532d;
    color: #86efac;
}

QPushButton#DangerButton {
    background: #7f1d1d;
    border-color: #7f1d1d;
    color: #fca5a5;
}

QProgressBar {
    background: #1f2636;
    border: 0;
    border-radius: 2px;
    height: 5px;
    text-align: center;
}

QProgressBar::chunk {
    background: #378add;
    border-radius: 2px;
}
"""
