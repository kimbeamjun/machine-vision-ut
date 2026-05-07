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
                if not os.path.exists(pt["path"]):
                    self.finished.emit(False, f"캘리브레이션 파일을 찾을 수 없습니다: {pt['path']}")
                    return

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
                else:
                    self.finished.emit(False, f"캘리브레이션 {pt['point_no']}번 영상 업로드 실패")
                    return

            # 5개 포인트 등록 API 호출
            self.api.register_calibration(uploaded_points)
            self.finished.emit(True, "캘리브레이션 등록 완료")

        except Exception as e:
            self.finished.emit(False, f"캘리브레이션 오류: {str(e)}")


class CalibrationStatusWorker(QThread):
    """
    캘리브레이션 AI 분석 완료 여부를 주기적으로 폴링하는 일꾼.

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
        
class ScreenshotUploadWorker(QThread):
    """
    페이지 이동 시 스크린샷을 캡처하여 서버에 업로드하고 경로를 반환하는 일꾼
    """
    finished = Signal(bool, str, str)  # (성공여부, 스크린샷_경로/메시지, 로그_ID)

    def __init__(self, api_client, image_data: bytes, log_id: str):
        super().__init__()
        self.api = api_client
        self.image_data = image_data # 메모리에 있는 이미지 바이트 데이터
        self.log_id = log_id

    def run(self):
        try:
            # 1. Presigned URL 요청
            url_data = self.api.request_presigned_url(file_type="screenshot")
            presigned_url = url_data.get("presigned_url")
            object_key = url_data.get("object_key") # 서버에서 생성된 저장 경로

            if not presigned_url or not object_key:
                self.finished.emit(False, "URL 요청 실패", self.log_id)
                return

            # 2. 업로드 (파일 저장 없이 바이너리로 바로 전송)
            # ApiClient의 upload_file을 조금 수정하거나 requests.put을 직접 사용
            import requests
            response = requests.put(presigned_url, data=self.image_data, timeout=10)
            
            if response.status_code in (200, 201, 204):
                self.finished.emit(True, object_key, self.log_id)
            else:
                self.finished.emit(False, "업로드 실패", self.log_id)

        except Exception as e:
            self.finished.emit(False, str(e), self.log_id)