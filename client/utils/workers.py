import os
import sys
import time
from typing import Any, Dict, List
import cv2

# 루트 경로 설정
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(CURRENT_DIR)

if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QImage
from core.recorder import ScreenRecorder
from core.api_client import ApiClient


class CameraPreviewWorker(QThread):
    """OpenCV로 웹캠 프레임을 읽어 QLabel에 표시할 QImage를 전달하는 스레드."""
    frame_ready = Signal(QImage)
    error = Signal(str)

    def __init__(self, camera_index: int = 0, width: int = 260, height: int = 180, fps: int = 20):
        super().__init__()
        self.camera_index = camera_index
        self.width = width
        self.height = height
        self.fps = fps
        self._running = False

    def run(self):
        self._running = True
        cap = cv2.VideoCapture(self.camera_index, cv2.CAP_DSHOW)
        if not cap.isOpened():
            cap = cv2.VideoCapture(self.camera_index)

        if not cap.isOpened():
            self.error.emit("웹캠을 열 수 없습니다.")
            return

        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        cap.set(cv2.CAP_PROP_FPS, self.fps)

        frame_interval = 1.0 / max(1, self.fps)
        try:
            while self._running:
                started = time.perf_counter()
                ret, frame = cap.read()
                if ret:
                    frame = cv2.resize(frame, (self.width, self.height))
                    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    bytes_per_line = rgb.shape[1] * rgb.shape[2]
                    image = QImage(
                        rgb.data,
                        rgb.shape[1],
                        rgb.shape[0],
                        bytes_per_line,
                        QImage.Format.Format_RGB888,
                    ).copy()
                    self.frame_ready.emit(image)

                elapsed = time.perf_counter() - started
                sleep_time = frame_interval - elapsed
                if sleep_time > 0:
                    time.sleep(sleep_time)
        finally:
            cap.release()

    def stop(self):
        self._running = False


class RecordingWorker(QThread):
    """UI 멈춤 방지를 위해 녹화를 백그라운드에서 실행하는 스레드"""
    error = Signal(str)

    def __init__(self, recorder: ScreenRecorder, pixel_region: dict, output_path: str):
        super().__init__()
        self.recorder = recorder
        self.pixel_region = pixel_region # 보정된 물리 픽셀 데이터
        self.output_path = output_path

    def run(self):
        try:
            self.recorder.start(self.pixel_region, self.output_path)
        except Exception as exc:
            self.error.emit(str(exc))
        


class UploadWorker(QThread):
    """
    1. 메타데이터 전송
    2. Presigned URL 요청
    3. MinIO 업로드 (재시도 포함)
    4. 분석 트리거
    """
    finished = Signal(bool, str)  # (성공여부, 메시지)
    progress = Signal(str, int)   # (메시지, 퍼센트)

    def __init__(self, api_client, video_path: str, metadata: Dict[str, Any], max_retries: int = 3):
        super().__init__()
        self.api = api_client
        self.video_path = video_path
        self.metadata = metadata
        self.max_retries = max_retries

    def run(self):
        try:
            # 메타데이터 전송 (10%)
            self.progress.emit("메타데이터 전송 중...", 10)
            self.api.send_metadata(
                page_logs=self.metadata.get('page_logs', []),
                task_results=self.metadata.get('task_results', [])
            )

            # URL 요청 및 업로드 통합 재시도 루프
            upload_success = False
            for attempt in range(1, self.max_retries + 1):
                try:
                    self.progress.emit(f"업로드 준비 중... (시도 {attempt}/{self.max_retries})", 20 + (attempt * 5))
                    url_data = self.api.request_presigned_url(file_type="recording")
                    presigned_url = url_data.get("presigned_url")

                    if presigned_url and self.api.upload_file(presigned_url, self.video_path):
                        upload_success = True
                        break
                except Exception as e:
                    print(f"시도 {attempt} 실패: {e}")

                time.sleep(2)

            if not upload_success:
                self.finished.emit(False, f"영상 업로드에 {self.max_retries}회 실패했습니다. 네트워크 상태를 확인하세요.")
                return

            # 분석 시작 트리거 (90%)
            self.progress.emit("AI 분석 작업 요청 중...", 90)
            self.api.start_analysis()

            self.progress.emit("모든 데이터 전송 완료!", 100)
            self.finished.emit(True, "성공")

        except Exception as e:
            self.finished.emit(False, f"시스템 오류 발생: {str(e)}")

