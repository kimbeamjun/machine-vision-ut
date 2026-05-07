import sys
import os
import time
from typing import Any, Dict, List, cast, Optional

# 폴더 구조 확인 및 루트 경로
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.abspath(os.path.join(CURRENT_DIR, ".."))

if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from PySide6.QtMultimedia import QCamera, QMediaCaptureSession, QImageCapture
from PySide6.QtMultimediaWidgets import QVideoWidget    
from PySide6.QtGui import QGuiApplication
from PySide6.QtCore import Qt, QThreadPool, Slot, QTimer, QUrl, QByteArray, QBuffer, QIODevice
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
        self.task_results = []   # 태스크 수행 결과 (dict 형태 등)

        # 3. 설정 및 스레드 풀 객체 생성
        self.state = ClientState()
        self.thread_pool = QThreadPool.globalInstance()

        # 4. API 클라이언트 초기화
        config = ApiConfig(base_url="http://10.10.10.113:8001")
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
        # [FIX] editingFinished 시그널로 state.server_url 및 ApiClient 갱신
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
        """오버레이에서 선택된 사각형 좌표를 state에 반영 (비율로 변환)"""
        geo = QApplication.primaryScreen().geometry()
        self.state.viewport_region.x = rect.x() / geo.width()
        self.state.viewport_region.y = rect.y() / geo.height()
        self.state.viewport_region.w = rect.width() / geo.width()
        self.state.viewport_region.h = rect.height() / geo.height()
        # viewport dict도 동기화
        self.viewport = self.state.viewport_region.as_payload()
        self.region_preview.update()
        self._update_region_grid()
        # [FIX] 사이드바 좌표 텍스트를 갱신된 비율로 업데이트
        vr = self.state.viewport_region
        self.coord_label.setText(
            f"x:{vr.x:.2f} y:{vr.y:.2f} | w:{vr.w:.2f} h:{vr.h:.2f}"
        )

    def _on_server_url_changed(self) -> None:
        """[FIX] 사용자가 Server URL을 수정하면 state와 ApiClient를 즉시 갱신합니다."""
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

        # [FIX] 재시도: 이전 CalibrationStatusWorker가 아직 살아있으면 종료
        if hasattr(self, "calib_status_worker") and self.calib_status_worker is not None:
            if self.calib_status_worker.isRunning():
                self.calib_status_worker.stop()
                self.calib_status_worker.wait(2000)
            self.calib_status_worker = None

        # [FIX] 재시도: 이전 CalibrationWorker도 방어적으로 종료
        if hasattr(self, "calib_worker") and self.calib_worker is not None:
            if self.calib_worker.isRunning():
                self.calib_worker.wait(2000)
            self.calib_worker = None

        # 캔버스 UI 애니메이션 시작
        self.calib_canvas.start_calibration()

        # 실제 웹캠 촬영 다이얼로그 실행
        self.calib_dialog = CalibrationDialog(self, viewport_region=self.viewport)
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
        """웹캠 촬영 완료 후 업로드 워커 실행 — 분석 대기 화면으로 전환"""
        self._show_screen(3)
        self.p_bar.setValue(10)

        self.calib_worker = CalibrationWorker(self.api, captured_data)
        self.calib_worker.progress.connect(lambda msg: print(f"[캘리브레이션] {msg}"))
        self.calib_worker.finished.connect(self.on_calibration_upload_done)
        self.calib_worker.start()

    def on_calibration_upload_done(self, success: bool, message: str) -> None:
        """업로드 완료 후 캘리브레이션 AI 분석 상태 폴링 시작"""
        if success:
            self.p_bar.setValue(50)
            self.calib_status_worker = CalibrationStatusWorker(self.api)
            self.calib_status_worker.status_updated.connect(self._update_calibration_status)
            self.calib_status_worker.calibration_done.connect(self.on_calibration_approved)
            self.calib_status_worker.calibration_failed.connect(self._on_calibration_failed)
            self.calib_status_worker.start()
        else:
            QMessageBox.warning(self, "업로드 실패", f"캘리브레이션 영상 업로드 실패:\n{message}")
            self._show_screen(1)

    def _update_calibration_status(self, status: str) -> None:
        """
        CalibrationStatusWorker가 순수 status 문자열을 emit하므로 직접 비교하여 진행 상태 업데이트
        """
        print(f"[캘리브레이션] AI 분석 상태: {status}")
        if status == "analyzing":
            self.p_bar.setValue(75)

    def on_calibration_approved(self, result_data: dict) -> None:
        """캘리브레이션 AI 분석 완료 → 화면 녹화 시작"""
        self.p_bar.setValue(100)
        QMessageBox.information(self, "준비 완료", "AI 서버가 시선을 학습했습니다. 테스트를 시작합니다.")
        self._show_screen(2)
        self.start_test()

    def _on_calibration_failed(self, failed_points: list) -> None:
        """캘리브레이션 실패 포인트 안내 및 재촬영 유도"""
        pts_str = ", ".join(str(p) for p in failed_points)
        QMessageBox.warning(
            self, "캘리브레이션 실패",
            f"다음 포인트에서 얼굴이 감지되지 않았습니다: {pts_str}\n해당 포인트를 다시 촬영해주세요."
        )
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

            # --- (중략: 상단 URL 바 및 브라우저 영역은 기존과 동일) ---
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
            
            # [해결 포인트] task_layout을 먼저 정의합니다.
            self.task_layout = QVBoxLayout(task_panel)
            
            # 4. 웹캠 미리보기 섹션 추가
            self.task_layout.addWidget(section_label("👤 사용자 캠"))
            
            # 카메라 설정을 호출하여 self.viewfinder를 생성합니다.
            self._setup_camera() 
            
            # 생성된 viewfinder를 레이아웃에 추가합니다.
            if hasattr(self, 'viewfinder'):
                self.task_layout.addWidget(self.viewfinder)
            
            self.task_layout.addSpacing(10)
            self.task_layout.addWidget(section_label("📋 테스트 시나리오"))

            # --- 태스크 버튼 생성 로직 (기존과 동일) ---
            self.tasks = ["메인 로고 클릭", "검색바 입력", "상세 페이지 이동"]
            for i, t_name in enumerate(self.tasks, 1):
                t_group = QGroupBox(f"Task {i}")
                t_group_layout = QVBoxLayout(t_group)
                label = QLabel(t_name)
                
                btn_start = QPushButton("시작")
                btn_success = QPushButton("성공")
                btn_success.setVisible(False)
                btn_fail = QPushButton("실패")
                btn_fail.setVisible(False)

                # 시그널 연결
                btn_start.clicked.connect(lambda chk, n=t_name, o=i, s=btn_start, ok=btn_success, no=btn_fail: 
                                        self._ui_start_task(n, o, s, ok, no))
                btn_success.clicked.connect(lambda chk, ok=btn_success, no=btn_fail: self._ui_finish_task(True, ok, no))
                btn_fail.clicked.connect(lambda chk, ok=btn_success, no=btn_fail: self._ui_finish_task(False, ok, no))

                t_group_layout.addWidget(label)
                t_group_layout.addWidget(btn_start)
                t_group_layout.addWidget(btn_success)
                t_group_layout.addWidget(btn_fail)
                self.task_layout.addWidget(t_group)

            self.task_layout.addStretch()

            # 하단 종료 버튼
            btn_finish = QPushButton("테스트 종료 및 데이터 전송")
            btn_finish.setObjectName("PrimaryButton")
            btn_finish.clicked.connect(self._handle_test_finished)
            main_layout.addWidget(btn_finish)

            layout.addWidget(main)
            layout.addWidget(task_panel)
            return self._app_frame(body, "recording")

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

    def _ui_start_task(self, name, order, btn_s, btn_ok, btn_no):
        """UI에서 시작 버튼 클릭 시 호출"""
        self._start_task(name, order) # 이전에 만든 데이터 기록 함수 호출
        btn_s.setEnabled(False)      # 시작 버튼 비활성화
        btn_s.setText("진행 중...")
        btn_ok.setVisible(True)      # 성공 버튼 표시
        btn_no.setVisible(True)      # 실패 버튼 표시

    def _ui_finish_task(self, is_success, btn_ok, btn_no):
        """UI에서 성공/실패 버튼 클릭 시 호출"""
        self._finish_task(is_success) # 이전에 만든 데이터 기록 함수 호출
        btn_ok.setEnabled(False)     # 결과 확정 후 비활성화
        btn_no.setEnabled(False)
        status_text = "✅ 성공" if is_success else "❌ 실패"
        btn_ok.getParent().setTitle(f"Task 완료 ({status_text})")

    def start_test(self) -> None:
        """테스트 시작: 화면 녹화 시작 + 첫 페이지 로그 기록"""
        self.page_logs.clear()
        self.state.task_results.clear()
        self.test_running = True
        self.video_output_path = os.path.abspath("test_video.mp4")
        self.recording_thread = RecordingWorker(self.recorder, self.viewport, self.video_output_path)
        self.recording_thread.start()
        self._load_test_url()

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

        if self.web_view is not None:
            self.web_view.setUrl(QUrl(raw_url))
        elif self.test_running:
            self.record_page_entry(raw_url)

    def _on_browser_url_changed(self, url: QUrl) -> None:
        url_text = url.toString()
        if not url_text:
            return

        self.test_url_input.setText(url_text)
        if self.test_running:
            self.record_page_entry(url_text)

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
            테스트 종료 처리: 마지막 로그 기록 -> 녹화 중지 -> 데이터 업로드 시작
            """
            # 1. 마지막 페이지 로그 종료 시간 기록 (중요!)
            if hasattr(self, 'page_logs') and self.page_logs:
                # 현재 녹화된 시점을 마지막 로그의 종료 시각으로 설정
                current_ts = time.time() - self.recorder.start_time
                self.page_logs[-1]['end_video_ts'] = round(current_ts, 2)

            # 2. 녹화 중지
            if self.recorder.is_recording:
                self.recorder.stop()
                if self.recording_thread and self.recording_thread.isRunning():
                    self.recording_thread.wait()
            
            self.test_running = False
            
            # 3. 화면 전환 (Index 3: 업로드 대기 화면)
            self._show_screen(3)
            self.is_uploading = True
            self.upload_status_label.setText("데이터를 정리하여 서버로 전송 중입니다...")

            # 4. 업로드 워커 실행 (metadata 선전송 -> 영상 후전송 구조)
            metadata = {
                "page_logs": self.page_logs,
                "task_results": self.task_results
            }

            from utils.workers import UploadWorker
            self.upload_worker = UploadWorker(
                api_client=self.api,
                video_path=self.video_output_path,
                metadata=metadata
            )

            # 시그널 연결 (기존에 정의한 _on_upload_progress, _on_upload_finished 사용)
            self.upload_worker.progress.connect(self._on_upload_progress)
            self.upload_worker.finished.connect(self._on_upload_finished)
            self.upload_worker.start()

    def _on_upload_progress(self, message: str, value: Optional[int] = None) -> None:
            """
            업로드 진행 상황 업데이트
            :param message: 표시할 텍스트 메시지
            :param value: 프로그레스 바에 표시할 정수 값 (0~100)
            """
            self.upload_status_label.setText(message)
            
            # value 값이 들어오면 프로그레스 바를 업데이트합니다.
            if value is not None:
                self.p_bar.setValue(value)

    @Slot(bool, str)
    def _on_upload_finished(self, success: bool, message: str) -> None:
        """
        업로드 워커 종료 시 호출되는 슬롯
        :param success: 업로드 성공 여부
        :param message: 실패 사유 또는 성공 메시지
        """
        if success:
            # 성공 시: 분석 폴링 시작
            self.upload_status_label.setText("✅ 업로드 성공! 분석을 시작합니다.")
            self._start_analysis_polling()
        else:
            # 실패 시: 상태 리셋 및 UI 복구
            self.is_uploading = False  # 상태 플래그 해제
            self.p_bar.setValue(0)      # 프로그레스 바 초기화
            
            # [수정] QMessageBox 설정 시 정확한 Enum 경로 사용
            msg_box = QMessageBox(self)
            # QMessageBox.Critical -> QMessageBox.Icon.Critical
            msg_box.setIcon(QMessageBox.Icon.Critical) 
            msg_box.setWindowTitle("업로드 실패")
            msg_box.setText(f"데이터 전송에 실패했습니다.\n사유: {message}")
            
            # QMessageBox.ActionRole -> QMessageBox.ButtonRole.ActionRole
            retry_btn = msg_box.addButton("다시 시도", QMessageBox.ButtonRole.ActionRole)
            # QMessageBox.RejectRole -> QMessageBox.ButtonRole.RejectRole
            cancel_btn = msg_box.addButton("테스트 화면으로 돌아가기", QMessageBox.ButtonRole.RejectRole)
            
            msg_box.exec()
            
            if msg_box.clickedButton() == retry_btn:
                # 다시 시도: 핸들러 재호출 (처음부터 다시 시퀀스 시작)
                self._handle_test_finished()
            else:
                # 복구: 테스트 화면(Index 2)으로 되돌리기
                self.test_running = True # 다시 테스트 가능 상태로 설정
                self._show_screen(2)

    def _start_analysis_polling(self) -> None:
        """분석 상태 감시 시작"""
        from utils.workers import AnalysisStatusWorker
        
        # 기존 워커가 있다면 정리
        if self.analysis_status_worker and self.analysis_status_worker.isRunning():
            self.analysis_status_worker.stop()
            self.analysis_status_worker.wait()

        self.analysis_status_worker = AnalysisStatusWorker(self.api)
        self.analysis_status_worker.status_updated.connect(
            lambda s: self.upload_status_label.setText(f"🧐 AI 분석 중: {s}...")
        )
        self.analysis_status_worker.analysis_finished.connect(self._on_analysis_complete)
        self.analysis_status_worker.start()

    def _on_analysis_complete(self, data: dict) -> None:
        """분석이 성공적으로 완료되었을 때 호출"""
        self.is_uploading = False
        self.upload_status_label.setText("🎉 분석이 완료되었습니다!")
        
        # 요구사항에 따라 Index 4(리포트 화면)로 자동 전환하거나 버튼 활성화
        self._show_screen(4)
        QMessageBox.information(self, "분석 완료", "최종 리포트가 생성되었습니다.")

    def _on_analysis_status_updated(self, status: str) -> None:
        if status == "generating":
            self.p_bar.setValue(90)
        elif status == "failed":
            QMessageBox.critical(self, "분석 실패", "서버 분석 또는 보고서 생성에 실패했습니다.")
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

    def _finish_task(self, is_success: bool) -> None:
        """
        태스크 종료 시 호출: 결과 판단, 소요 시간(duration) 계산 및 저장
        """
        if not hasattr(self, 'current_task_info') or self.current_task_info is None:
            print("⚠️ 진행 중인 태스크가 없습니다.")
            return

        # 1. 소요 시간 계산
        duration = round(time.time() - self.current_task_info["start_time"], 2)
        
        # 2. 결과 데이터 구성 (설계서 핵심 데이터)
        task_entry = {
            "task_order": self.current_task_info["task_order"],
            "task_name": self.current_task_info["task_name"],
            "result": "success" if is_success else "fail",
            "duration_sec": duration,
            "timestamp": time.time()
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
            # 1. 현재 운영체제에서 사용 중인 메인 스크린 객체를 획득합니다.
            screen = QGuiApplication.primaryScreen()
            if not screen:
                print("⚠️ 활성화된 스크린을 찾을 수 없어 캡처를 중단합니다.")
                return

            # 2. 메인 스크린의 전체 화면을 QPixmap 형태로 캡처(Grab)합니다.
            pixmap = screen.grabWindow(0)
            
            # 3. 디스크에 임시 PNG 파일을 쓰지 않기 위해 PySide6의 메모리 버퍼 시스템을 활용합니다.
            byte_array = QByteArray()                # 바이너리 데이터를 담을 바이트 배열 생성
            buffer = QBuffer(byte_array)             # 바이트 배열을 버퍼 장치에 연결
            buffer.open(QIODevice.OpenModeFlag.WriteOnly) # 쓰기 전용 모드로 버퍼를 오픈
            pixmap.save(buffer, "PNG")               # 캡처한 이미지를 PNG 포맷의 바이너리로 버퍼에 기록
            image_bytes = byte_array.data()          # 최종적으로 Python에서 사용할 수 있는 bytes 형태로 변환

            # 4. 고유한 작업을 식별하기 위해 현재까지 기록된 페이지 로그의 길이를 임시 ID로 설정합니다.
            log_id = str(len(self.page_logs))

            # 5. UI 메인 스레드가 멈추지 않도록 비동기 스레드인 ScreenshotUploadWorker를 생성합니다.
            worker = ScreenshotUploadWorker(self.api, image_bytes, log_id)
            
            # 6. 파이썬의 가비지 컬렉터(GC)에 의해 워커 객체가 도중에 소멸되는 것을 방지하기 위해 리스트에 참조를 유지합니다.
            if not hasattr(self, 'screenshot_workers'):
                self.screenshot_workers = []
            self.screenshot_workers.append(worker)

            # 7. 워커의 작업 완료 시그널과 결과 처리 슬롯(함수)을 연결합니다.
            # 람다 식을 활용하여 현재 어떤 로그 엔트리를 업데이트해야 하는지 참조를 함께 전달합니다.
            worker.finished.connect(
                lambda success, path, lid: self._on_screenshot_upload_finished(
                    worker, success, path, lid, log_entry
                )
            )
            
            # 8. 백그라운드 스레드 작업을 시작합니다.
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
            # 업로드 성공 시: 비어있던 screenshot_path 키에 서버가 반환해준 저장 경로를 채웁니다.
            log_entry["screenshot_path"] = result
            print(f"📸 [스크린샷] 서버 업로드 성공 및 경로 저장 완료: {result}")
        else:
            # 업로드 실패 시: 로그 기록을 보존하되 에러 내용을 콘솔에 출력합니다.
            print(f"❌ [스크린샷] 업로드 실패 사유: {result} (로그 ID: {log_id})")
        
        # 사용이 완료된 워커 객체는 메모리 누수 방지를 위해 관리 리스트에서 제거합니다.
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
            """앱 종료 시 데이터 유실 방지를 위한 리소스 정리 및 경고"""
            
            # 1. 업로드 중일 때 경고 (v6 요구사항 반영)
            if self.is_uploading:
                answer = QMessageBox.warning(
                    self,
                    "작업 진행 중",
                    "데이터 업로드 또는 AI 분석이 진행 중입니다.\n지금 종료하면 결과 리포트를 확인할 수 없습니다. 정말 종료하시겠습니까?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if answer == QMessageBox.StandardButton.No:
                    event.ignore()
                    return
                
                # 사용자가 종료를 선택한 경우 서버에 즉시 삭제 요청
                self.api.abort_session()

            # 2. 실행 중인 워커(Worker)들 안전하게 정리
            
            # 녹화 중인 경우 안전하게 중지
            if hasattr(self, 'test_running') and self.test_running:
                if self.recorder.is_recording:
                    self.recorder.stop()
                if self.recording_thread and self.recording_thread.isRunning():
                    self.recording_thread.wait(3000)

            # 업로드 워커 정리
            if self.upload_worker and self.upload_worker.isRunning():
                # 업로드는 강제 종료 시 서버 데이터가 꼬일 수 있으므로 주의가 필요합니다.
                self.upload_worker.terminate() 
                self.upload_worker.wait(1000)

            # 분석 상태 폴링 워커 정리
            if self.analysis_status_worker and self.analysis_status_worker.isRunning():
                self.analysis_status_worker.stop() # stop()은 우리가 workers.py에서 정의한 메서드입니다.
                self.analysis_status_worker.wait(2000)

            # 캘리브레이션 관련 워커들 정리 (기존 FIX 반영)
            for worker_name in ["calib_worker", "calib_status_worker"]:
                worker = getattr(self, worker_name, None)
                if worker and worker.isRunning():
                    if hasattr(worker, 'stop'):
                        worker.stop()
                    worker.wait(2000)

            event.accept()