import sys
import os
import time
from typing import Any, Dict, List, cast, Optional

# 폴더 구조 확인 및 루트 경로
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.abspath(os.path.join(CURRENT_DIR, ".."))

CONFIG_DIR = os.path.join(ROOT_DIR, "config")
TASKS_CONFIG_PATH = os.path.join(CONFIG_DIR, "tasks_config.json")

if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from PySide6.QtMultimedia import QCamera, QMediaCaptureSession, QImageCapture
from PySide6.QtMultimediaWidgets import QVideoWidget    
from PySide6.QtGui import QGuiApplication
from PySide6.QtCore import Qt, QThreadPool, Slot, QTimer, QUrl, QByteArray, QBuffer, QIODevice, QPoint
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QMainWindow, QPushButton, QProgressBar, QSizePolicy,
    QStackedWidget, QVBoxLayout, QWidget, QApplication,
    QMessageBox, QGroupBox
)


try:
    from PySide6.QtWebEngineWidgets import QWebEngineView
except ImportError:
    QWebEngineView = None

try:
    from models.models import ClientState, PageLog
    from core.api_client import ApiClient, ApiConfig
except (ImportError, ModuleNotFoundError):
    sys.path.append(ROOT_DIR)
    from models.models import ClientState, PageLog
    from core.api_client import ApiClient, ApiConfig

