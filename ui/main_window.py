import sys
import os
import time
from typing import Any, Dict, List, cast, Optional

# 폴더 구조 확인 및 루트 경로
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.abspath(os.path.join(CURRENT_DIR, ".."))

if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from PySide6.QtCore import Qt, QThreadPool, Slot, QTimer
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QMainWindow, QPushButton, QProgressBar, QSizePolicy,
    QStackedWidget, QVBoxLayout, QWidget, QApplication, QMessageBox
)

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
    CalibrationStatusWorker, AnalysisStatusWorker,
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

        # 3. 설정 및 스레드 풀 객체 생성
        self.state = ClientState()
        self.thread_pool = QThreadPool.globalInstance()

        # 4. API 클라이언트 초기화
        config = ApiConfig(base_url=self.state.server_url)
        self.api = ApiClient(config)

        # 5. UI 전체 설정
        self.setWindowTitle("UT Automation Client")
        self.resize(1160, 720)
        self.setStyleSheet(APP_QSS)

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
        side_layout.addWidget(QLineEdit(self.state.server_url))
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
        기존 start_test_process()에서 session_id를 재생성하던 로직을 제거하고
        _handle_create_session()에서 이미 생성된 session_id를 그대로 사용합니다.
        """
        if self.state.session_id is None:
            QMessageBox.warning(self, "세션 없음", "먼저 '녹화 범위 설정' 화면에서 세션을 생성해주세요.")
            self._show_screen(0)
            return

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
        """[화면 2] 시선 추적 테스트 진행 화면"""
        body = QWidget()
        layout = QHBoxLayout(body)
        main = QWidget()
        main_layout = QVBoxLayout(main)
        main_layout.addWidget(QLineEdit("https://demo-test.com"))

        browser_area = QFrame()
        browser_area.setStyleSheet("background:#1e2433; border: 2px dashed #30363d; border-radius:8px;")
        browser_area.setMinimumHeight(350)
        main_layout.addWidget(browser_area)

        task_panel = QFrame()
        task_panel.setFixedWidth(250)
        task_layout = QVBoxLayout(task_panel)
        task_layout.addWidget(section_label("현재 태스크"))
        for t in ["메인 로고 클릭", "검색바 입력", "상세 페이지 이동"]:
            task_layout.addWidget(QLabel(f"- {t}"))
        task_layout.addStretch()

        btn_finish = QPushButton("테스트 종료 및 데이터 전송")
        btn_finish.setObjectName("PrimaryButton")
        btn_finish.clicked.connect(self._handle_test_finished)
        main_layout.addWidget(btn_finish)

        layout.addWidget(main)
        layout.addWidget(task_panel)
        return self._app_frame(body, "recording")

    def start_test(self) -> None:
        """테스트 시작: 화면 녹화 시작 + 첫 페이지 로그 기록"""
        self.video_output_path = os.path.abspath("test_video.mp4")
        self.recording_thread = RecordingWorker(self.recorder, self.viewport, self.video_output_path)
        self.recording_thread.start()
        self.record_page_entry("https://start.url")

    def record_page_entry(self, url: str) -> None:
        """페이지 진입 시 로그 기록 (절대 타임스탬프 기준)"""
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

    # ──────────────────────────────────────────────
    # 화면 3: 업로드/분석 대기
    # ──────────────────────────────────────────────

    def _build_upload_screen(self) -> QWidget:
        """[화면 3] 데이터 전송 및 분석 대기 화면"""
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.addStretch()
        layout.addWidget(QLabel("AI 서버 분석 중... 잠시만 기다려 주세요."), 0, Qt.AlignmentFlag.AlignCenter)
        self.p_bar = QProgressBar()
        self.p_bar.setFixedWidth(400)
        layout.addWidget(self.p_bar, 0, Qt.AlignmentFlag.AlignCenter)
        layout.addStretch()
        return self._app_frame(body, "upload")

    def _handle_test_finished(self) -> None:
        """테스트 종료 후 녹화 중지 및 업로드 시작"""
        if self.recording_thread and self.recording_thread.isRunning():
            self.recorder.stop()
            self.recording_thread.wait(5000)

        final_ts = self.recorder.get_elapsed_time()
        if self.page_logs:
            self.page_logs[-1]["end_video_ts"] = final_ts

        if not self.state.task_results:
            self.state.task_results.append(
                {
                    "task_order": 1,
                    "result": "completed",
                    "duration_sec": final_ts,
                }
            )

        self._show_screen(3)
        self.p_bar.setValue(25)

        metadata = {
            "page_logs": self.page_logs,
            "task_results": self.state.task_results,
        }
        self.is_uploading = True
        self.upload_worker = UploadWorker(self.api, self.video_output_path, metadata)
        self.upload_worker.progress.connect(lambda msg: print(f"[업로드] {msg}"))
        self.upload_worker.finished.connect(self._on_upload_finished)
        self.upload_worker.start()

    def _on_upload_finished(self, success: bool, msg: str) -> None:
        """업로드 결과에 따른 화면 전환"""
        self.is_uploading = False
        if success:
            self.p_bar.setValue(80)
            self.analysis_status_worker = AnalysisStatusWorker(self.api)
            self.analysis_status_worker.status_updated.connect(self._on_analysis_status_updated)
            self.analysis_status_worker.analysis_finished.connect(self._on_analysis_finished)
            self.analysis_status_worker.start()
        else:
            QMessageBox.critical(self, "오류", f"업로드 실패: {msg}")

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
        if self.is_uploading:
            answer = QMessageBox.warning(
                self,
                "업로드 진행 중",
                "영상 업로드가 진행 중입니다. 지금 종료하면 테스트 데이터가 누락될 수 있습니다.",
                QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer == QMessageBox.StandardButton.Cancel:
                event.ignore()
                return

        if self.recording_thread and self.recording_thread.isRunning():
            self.recorder.stop()
            self.recording_thread.wait(3000)
        if self.upload_worker and self.upload_worker.isRunning():
            self.upload_worker.terminate()
        if self.analysis_status_worker and self.analysis_status_worker.isRunning():
            self.analysis_status_worker.stop()
            self.analysis_status_worker.wait(1000)

        event.accept()
