import os
import sys
import time
from typing import Any, Dict, List

# 루트 경로 설정
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(CURRENT_DIR)
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from PySide6.QtCore import QThread, Signal
from core.recorder import ScreenRecorder
from core.api_client import ApiClient


class RecordingWorker(QThread):
    """UI 멈춤 방지를 위해 녹화를 백그라운드에서 실행하는 스레드"""
    def __init__(self, recorder: ScreenRecorder, region: dict, output_path: str):
        super().__init__()
        self.recorder = recorder
        self.region = region
        self.output_path = output_path

    def run(self):
        # 모든 좌표는 비율(0.0~1.0)로 전달 — recorder 내부에서 픽셀 변환
        self.recorder.start(self.region, self.output_path)


class UploadWorker(QThread):
    """
    메타데이터 전송 및 최종 영상을 업로드하는 일꾼.
    분석 트리거는 MinIO 업로드 완료 시 서버 웹훅이 자동 처리합니다. (명세서 4단계)
    """
    finished = Signal(bool, str)  # (성공여부, 메시지)
    progress = Signal(str)        # 현재 진행 상태 메시지

    def __init__(self, api_client: ApiClient, video_path: str, metadata: Dict[str, Any]):
        super().__init__()
        self.api = api_client
        self.video_path = video_path
        self.metadata = metadata

    def run(self):
        try:
            # 1. 메타데이터 전송 (명세서 D 항목)
            self.progress.emit("메타데이터 전송 중...")
            self.api.send_metadata(
                page_logs=self.metadata['page_logs'],
                task_results=self.metadata['task_results'],
            )

            # 2. 영상 업로드를 위한 Presigned URL 요청 (명세서 1단계, file_type='recording')
            self.progress.emit("영상 업로드 준비 중...")
            url_data = self.api.request_presigned_url(file_type="recording")

            presigned_url = url_data.get("presigned_url")
            object_key = url_data.get("object_key")

            if not presigned_url or not isinstance(object_key, str):
                self.finished.emit(False, "서버로부터 유효한 업로드 경로를 받지 못했습니다.")
                return

            # 3. 실제 영상 파일 업로드 (명세서 3단계)
            #    완료 시 MinIO가 웹훅으로 분석을 자동 트리거 (명세서 4단계)
            self.progress.emit("영상 파일 업로드 중...")
            success = self.api.upload_file(presigned_url, self.video_path)

            if success:
                self.finished.emit(True, "영상 업로드 완료 — 서버에서 분석을 시작합니다.")
            else:
                self.finished.emit(False, "영상 업로드에 실패했습니다.")

        except Exception as e:
            self.finished.emit(False, f"오류 발생: {str(e)}")


class CalibrationWorker(QThread):
    """캘리브레이션 영상들을 업로드하고 AI 등록을 처리하는 일꾼"""
    finished = Signal(bool, str)
    progress = Signal(str)

    def __init__(self, api_client: ApiClient, points_to_upload: List[Dict[str, Any]]):
        super().__init__()
        self.api = api_client
        # 기대 형식: [{"point_no": 1, "x": 0.1, "y": 0.1, "path": "..."}]
        self.points_to_upload = points_to_upload

    def run(self):
        try:
            uploaded_points = []
            for pt in self.points_to_upload:
                self.progress.emit(f"포인트 {pt['point_no']} 영상 업로드 중...")

                # file_type='calibration', point_no 전달 (명세서 1단계)
                url_res = self.api.request_presigned_url(
                    file_type="calibration",
                    point_no=pt['point_no'],
                )
                if self.api.upload_file(url_res['presigned_url'], pt['path']):
                    uploaded_points.append({
                        "point_no": pt['point_no'],
                        "screen_x": pt['x'],  # 비율 좌표
                        "screen_y": pt['y'],
                        "video_object_key": url_res['object_key'],
                    })

            # 5개 포인트 등록 API 호출 (명세서 B 항목)
            self.api.register_calibration(uploaded_points)
            self.finished.emit(True, "캘리브레이션 등록 완료")

        except Exception as e:
            self.finished.emit(False, f"캘리브레이션 오류: {str(e)}")


class CalibrationStatusWorker(QThread):
    """
    [추가] 캘리브레이션 AI 분석 완료 여부를 주기적으로 폴링하는 일꾼.
    (명세서 C 항목: GET /api/v1/sessions/{id}/calibrate/status)

    캘리브레이션 완료(status=done) → 본 녹화 시작 가능.
    실패(status=failed) → failed_points 참조하여 해당 포인트만 재촬영 유도.
    """
    status_updated = Signal(str)           # 현재 상태 문자열 ("analyzing" | "done" | "failed")
    calibration_done = Signal(dict)        # done 시 전체 응답 dict 전달
    calibration_failed = Signal(list)      # failed 시 failed_points 리스트 전달

    def __init__(self, api_client: ApiClient):
        super().__init__()
        self.api = api_client
        self.is_running = True

    def run(self):
        while self.is_running:
            try:
                data = self.api.check_calibration_status()  # GET /calibrate/status
                status = data.get("status", "unknown")
                self.status_updated.emit(status)  # 순수 status 문자열만 emit

                if status == "done":
                    self.calibration_done.emit(data)
                    break
                elif status == "failed":
                    failed_points = data.get("failed_points", [])
                    self.calibration_failed.emit(failed_points)
                    break
                # status == "analyzing" → 계속 폴링

                time.sleep(2)  # 명세서 권장 폴링 간격: 1~2초
            except Exception as e:
                print(f"캘리브레이션 상태 체크 중 오류: {e}")
                time.sleep(5)

    def stop(self):
        self.is_running = False


class AnalysisStatusWorker(QThread):
    """
    본녹화 분석 완료 후 리포트(PDF) 생성 상태를 주기적으로 폴링하는 일꾼.
    (명세서 12~13단계: GET /api/v1/sessions/{id}/report)

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

                time.sleep(2)  # 명세서 권장 폴링 간격: 1~2초
            except Exception as e:
                print(f"리포트 상태 체크 중 오류: {e}")
                time.sleep(5)

    def stop(self):
        self.is_running = False