from .styles import APP_QSS
from .widgets import CalibrationCanvas, RegionPreview, muted, panel, section_label
from .overlay import RegionSelector
from core.recorder import ScreenRecorder
from ui.calibration_dialog import CalibrationDialog
from utils.workers import (
    RecordingWorker, UploadWorker, CalibrationWorker,
    CalibrationStatusWorker, AnalysisStatusWorker, ScreenshotUploadWorker
)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()

        # 1. 레코더 객체 생성
        self.recorder = ScreenRecorder()

        # 2. 녹화 영역 및 세션 변수 초기화
        self.viewport = {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}
        self.session_id: Optional[str] = None
        self.page_logs: List[Dict[str, Any]] = []
        self.recording_thread = None
        self.upload_worker = None
        self.analysis_status_worker = None
        self.is_uploading = False
        self.video_output_path = os.path.abspath("test_video.mp4")
        self.test_running = False
        self.is_recording = False
        
        self.tasks = []               # 서버에서 받을 태스크 목록
        self.current_task_index = 0   # 현재 몇 번째 태스크인지 (0부터 시작)
        self.task_results = []        # 결과 저장 리스트
        self.current_task_info = None # 현재 진행 중인 태스크 임시 저장

        # 3. 설정 및 스레드 풀 객체 생성
        self.state = ClientState()
        self.thread_pool = QThreadPool.globalInstance()

        # 4. API 클라이언트 초기화
        config = ApiConfig(base_url="http://10.10.10.113:8000") # 명세서 MS-1 기준
        self.api = ApiClient(config)

        # 5. UI 전체 설정
        self.setWindowTitle("UT Automation Client")
        self.resize(1160, 720)
        self.setStyleSheet(APP_QSS)
        
        self.upload_status_label = QLabel("데이터 업로드 준비 중...", self)
        self.upload_status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        

        # 6. 화면 전환 레이아웃
        self.stack = QStackedWidget()
        self.nav_buttons: List[QPushButton] = []

        # 7. 메인 레이아웃 구성
        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(14, 14, 14, 14)
        root_layout.setSpacing(10)
        root_layout.addLayout(self._build_nav())
        root_layout.addWidget(self.stack)
        self.setCentralWidget(root)

        # 8. 5개 화면 등록 (Index 0~4)
        self.stack.addWidget(self._build_region_screen())       # Index 0 (영역 설정)
        self.stack.addWidget(self._build_calibration_screen())  # Index 1 (캘리브레이션)
        self.stack.addWidget(self._build_test_screen())         # Index 2 (본 테스트)
        self.stack.addWidget(self._build_upload_screen())       # Index 3 (업로드/분석 대기)
        self.stack.addWidget(self._build_report_screen())       # Index 4 (결과 리포트)
        
        # 9. 초기 화면 표시
        self._show_screen(0)

    # ──────────────────────────────────────────────
    # 네비게이션
    # ──────────────────────────────────────────────

    def _build_nav(self) -> QHBoxLayout:
        """상단 단계별 이동 버튼 바 생성"""
        layout = QHBoxLayout()
        layout.setSpacing(6)
        labels = ["녹화 범위 설정", "5점 캘리브레이션", "테스트 진행", "업로드/분석 대기", "보고서 완료"]
        for index, label in enumerate(labels):
            button = QPushButton(label)
            button.clicked.connect(lambda checked=False, i=index: self._show_screen(i))
            self.nav_buttons.append(button)
            layout.addWidget(button)
        layout.addStretch()
        return layout

    def _show_screen(self, index: int) -> None:
        """선택한 인덱스로 화면을 전환하고 버튼 하이라이트 적용"""
        self.stack.setCurrentIndex(index)
        for i, button in enumerate(self.nav_buttons):
            button.setObjectName("PrimaryButton" if i == index else "")
            button.style().unpolish(button)
            button.style().polish(button)

    def _app_frame(self, content: QWidget, badge: str = "v0.1.0") -> QFrame:
        """타이틀바와 컨텐츠 영역이 포함된 프레임 디자인"""
        frame = QFrame()
        frame.setObjectName("AppFrame")
        outer = QVBoxLayout(frame)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        title = QFrame()
        title.setObjectName("TitleBar")
        title_layout = QHBoxLayout(title)
        title_layout.setContentsMargins(14, 8, 14, 8)
        title_layout.addWidget(QLabel("UT Automation Client"))
        title_layout.addWidget(QLabel(badge))
        title_layout.addStretch()

        # session_id가 str이므로 str() 변환 그대로 유지
        sid = self.state.session_id if self.state.session_id is not None else "-"
        title_layout.addWidget(muted(f"session_id: {sid}"))

        outer.addWidget(title)
        outer.addWidget(content)
        return frame

    # ──────────────────────────────────────────────
    # 화면 0: 녹화 영역 설정
    # ──────────────────────────────────────────────

    def _build_region_screen(self) -> QWidget:
        """[화면 0] 녹화 영역 설정 화면"""
        body = QWidget()
        layout = QHBoxLayout(body)
        layout.setContentsMargins(0, 0, 0, 0)

        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(250)
        side_layout = QVBoxLayout(sidebar)
        side_layout.setContentsMargins(14, 14, 14, 14)
        side_layout.setSpacing(8)
        side_layout.addWidget(section_label("서버 설정"))
        side_layout.addWidget(QLabel("Server URL"))
        # editingFinished 시그널로 state.server_url 및 ApiClient 갱신
        self.server_url_input = QLineEdit(self.state.server_url)
        self.server_url_input.editingFinished.connect(self._on_server_url_changed)
        side_layout.addWidget(self.server_url_input)
        side_layout.addSpacing(12)
        side_layout.addWidget(section_label("녹화 범위"))

        self.region_preview = RegionPreview(self.state.viewport_region)
        side_layout.addWidget(self.region_preview)

        self.coord_label = muted("x:0.00 y:0.00 | w:1.00 h:1.00")
        side_layout.addWidget(self.coord_label)

        btn_reset = QPushButton("범위 재선택")
        btn_reset.clicked.connect(self._open_region_selector)
        side_layout.addWidget(btn_reset)
        side_layout.addStretch()

        main = QWidget()
        main_layout = QVBoxLayout(main)
        main_layout.setContentsMargins(18, 18, 18, 18)
        main_layout.setSpacing(12)
        main_layout.addWidget(QLabel("<h2>녹화 범위 설정</h2>"))
        main_layout.addWidget(muted("화면 오버레이를 통해 캡처할 영역을 지정하세요."))

        self.grid_container = QWidget()
        main_layout.addWidget(panel("viewport_region 좌표 데이터", self.grid_container))
        self._update_region_grid()
        main_layout.addStretch()

        actions = QHBoxLayout()
        actions.addStretch()
        btn_next = QPushButton("세션 생성 및 다음")
        btn_next.setObjectName("PrimaryButton")
        btn_next.clicked.connect(self._handle_create_session)
        actions.addWidget(btn_next)
        main_layout.addLayout(actions)

        layout.addWidget(sidebar)
        layout.addWidget(main)
        return self._app_frame(body)

    def _update_region_grid(self) -> None:
        """그리드 레이아웃의 자식 위젯을 안전하게 제거하고 갱신"""
        if not self.grid_container.layout():
            QGridLayout(self.grid_container)

        grid = cast(QGridLayout, self.grid_container.layout())
        if grid is not None:
            while grid.count() > 0:
                item = grid.takeAt(0)
                if item is not None:
                    w = item.widget()
                    if w is not None:
                        w.deleteLater()

        payload = self.state.viewport_region.as_payload()
        for i, (name, val) in enumerate(payload.items()):
            grid.addWidget(QLabel(name), (i // 2) * 2, i % 2)
            val_lbl = QLabel(f"{val:.4f}")
            val_lbl.setStyleSheet("color:#7eb8f7; font-size:16px; font-weight:bold;")
            grid.addWidget(val_lbl, (i // 2) * 2 + 1, i % 2)

    def _open_region_selector(self) -> None:
        """영역 선택 오버레이 표시"""
        self.selector = RegionSelector()
        self.selector.region_selected.connect(self._on_region_captured)
        self.selector.show()

    def _on_region_captured(self, rect) -> None:
        """오버레이에서 선택된 사각형 좌표를 state에 반영 및 물리 픽셀 계산"""
        # 1. 현재 앱이 실행 중인 화면의 정보 획득
        screen = QGuiApplication.primaryScreen()
        geo = screen.geometry()
        dpr = screen.devicePixelRatio()  # DPI 배율 (예: 1.5, 2.0)

        # 2. 비율 좌표 저장 (서버 전송용 0.0 ~ 1.0)
        self.state.viewport_region.x = rect.x() / geo.width()
        self.state.viewport_region.y = rect.y() / geo.height()
        self.state.viewport_region.w = rect.width() / geo.width()
        self.state.viewport_region.h = rect.height() / geo.height()
        
        # mss 녹화 엔진을 위한 실제 물리 픽셀 좌표 계산
        # Qt의 rect는 논리 좌표이므로 dpr을 곱해야 실제 픽셀 값이 나옵니다.
        self.pixel_region = {
            "top": int(rect.y() * dpr),
            "left": int(rect.x() * dpr),
            "width": int(rect.width() * dpr),
            "height": int(rect.height() * dpr)
        }

        self.viewport = self.state.viewport_region.as_payload()
        self.region_preview.update()
        self._update_region_grid()
        
        vr = self.state.viewport_region
        self.coord_label.setText(
            f"x:{vr.x:.2f} y:{vr.y:.2f} | w:{vr.w:.2f} h:{vr.h:.2f} (DPR:{dpr:.1f})"
        )

    def _on_server_url_changed(self) -> None:
        """사용자가 Server URL을 수정하면 state와 ApiClient를 즉시 갱신합니다."""
        new_url = self.server_url_input.text().strip()
        if new_url and new_url != self.state.server_url:
            self.state.server_url = new_url
            self.api = ApiClient(ApiConfig(base_url=new_url))

    def _handle_create_session(self) -> None:
        """
        실제 API를 호출하여 세션을 생성합니다.
        create_session() 호출 → UUID string session_id 저장
        """
        try:
            payload = self.state.viewport_region.as_payload()
            session_data = self.api.create_session(viewport_region=payload)

            raw_id = session_data.get("session_id")
            if raw_id is None:
                raise ValueError("서버 응답에 session_id가 없습니다.")

            # session_id는 UUID string
            self.session_id = str(raw_id)
            self.state.session_id = self.session_id

            self._show_screen(1)

        except Exception as e:
            QMessageBox.critical(self, "세션 생성 실패", f"서버에 연결할 수 없습니다:\n{str(e)}")

    # ──────────────────────────────────────────────
    # 화면 1: 캘리브레이션
    # ──────────────────────────────────────────────
                
    def _build_calibration_screen(self) -> QWidget:
        """[화면 1] 5점 캘리브레이션 화면"""
        body = QWidget()
        layout = QHBoxLayout(body)

        sidebar = QFrame()
        sidebar.setFixedWidth(250)
        side_layout = QVBoxLayout(sidebar)
        side_layout.addWidget(section_label("캘리브레이션 상태"))
        self.status_labels = [QLabel(f"{i+1}번 지점: 대기") for i in range(5)]
        for lbl in self.status_labels:
            side_layout.addWidget(lbl)
        side_layout.addStretch()

        main = QWidget()
        main_layout = QVBoxLayout(main)
        self.calib_canvas = CalibrationCanvas()
        self.calib_canvas.point_captured.connect(self._on_calibration_point_captured)
        self.calib_canvas.calibration_finished.connect(self._on_calibration_canvas_done)
        main_layout.addWidget(self.calib_canvas)

        actions = QHBoxLayout()
        # "데이터 수집 시작" 버튼을 _start_calibration_dialog에 연결
        btn_start = QPushButton("데이터 수집 시작")
        btn_start.clicked.connect(self._start_calibration_dialog)
        self.btn_next_test = QPushButton("테스트 단계 이동")
        self.btn_next_test.setEnabled(False)
        self.btn_next_test.clicked.connect(lambda: self._show_screen(2))

        actions.addWidget(btn_start)
        actions.addStretch()
        actions.addWidget(self.btn_next_test)
        main_layout.addLayout(actions)

        layout.addWidget(sidebar)
        layout.addWidget(main)
        return self._app_frame(body)

    def _start_calibration_dialog(self) -> None:
        """
        세션 확인 후 CalibrationDialog(웹캠 촬영)를 실행합니다.
        재시도 시나리오(AI 분석 실패 후 Screen 1 복귀)를 고려하여,
        이전 폴링 워커가 살아있으면 먼저 종료합니다.
        """
        if self.state.session_id is None:
            QMessageBox.warning(self, "세션 없음", "먼저 '녹화 범위 설정' 화면에서 세션을 생성해주세요.")
            self._show_screen(0)
            return

        # 재시도: 이전 CalibrationStatusWorker가 아직 살아있으면 종료
        if hasattr(self, "calib_status_worker") and self.calib_status_worker is not None:
            if self.calib_status_worker.isRunning():
                self.calib_status_worker.stop()
                self.calib_status_worker.wait(2000)
            self.calib_status_worker = None

        # 재시도: 이전 CalibrationWorker도 방어적으로 종료
        if hasattr(self, "calib_worker") and self.calib_worker is not None:
            if self.calib_worker.isRunning():
                self.calib_worker.wait(2000)
            self.calib_worker = None

        # 캔버스 UI 애니메이션 시작
        self.calib_canvas.start_calibration()

        # 실제 웹캠 촬영 다이얼로그 실행
        # 재촬영 시나리오: failed_retry_points가 있으면 해당 포인트만 촬영
        retry_points = getattr(self, "failed_retry_points", None)
        self.calib_dialog = CalibrationDialog(
            self,
            viewport_region=self.viewport,
            points_to_capture=retry_points,  # None이면 전체 5점
        )
        self.failed_retry_points = None  # 소비 후 초기화
        self.calib_dialog.calibration_finished.connect(self.on_calibration_ui_finished)
        self.calib_dialog.exec()

    def _on_calibration_point_captured(self, pt_no: int, sx: float, sy: float) -> None:
        """캔버스 애니메이션 — 지점 캡처 완료 시 UI 업데이트"""
        self.status_labels[pt_no - 1].setText(f"{pt_no}번 지점: ✅ 완료")
        self.status_labels[pt_no - 1].setStyleSheet("color:#86efac;")

    def _on_calibration_canvas_done(self) -> None:
        """캔버스 애니메이션 완료 (실제 업로드 완료와는 별개)"""
        pass  # 업로드 완료는 on_calibration_upload_done에서 처리

    def on_calibration_ui_finished(self, captured_data: list) -> None:
        """웹캠 촬영 완료 → 업로드·분석 대기 화면으로 전환 후 CalibrationWorker 실행"""
        self._show_screen(3)
        self.p_bar.setValue(10)
        self.upload_status_label.setText("캘리브레이션 영상 업로드 중...")

        self.calib_worker = CalibrationWorker(self.api, captured_data)

        # 진행 상황 → 상태 라벨 + 콘솔
        self.calib_worker.progress.connect(
            lambda msg: (
                self.upload_status_label.setText(msg),
                print(f"[캘리브레이션] {msg}")
            )
        )
        # 업로드/시스템 오류
        self.calib_worker.finished.connect(self.on_calibration_upload_done)
        # 성공 → calibration_done, 실패 → calibration_failed (폴링 불필요)
        self.calib_worker.calibration_done.connect(self.on_calibration_approved)
        self.calib_worker.calibration_failed.connect(self._on_calibration_failed)
        self.calib_worker.start()

    def on_calibration_upload_done(self, success: bool, message: str) -> None:
        """
        CalibrationWorker.finished는 이제 업로드 실패·시스템 오류 시에만 호출된다.
        분석 성공/실패는 calibration_done / calibration_failed 시그널이 담당.
        CalibrationStatusWorker 폴링은 PDF 명세에 없는 엔드포인트를 사용하므로 제거.
        """
        if not success:
            self.upload_status_label.setText(f"❌ 오류 발생: {message}")
            QMessageBox.warning(self, "캘리브레이션 오류", f"처리 중 오류가 발생했습니다:\n{message}")
            self._show_screen(1)

    def _update_calibration_status(self, status: str) -> None:
        """
        CalibrationStatusWorker가 순수 status 문자열을 emit하므로 직접 비교하여 진행 상태 업데이트
        """
        print(f"[캘리브레이션] AI 분석 상태: {status}")
        if status == "analyzing":
            self.p_bar.setValue(75)

    def on_calibration_approved(self, result_data: dict) -> None:
        """status=success → 테스트 화면으로 이동"""
        self.p_bar.setValue(100)
        self.upload_status_label.setText("✅ 캘리브레이션 완료!")
        QMessageBox.information(self, "준비 완료", "AI 서버가 시선을 학습했습니다. 테스트를 시작합니다.")
        self._show_screen(2)
        self._start_test()

    def _on_calibration_failed(self, failed_points: list) -> None:
        """캘리브레이션 실패 포인트 안내 및 해당 포인트만 재촬영 유도 """
        pts_str = ", ".join(str(p) for p in failed_points)
        QMessageBox.warning(
            self, "캘리브레이션 실패",
            f"다음 포인트에서 얼굴이 감지되지 않았습니다: {pts_str}\n해당 포인트만 다시 촬영합니다."
        )
        # 실패 point_no 번호로 재촬영 대상만 필터링해서 저장
        _ALL = CalibrationDialog._DEFAULT_POINTS
        self.failed_retry_points = [p for p in _ALL if p["point_no"] in failed_points]
        self._show_screen(1)

    # ──────────────────────────────────────────────
    # 화면 2: 테스트 진행
    # ──────────────────────────────────────────────
    
    def _build_test_screen(self) -> QWidget:
            """[화면 2] 시선 추적 테스트 진행 화면 (카메라 및 태스크 제어 포함)"""
            body = QWidget()
            layout = QHBoxLayout(body)
            main = QWidget()
            main_layout = QVBoxLayout(main)

            url_bar = QHBoxLayout()
            self.test_url_input = QLineEdit("https://example.com")
            self.test_url_input.returnPressed.connect(self._load_test_url)
            btn_go = QPushButton("이동")
            btn_go.clicked.connect(self._load_test_url)
            url_bar.addWidget(self.test_url_input)
            url_bar.addWidget(btn_go)
            main_layout.addLayout(url_bar)

            if QWebEngineView is not None:
                self.web_view = QWebEngineView()
                self.web_view.setMinimumHeight(350)
                self.web_view.urlChanged.connect(self._on_browser_url_changed)
                main_layout.addWidget(self.web_view)
            # ------------------------------------------------------

            # 3. 우측 패널 (카메라 미리보기 + 태스크 제어)
            task_panel = QFrame()
            task_panel.setFixedWidth(280)
            task_panel.setStyleSheet("background: #21262d; border-radius: 8px;")
            
            self.task_layout = QVBoxLayout(task_panel)
            
            # 4. 웹캠 미리보기 섹션
            self.task_layout.addWidget(section_label("👤 사용자 캠"))
            
            # 카메라 설정을 호출하여 self.viewfinder를 생성
            self._setup_camera() 
            
            # 생성된 viewfinder를 레이아웃에 추가
            if hasattr(self, 'viewfinder'):
                self.task_layout.addWidget(self.viewfinder)
            
            self.task_layout.addSpacing(10)
            self.task_layout.addWidget(section_label("📋 테스트 시나리오"))

            self.tasks = self._load_tasks_config()
            self.task_layout.addStretch()   # 태스크 버튼은 start_test()에서 동적 생성
            self._task_group_container = QWidget()
            self._task_group_layout = QVBoxLayout(self._task_group_container)
            self._task_group_layout.setContentsMargins(0, 0, 0, 0)
            self.task_layout.insertWidget(self.task_layout.count() - 1, self._task_group_container)
            self._rebuild_task_buttons()

            # 하단 종료 버튼
            btn_finish = QPushButton("테스트 종료 및 데이터 전송")
            btn_finish.setObjectName("PrimaryButton")
            btn_finish.clicked.connect(self._handle_test_finished)
            main_layout.addWidget(btn_finish)

            layout.addWidget(main)
            layout.addWidget(task_panel)
            return self._app_frame(body, "recording")

    def _load_tasks_config(self) -> List[str]:
        """
        tasks_config.json에서 태스크 이름 목록을 읽어온다.
        파일이 없거나 파싱 실패 시 기본값을 반환한다.
        tasks_config.json 위치: 프로젝트 루트 (main.py와 같은 디렉토리)
        """
        import json
        config_path = os.path.join(ROOT_DIR, "tasks_config.json")
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            tasks = [t["name"] for t in data.get("tasks", []) if "name" in t]
            if tasks:
                print(f"[태스크] tasks_config.json 로드 완료: {tasks}")
                return tasks
        except FileNotFoundError:
            print(f"[태스크] tasks_config.json 없음 → 기본값 사용 ({config_path})")
        except Exception as e:
            print(f"[태스크] tasks_config.json 파싱 오류 → 기본값 사용: {e}")

        # 기본 태스크 (개발/테스트용)
        return ["메인 로고 클릭", "검색바 입력", "상세 페이지 이동"]

    def _rebuild_task_buttons(self) -> None:
        """
        self.tasks 목록을 기반으로 태스크 QGroupBox 버튼들을 동적으로 재생성한다.
        start_test() 호출 시 tasks_config.json을 다시 읽어 갱신할 수 있다.
        """
        # [수정] 기존 버튼 제거 시 None 체크를 추가하여 Pylance 오류 해결
        while self._task_group_layout.count():
            item = self._task_group_layout.takeAt(0)
            if item is not None:
                widget = item.widget()
                if widget is not None:
                    widget.deleteLater()
                else:
                    # 위젯이 없는 아이템(예: Spacer)인 경우 레이아웃에서 안전하게 제거
                    pass

        for i, t_name in enumerate(self.tasks, 1):
            t_group = QGroupBox(f"Task {i}")
            t_group_layout = QVBoxLayout(t_group)

            label = QLabel(t_name)
            btn_start = QPushButton("시작")
            btn_success = QPushButton("성공")
            btn_success.setVisible(False)
            btn_fail = QPushButton("실패")
            btn_fail.setVisible(False)

            # 각 버튼에 람다를 연결하여 태스크 상태 제어
            btn_start.clicked.connect(
                lambda chk, n=t_name, o=i, s=btn_start, ok=btn_success, no=btn_fail:
                self._ui_start_task(n, o, s, ok, no)
            )
            btn_success.clicked.connect(
                lambda chk, ok=btn_success, no=btn_fail:
                self._ui_finish_task(True, ok, no)
            )
            btn_fail.clicked.connect(
                lambda chk, ok=btn_success, no=btn_fail:
                self._ui_finish_task(False, ok, no)
            )

            t_group_layout.addWidget(label)
            t_group_layout.addWidget(btn_start)
            t_group_layout.addWidget(btn_success)
            t_group_layout.addWidget(btn_fail)
            self._task_group_layout.addWidget(t_group)

    def _setup_camera(self):
        """웹캠 초기화 및 캡처 세션 설정"""
        try:
            # 카메라 객체 생성
            self.camera = QCamera()
            self.capture_session = QMediaCaptureSession()
            self.capture_session.setCamera(self.camera)
            
            # 비디오 출력 위젯 설정
            self.viewfinder = QVideoWidget()
            self.viewfinder.setFixedSize(260, 180) # 패널 너비에 맞춰 조정
            self.viewfinder.setStyleSheet("border: 1px solid #30363d; background: black; border-radius: 4px;")
            
            self.capture_session.setVideoOutput(self.viewfinder)
            
            # 카메라 시작
            self.camera.start()
        except Exception as e:
            print(f"⚠️ 카메라 설정을 완료할 수 없습니다: {e}")

    # --- 태스크 제어용 헬퍼 함수 ---
    def _ui_start_task(self, name: str, order: int, btn_s: QPushButton, btn_ok: QPushButton, btn_no: QPushButton):
        """인자 5개를 정확히 받아 처리 (MainWindow 내부 정의)"""
        # 1. 데이터 기록 시작 (ScreenRecorder 등 연동)
        self._start_task(name, order)
        
        # 2. 버튼 상태 변경
        btn_s.setEnabled(False)
        btn_s.setText("진행 중...")
        btn_ok.setVisible(True)
        btn_no.setVisible(True)

    def _ui_finish_task(self, is_success: bool, btn_ok: QPushButton, btn_no: QPushButton):
        """인자 3개를 정확히 받아 처리"""
        # 1. 결과 데이터 저장 (is_success 반영)
        self._finish_task(is_success)
        
        # 2. UI 비활성화
        btn_ok.setEnabled(False)
        btn_no.setEnabled(False)
        
        # 3. 다음 태스크 인덱스 증가 및 종료 체크
        if self.current_task_index < len(self.tasks) - 1:
            self.current_task_index += 1
        else:
            # 모든 태스크 완료 시 v5 명세에 따른 종료 처리
            self._handle_test_finished()

    def _start_test(self):
        """
        본 테스트 및 영상 녹화 시작.
        카메라는 유지하고 화면 캡처만 별도 스레드에서 실행한다.
        """
        if self.is_recording:
            return

        try:
            ts = int(time.time())
            self.video_output_path = os.path.abspath(f"session_test_{ts}.mp4")
            self.current_video_path = self.video_output_path

            self.page_logs = []
            self.task_results = []
            self.current_task_index = 0
            self.current_task_info = None
            self.is_finalized = False
            self.test_running = True
            self.tasks = self._load_tasks_config()
            self._rebuild_task_buttons()

            pixel_region = self._get_recording_region()
            self._resume_camera_and_start_record(pixel_region)
            self._load_test_url()
            
        except Exception as e:
            print(f"시작 오류: {e}")

    def start_test(self) -> None:
        """기존 호출부 호환용 래퍼."""
        self._start_test()
    
    def _resume_camera_and_start_record(self, pixel_region):
        """카메라를 안정적으로 재개한 후 녹화 워커를 구동합니다."""
        # QCamera 프리뷰는 본 녹화와 별도이므로 멈추지 않는다. 꺼져 있을 때만 켠다.
        if hasattr(self, 'camera') and self.camera and not self.camera.isActive():
            self.camera.start()
        
        # 녹화 워커 생성 및 실행
        from utils.workers import RecordingWorker
        self.recording_worker = RecordingWorker(
            recorder=self.recorder,
            pixel_region=pixel_region,
            output_path=self.video_output_path
        )
        
        # 종료 시그널 연결 (병합 완료 확인용)
        self.recording_worker.finished.connect(lambda: print("✅ 병합 완료"))
        
        self.recording_worker.start()
        self.is_recording = True
        print(f"🎬 녹화 및 카메라 정상 작동 중: {self.video_output_path}")

    def _get_recording_region(self) -> dict:
        """
        선택 영역이 있으면 그 영역을, 없으면 테스트 브라우저 영역을 물리 픽셀 기준으로 반환한다.
        """
        if hasattr(self, "pixel_region"):
            return dict(self.pixel_region)

        target = getattr(self, "web_view", None) or self.centralWidget()
        top_left = target.mapToGlobal(QPoint(0, 0))

        screen = target.windowHandle().screen() if target.windowHandle() else QGuiApplication.primaryScreen()
        dpr = screen.devicePixelRatio() if screen else 1.0

        return {
            "left": int(top_left.x() * dpr),
            "top": int(top_left.y() * dpr),
            "width": int(target.width() * dpr),
            "height": int(target.height() * dpr),
        }

    def record_page_entry(self, url: str) -> None:
        """페이지 진입 시 로그 기록 (절대 타임스탬프 기준)"""
        if self.page_logs and self.page_logs[-1]["url"] == url and self.page_logs[-1]["end_video_ts"] is None:
            return

        current_ts = self.recorder.get_elapsed_time()

        if self.page_logs:
            self.page_logs[-1]['end_video_ts'] = current_ts

        new_log = {
            "page_no": len(self.page_logs) + 1,
            "url": url,
            "start_video_ts": current_ts,
            "end_video_ts": None,
            "screenshot_path": "",
        }
        self.page_logs.append(new_log)

    def _load_test_url(self) -> None:
        raw_url = self.test_url_input.text().strip()
        if not raw_url:
            return

        if "://" not in raw_url:
            raw_url = f"https://{raw_url}"
            self.test_url_input.setText(raw_url)

        # web_view는 WebEngine 설치 시에만 존재 → hasattr 방어
        if hasattr(self, 'web_view') and self.web_view is not None:
            self.web_view.setUrl(QUrl(raw_url))
        elif self.test_running:
            # WebEngine 미설치 환경: URL 변경을 수동으로 기록하고 스크린샷 촬영
            self.record_page_entry(raw_url)
            if self.page_logs:
                self._capture_and_upload_screenshot(self.page_logs[-1])

    def _on_browser_url_changed(self, url: QUrl) -> None:
        url_text = url.toString()
        if not url_text or url_text == "about:blank":
            return

        self.test_url_input.setText(url_text)
        if self.test_running:
            self.record_page_entry(url_text)
            # URL 변경마다 스크린샷 캡처 후 MinIO 비동기 업로드
            if self.page_logs:
                self._capture_and_upload_screenshot(self.page_logs[-1])
                
    def _stop_test(self):
        """테스트 종료 및 녹화 중지"""
        if hasattr(self, 'recorder'):
            self.recorder.stop()
        self.is_recording = False
        print("⏹️ 녹화 요청 전송됨 (병합 대기 중...)")
        
    # ──────────────────────────────────────────────
    # 화면 3: 업로드/분석 대기
    # ──────────────────────────────────────────────

    def _build_upload_screen(self) -> QWidget:
            """[화면 3] 데이터 전송 및 분석 대기 화면"""
            body = QWidget()
            layout = QVBoxLayout(body)
            layout.addStretch()

            # 상태 라벨
            self.upload_status_label.setStyleSheet("font-size: 16px; font-weight: bold;")
            layout.addWidget(self.upload_status_label, 0, Qt.AlignmentFlag.AlignCenter)

            # 프로그레스 바 설정
            self.p_bar = QProgressBar()
            self.p_bar.setFixedWidth(400)
            self.p_bar.setRange(0, 100)  # 0%에서 100%까지 표시
            self.p_bar.setValue(0)       # 처음엔 0으로 시작
            layout.addWidget(self.p_bar, 0, Qt.AlignmentFlag.AlignCenter)
            
            layout.addStretch()
            return self._app_frame(body, "upload")

    def _handle_test_finished(self) -> None:
            """
            테스트 종료 처리: 마지막 로그 기록 및 녹화 중지 (1회성 실행)
            """
            # 1. 마지막 페이지 로그 종료 시간 기록 (중복 기록 방지)
            if not hasattr(self, 'is_finalized') or not self.is_finalized:
                if hasattr(self, 'page_logs') and self.page_logs:
                    # recorder에서 현재까지의 경과 시간을 가져와 마지막 로그 마감
                    self.page_logs[-1]['end_video_ts'] = round(self.recorder.get_elapsed_time(), 3)
                
                # 2. 녹화 중지 및 스레드 자원 회수
                if self.recorder.is_recording:
                    self.recorder.stop()
                    if hasattr(self, "recording_worker") and self.recording_worker.isRunning():
                        self.recording_worker.wait()
                
                self.test_running = False
                self.is_recording = False
                self.is_finalized = True # 1회성 로직 완료 플래그

            # 3. 화면 전환 (Index 3: 업로드 대기 화면)
            self._show_screen(3)
            
            # 4. 실제 업로드 프로세스 시작
            self._start_upload_process()

    def _start_upload_process(self) -> None:
            """
            업로드 전용 경로: Presigned URL 발급 및 데이터 전송 시작
            """
            self.is_uploading = True
            self.p_bar.setValue(0)
            self.upload_status_label.setText("데이터를 정리하여 서버로 전송 중입니다...")

            # 메타데이터 구성
            metadata = {
                "page_logs": self.page_logs,
                "task_results": self.task_results
            }

            # hasattr뿐만 아니라 None 체크를 동시에 수행하여 AttributeError 방지
            if hasattr(self, 'upload_worker') and self.upload_worker is not None:
                if self.upload_worker.isRunning():
                    self.upload_worker.quit()
                    self.upload_worker.wait()

            from utils.workers import UploadWorker
            # 새로운 워커 인스턴스 할당
            self.upload_worker = UploadWorker(
                api_client=self.api,
                video_path=self.video_output_path,
                metadata=metadata
            )

            # 시그널 연결
            self.upload_worker.progress.connect(self._on_upload_progress)
            self.upload_worker.finished.connect(self._on_upload_finished)
            self.upload_worker.start()

    def _on_upload_progress(self, message: str, value: Optional[int] = None) -> None:
        """업로드 진행 상황 업데이트"""
        self.upload_status_label.setText(message)
        if value is not None:
            self.p_bar.setValue(value)

    @Slot(bool, str)
    def _on_upload_finished(self, success: bool, message: str) -> None:
        """업로드 워커 종료 시 호출되는 슬롯"""
        if success:
            self.upload_status_label.setText("✅ 업로드 성공! AI 분석 요청이 접수되었습니다.")
            self.p_bar.setValue(100)
            self.is_uploading = False
            self.report_result = {"status": "accepted", "message": message}
            self.is_finalized = False # 다음 테스트를 위해 플래그 초기화
            self._show_screen(4)
        else:
            self.is_uploading = False
            
            msg_box = QMessageBox(self)
            msg_box.setIcon(QMessageBox.Icon.Critical) 
            msg_box.setWindowTitle("업로드 실패")
            msg_box.setText(f"데이터 전송에 실패했습니다.\n사유: {message}")
            
            # 버튼 설정
            retry_btn = msg_box.addButton("다시 시도", QMessageBox.ButtonRole.ActionRole)
            cancel_btn = msg_box.addButton("테스트 화면으로 돌아가기", QMessageBox.ButtonRole.RejectRole)
            
            msg_box.exec()
            
            if msg_box.clickedButton() == retry_btn:
                # 처음부터 다시 하는 대신, 업로드 로직만 재실행
                self._start_upload_process()
            else:
                # 복구 및 초기화
                self.is_finalized = False 
                self.test_running = True 
                self._show_screen(2)
                
    def _start_analysis_polling(self) -> None:
        """
        분석 상태 감시 시작
        [수정] status_updated 시그널을 _on_analysis_status_updated 메서드에 직접 연결하여 
        상태별 분기 처리가 가능하도록 수정했습니다.
        """
        from utils.workers import AnalysisStatusWorker
        
        # 기존 워커가 있다면 안전하게 정리
        if self.analysis_status_worker and self.analysis_status_worker.isRunning():
            self.analysis_status_worker.stop()
            self.analysis_status_worker.wait()

        self.analysis_status_worker = AnalysisStatusWorker(self.api)
        
        # 기존 람다 함수 대신 전용 핸들러 메서드에 직접 연결
        self.analysis_status_worker.status_updated.connect(self._on_analysis_status_updated)
        
        self.analysis_status_worker.analysis_finished.connect(self._on_analysis_complete)
        self.analysis_status_worker.start()

    def _on_analysis_complete(self, data: dict) -> None:
        """분석이 성공적으로 완료되었을 때 호출"""
        self.is_uploading = False
        self.upload_status_label.setText("🎉 분석이 완료되었습니다!")
        
        # Index 4(리포트 화면)로 자동 전환하거나 버튼 활성화
        self._show_screen(4)
        QMessageBox.information(self, "분석 완료", "최종 리포트가 생성되었습니다.")

    def _on_analysis_status_updated(self, status: str) -> None:
        """
        분석 상태 변경 시 호출되는 슬롯 (UI 갱신 및 에러 처리)
        """
        # 기본 상태 메시지 출력
        self.upload_status_label.setText(f"🧐 AI 분석 중: {status}...")
        print(f"[분석 상태] {status}")

        if status == "generating":
            # 보고서 생성 단계 진입 시 프로그레스 바 업데이트
            self.p_bar.setValue(90)
        
        elif status == "failed":
            # 분석 실패 시 사용자에게 알림을 표시하고 리소스 정리
            if self.analysis_status_worker:
                self.analysis_status_worker.stop()
            
            QMessageBox.critical(
                self, 
                "분석 실패", 
                "서버 분석 또는 보고서 생성에 실패했습니다.\n잠시 후 다시 시도하거나 관리자에게 문의하세요."
            )
            # 실패 시 결과 화면(Index 4)으로 강제 이동시키거나 이전 단계로 복구
            self._show_screen(4)
            
    def _on_analysis_finished(self, result: Dict[str, Any]) -> None:
        self.p_bar.setValue(100)
        self.report_result = result
        self._show_screen(4)
        
    def _start_task(self, task_name: str, task_order: int) -> None:
        """
        태스크 시작 시 호출: 시작 시간 기록 및 현재 활성 태스크 설정
        """
        self.current_task_info = {
            "task_name": task_name,
            "task_order": task_order,
            "start_time": time.time()  # duration 계산을 위한 실제 시간
        }
        print(f"🚀 태스크 시작: {task_name} (순서: {task_order})")
    
    def _load_tasks(self) -> None:
        """
        명시된 TASKS_CONFIG_PATH에서 파일을 읽어옵니다.
        파일이 없을 경우 사용자에게 위치 안내를 제공합니다.
        """
        import json
        if os.path.exists(TASKS_CONFIG_PATH):
            try:
                with open(TASKS_CONFIG_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.tasks = data.get("tasks", [])
            except Exception as e:
                print(f"설정 파일 읽기 오류: {e}")
                self.tasks = ["기본 태스크 1"]
        else:
            # 파일이 없을 경우 가이드 출력 및 기본값 설정
            print(f"경고: 설정 파일을 찾을 수 없습니다. 위치: {TASKS_CONFIG_PATH}")
            QMessageBox.warning(
                self, "설정 파일 누락",
                f"태스크 설정 파일이 없습니다.\n다음 위치에 파일을 생성해주세요:\n{TASKS_CONFIG_PATH}"
            )
            self.tasks = ["기본 태스크 1"]

    def _finish_task(self, is_success: bool) -> None:
        """
        태스크 종료 시 호출: 결과 판단, 소요 시간(duration) 계산 및 저장
        """
        if not hasattr(self, 'current_task_info') or self.current_task_info is None:
            print("⚠️ 진행 중인 태스크가 없습니다.")
            return

        # 1. 소요 시간 계산
        duration = round(time.time() - self.current_task_info["start_time"], 2)
        
        # 2. 결과 데이터 구성 (PDF 명세 CL-5: result = "success" | "fail", 한글 금지)
        task_entry = {
            "task_order": self.current_task_info["task_order"],
            "result": "success" if is_success else "fail",
            "duration_sec": duration,
        }

        # 3. 메인 저장소에 추가
        self.task_results.append(task_entry)
        
        # 4. 현재 태스크 초기화
        self.current_task_info = None
        print(f"🏁 태스크 종료: {task_entry['result']} (소요시간: {duration}초)")
    
    def add_task_result(self, task_id: int, success: bool, duration: float):
        """태스크 결과를 수집하여 리스트에 추가"""
        result = {
            "task_id": task_id,
            "success": success,
            "completion_time": duration,
            "timestamp": time.time()
        }
        self.task_results.append(result)

    def _capture_and_upload_screenshot(self, log_entry: dict) -> None:
            """
            현재 화면 전체를 캡처하여 디스크 저장 없이 메모리상에서 즉시 서버로 업로드하는 시퀀스를 시작합니다.
            
            :param log_entry: 화면 이동 로그 기록을 담고 있는 딕셔너리 객체 (업로드 완료 후 경로 업데이트용)
            """
            # 1. 현재 운영체제에서 사용 중인 메인 스크린 객체를 획득
            screen = QGuiApplication.primaryScreen()
            if not screen:
                print("⚠️ 활성화된 스크린을 찾을 수 없어 캡처를 중단합니다.")
                return

            # 2. 메인 스크린의 전체 화면을 QPixmap 형태로 캡처(Grab)
            pixmap = screen.grabWindow(0)
            
            # 3. 디스크에 임시 PNG 파일을 쓰지 않기 위해 PySide6의 메모리 버퍼 시스템을 활용
            byte_array = QByteArray()                # 바이너리 데이터를 담을 바이트 배열 생성
            buffer = QBuffer(byte_array)             # 바이트 배열을 버퍼 장치에 연결
            buffer.open(QIODevice.OpenModeFlag.WriteOnly) # 쓰기 전용 모드로 버퍼를 오픈
            pixmap.save(buffer, "PNG")               # 캡처한 이미지를 PNG 포맷의 바이너리로 버퍼에 기록
            image_bytes = byte_array.data()          # 최종적으로 Python에서 사용할 수 있는 bytes 형태로 변환

            # 4. 고유한 작업을 식별하기 위해 현재까지 기록된 페이지 로그의 길이를 임시 ID로 설정
            log_id = str(len(self.page_logs))

            # 5. UI 메인 스레드가 멈추지 않도록 비동기 스레드인 ScreenshotUploadWorker를 생성
            worker = ScreenshotUploadWorker(self.api, image_bytes, log_id)
            
            # 6. 파이썬의 가비지 컬렉터(GC)에 의해 워커 객체가 도중에 소멸되는 것을 방지하기 위해 리스트에 참조를 유지
            if not hasattr(self, 'screenshot_workers'):
                self.screenshot_workers = []
            self.screenshot_workers.append(worker)

            # 7. 워커의 작업 완료 시그널과 결과 처리 슬롯(함수)을 연결
            # 람다 식을 활용하여 현재 어떤 로그 엔트리를 업데이트해야 하는지 참조를 함께 전달
            worker.finished.connect(
                lambda success, path, lid: self._on_screenshot_upload_finished(
                    worker, success, path, lid, log_entry
                )
            )
            
            # 8. 백그라운드 스레드 작업을 시작
            worker.start()

    @Slot(bool, str, str)
    def _on_screenshot_upload_finished(self, worker: ScreenshotUploadWorker, success: bool, result: str, log_id: str, log_entry: dict) -> None:
        """
        스크린샷 업로드 스레드가 작업을 끝마쳤을 때 호출되는 결과 처리 슬롯입니다.
        
        :param worker: 작업을 수행한 ScreenshotUploadWorker 객체 (리스트에서 제거하기 위함)
        :param success: 업로드 성공 여부 (True/False)
        :param result: 성공 시 MinIO의 객체 경로(object_key), 실패 시 에러 메시지
        :param log_id: 작업을 요청할 때 부여했던 임시 로그 식별 ID
        :param log_entry: 경로를 채워 넣어야 하는 대상 페이지 로그 딕셔너리
        """
        if success:
            # 업로드 성공 시: 비어있던 screenshot_path 키에 서버가 반환해준 저장 경로를 채움
            log_entry["screenshot_path"] = result
            print(f"📸 [스크린샷] 서버 업로드 성공 및 경로 저장 완료: {result}")
        else:
            # 업로드 실패 시: 로그 기록을 보존하되 에러 내용을 콘솔에 출력합니다.
            print(f"❌ [스크린샷] 업로드 실패 사유: {result} (로그 ID: {log_id})")
        
        # 사용이 완료된 워커 객체는 메모리 누수 방지를 위해 관리 리스트에서 제거
        if hasattr(self, 'screenshot_workers') and worker in self.screenshot_workers:
            self.screenshot_workers.remove(worker)
            
    # ──────────────────────────────────────────────
    # 화면 4: 보고서
    # ──────────────────────────────────────────────

    def _build_report_screen(self) -> QWidget:
        """[화면 4] 분석 보고서 다운로드 화면"""
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.addStretch()
        btn = QPushButton("보고서(PDF) 내려받기")
        btn.setObjectName("SuccessButton")
        btn.setFixedSize(220, 50)
        btn.clicked.connect(self._handle_report_download)
        layout.addWidget(btn, 0, Qt.AlignmentFlag.AlignCenter)
        layout.addStretch()
        return self._app_frame(body, "done")

    def _handle_report_download(self) -> None:
        result = getattr(self, "report_result", None) or self.api.get_report_status()
        if isinstance(result, dict) and result.get("status") == "accepted":
            result = self.api.get_report_status()
        pdf_bytes = result.get("pdf_bytes") if isinstance(result, dict) else None
        if pdf_bytes:
            reports_dir = os.path.abspath("reports")
            os.makedirs(reports_dir, exist_ok=True)
            session_part = self.state.session_id or "latest"
            pdf_path = os.path.join(reports_dir, f"report_{session_part}.pdf")
            with open(pdf_path, "wb") as file:
                file.write(pdf_bytes)
            QMessageBox.information(self, "보고서 저장 완료", f"PDF 저장 경로:\n{pdf_path}")
            return

        pdf_url = result.get("pdf_url") or result.get("pdf_presigned_url") or result.get("download_url")
        if pdf_url:
            QMessageBox.information(self, "보고서 준비 완료", f"보고서 URL:\n{pdf_url}")
            return

        QMessageBox.information(self, "보고서 상태", f"현재 상태: {result.get('status', 'unknown')}")

    def closeEvent(self, event) -> None:
            """
            프로그램 종료 시 호출되는 이벤트 핸들러.
            스레드 객체가 생성되었는지(None이 아닌지) 먼저 확인한 후 안전하게 종료합니다.
            """
            # 1. 업로드 상태 플래그 확인
            is_uploading = getattr(self, 'is_uploading', False)

            if is_uploading:
                reply = QMessageBox.warning(
                    self, 
                    "업로드 진행 중",
                    "현재 데이터 업로드가 진행 중입니다.\n종료하시면 분석 데이터가 모두 삭제됩니다. 정말 종료하시겠습니까?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No
                )

                if reply == QMessageBox.StandardButton.Yes:
                    print("⚠️ 업로드 중 강제 종료 시퀀스를 시작합니다.")
                    
                    # 서버 세션 중단 요청 (구현되어 있다면 호출)
                    try:
                        if hasattr(self, 'api') and self.api:
                            self.api.abort_session()
                    except Exception as e:
                        print(f"세션 중단 실패: {e}")

                    # upload_worker가 실제로 생성되어 있는지(None이 아닌지) 확인
                    if hasattr(self, 'upload_worker') and self.upload_worker is not None:
                        if self.upload_worker.isRunning():
                            self.upload_worker.terminate()
                            self.upload_worker.wait()

                    event.accept()
                else:
                    event.ignore()
                    return

            else:
                # 업로드 중이 아닐 때 일반 리소스 정리
                if hasattr(self, "calib_status_worker") and self.calib_status_worker is not None:
                    if self.calib_status_worker.isRunning():
                        self.calib_status_worker.stop()
                        self.calib_status_worker.wait(2000)
                if hasattr(self, "calib_worker") and self.calib_worker is not None:
                    if self.calib_worker.isRunning():
                        self.calib_worker.wait(2000)
                if hasattr(self, "analysis_status_worker") and self.analysis_status_worker is not None:
                    if self.analysis_status_worker.isRunning():
                        self.analysis_status_worker.stop()
                        self.analysis_status_worker.wait(1000)
                self._cleanup_resources()
                event.accept()

    def _cleanup_resources(self):
        """카메라 및 녹화 리소스를 안전하게 해제하는 헬퍼 함수"""
        # 카메라 정지
        if hasattr(self, 'camera') and self.camera is not None:
            if self.camera.isActive():
                self.camera.stop()
        
        # 녹화 엔진 정지
        if hasattr(self, 'recorder') and self.recorder is not None:
            if self.recorder.is_recording:
                self.recorder.stop()
        if hasattr(self, "recording_worker") and self.recording_worker is not None:
            if self.recording_worker.isRunning():
                self.recording_worker.wait(2000)