class CalibrationWorker(QThread):
    """
    5개 포인트 영상을 순차 업로드한 뒤 CL-4 (/calibrate/start) 를 호출한다.
    동기 처리(최대 120초)이므로 응답 자체에 최종 결과가 담긴다.
    """
    finished = Signal(bool, str)          # 업로드/시스템 오류 시 사용 (success=False)
    progress = Signal(str)
    calibration_done = Signal(dict)       # CL-4 status="success" 시 전체 응답 전달
    calibration_failed = Signal(list)     # CL-4 status="failed" 시 failed_points 전달

    def __init__(self, api_client, points_to_upload: list):
        super().__init__()
        self.api = api_client
        self.points_to_upload = points_to_upload

    def run(self):
        try:
            for pt in self.points_to_upload:
                if not os.path.exists(pt["path"]):
                    self.finished.emit(False, f"파일을 찾을 수 없습니다: {pt['path']}")
                    return

                p_no = pt.get('point_no')
                sx = pt.get('screen_x', pt.get('x', 0.0))
                sy = pt.get('screen_y', pt.get('y', 0.0))

                self.progress.emit(f"포인트 {p_no} Presigned URL 요청 중...")

                # Presigned URL 발급
                url_res = self.api.request_presigned_url(
                    file_type="calibration",
                    point_no=p_no,
                    screen_x=sx,
                    screen_y=sy,
                )

                presigned_url = url_res.get('presigned_url')
                if not presigned_url:
                    self.finished.emit(False, f"포인트 {p_no}: Presigned URL 발급 실패")
                    return

                # 영상 업로드
                self.progress.emit(f"포인트 {p_no} 영상 업로드 중...")
                if not self.api.upload_file(presigned_url, pt['path']):
                    self.finished.emit(False, f"포인트 {p_no} 영상 업로드 실패")
                    return

            # 분석 시작 요청 — 동기 대기 (최대 120초, 타임아웃 150초)
            self.progress.emit("AI 캘리브레이션 분석 중... (최대 120초 소요)")
            result = self.api.register_calibration(self.points_to_upload)
            status = result.get("status", "error")

            if status == "success":
                # failed_points가 1개 이하면 서버가 success로 처리
                self.calibration_done.emit(result)
            elif status == "failed":
                # failed_points >= 2 → 재촬영 필요
                failed_pts = result.get("failed_points", [])
                self.calibration_failed.emit(failed_pts)
            else:
                # status == "error" 또는 알 수 없는 상태
                self.finished.emit(False, f"AI 서버 오류 (status={status})")

        except Exception as e:
            self.finished.emit(False, f"캘리브레이션 처리 중 오류: {str(e)}")


class CalibrationStatusWorker(QThread):
    """
    캘리브레이션 분석 상태 폴링
    서버가 'done' 혹은 'failed'를 줄 때까지 2초 간격으로 확인합니다.
    """
    status_updated = Signal(str)
    calibration_done = Signal(dict)
    calibration_failed = Signal(list)

    def __init__(self, api_client):
        super().__init__()
        self.api = api_client
        self.is_running = True

    def run(self):
        while self.is_running:
            try:
                # GET /api/v1/sessions/{id}/calibrate/status 호출
                data = self.api.check_calibration_status()
                status = data.get("status", "unknown")
                self.status_updated.emit(status)

                if status == "done":
                    self.calibration_done.emit(data)
                    break
                elif status == "failed":
                    # 실패한 포인트 번호 리스트 전달
                    failed_pts = data.get("failed_points", [])
                    self.calibration_failed.emit(failed_pts)
                    break

                time.sleep(2) # 폴링 간격
            except Exception as e:
                print(f"상태 체크 중 오류: {e}")
                time.sleep(5)

    def stop(self):
        self.is_running = False


class AnalysisStatusWorker(QThread):
    """
    본녹화 분석 완료 후 리포트(PDF) 생성 상태를 주기적으로 폴링하는 일꾼.

    CalibrationStatusWorker와 혼용 금지 — 각각 별도 엔드포인트 사용.
    """
    status_updated = Signal(str)    # 현재 상태 문자열 ("generating" | "done" | "failed")
    analysis_finished = Signal(dict)  # done 시 {'status': 'done', 'pdf_url': ..., 'pdf_presigned_url': ...}

    def __init__(self, api_client: ApiClient):
        super().__init__()
        self.api = api_client
        self.is_running = True

    def run(self):
        while self.is_running:
            try:
                data = self.api.get_report_status()  # GET /sessions/{id}/report
                status = data.get("status", "unknown")
                self.status_updated.emit(status)  # 순수 status 문자열만 emit

                if status == "done":
                    self.analysis_finished.emit(data)  # pdf_url, pdf_presigned_url 포함
                    break
                elif status == "failed":
                    self.status_updated.emit("failed")
                    break
                # status == "generating" → 계속 폴링

                time.sleep(2)  # 폴링 간격: 1~2초
            except Exception as e:
                print(f"리포트 상태 체크 중 오류: {e}")
                time.sleep(5)

    def stop(self):
        self.is_running = False
        
class ScreenshotUploadWorker(QThread):
    """
    페이지 이동 시 캡처한 스크린샷 바이트를 MinIO에 비동기 업로드.
    api_client.upload_bytes()를 사용해 localhost 치환 등 공통 처리를 공유한다.
    """
    finished = Signal(bool, str, str)  # (성공여부, object_key 또는 에러메시지, log_id)

    def __init__(self, api_client, image_data: bytes, log_id: str, page_no: int):
        super().__init__()
        self.api = api_client
        self.image_data = image_data
        self.log_id = log_id
        self.page_no = page_no

    def run(self):
        try:
            # file_type="screenshot" + page_no로 페이지별 Presigned URL 발급
            url_data = self.api.request_presigned_url(file_type="screenshot", page_no=self.page_no)
            presigned_url = url_data.get("presigned_url")
            object_key = url_data.get("object_key", "")

            if not presigned_url:
                self.finished.emit(False, "Presigned URL 발급 실패", self.log_id)
                return

            # raw requests → api.upload_bytes() 사용 (localhost 치환 포함)
            success = self.api.upload_bytes(presigned_url, self.image_data, content_type="image/png")

            if success:
                self.finished.emit(True, object_key, self.log_id)
            else:
                self.finished.emit(False, "업로드 실패", self.log_id)

        except Exception as e:
            self.finished.emit(False, str(e), self.log_id)
