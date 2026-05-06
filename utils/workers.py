import os
import sys
import time
from typing import Any, Dict, List

# 루트 경로 설정
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(CURRENT_DIR)
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from PySide6.QtCore import QThread, Signal, Slot
from core.recorder import ScreenRecorder
from core.api_client import ApiClient

class RecordingWorker(QThread):
    """ UI 멈춤 방지를 위해 녹화를 백그라운드에서 실행하는 스레드 """
    def __init__(self, recorder: ScreenRecorder, region: dict, output_path: str):
        super().__init__()
        self.recorder = recorder
        self.region = region
        self.output_path = output_path

    def run(self):
        # 모든 좌표는 비율(0.0~1.0)로 전달되어야 함
        self.recorder.start(self.region, self.output_path)

class UploadWorker(QThread):
    """ 
    메타데이터 전송 및 최종 영상을 업로드하는 일꾼
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
            # 1. 메타데이터 전송
            self.progress.emit("메타데이터 전송 중...")
            self.api.send_metadata(
                page_logs=self.metadata['page_logs'],
                task_results=self.metadata['task_results']
            )

            # 2. 영상 업로드를 위한 Presigned URL 요청
            self.progress.emit("영상 업로드 준비 중...")
            url_data = self.api.request_presigned_url(file_type="video")
            
            # 타입 안전성 확보: get() 결과가 None일 수 있으므로 명시적 체크
            presigned_url = url_data.get("presigned_url")
            object_key = url_data.get("object_key")

            if not presigned_url or not isinstance(object_key, str):
                self.finished.emit(False, "서버로부터 유효한 업로드 경로를 받지 못했습니다.")
                return

            # 3. 실제 영상 파일 업로드
            self.progress.emit("영상 파일 업로드 중...")
            success = self.api.upload_file(presigned_url, self.video_path)

            if success:
                # 4. 분석 시작 요청
                self.api.request_analysis(video_object_key=object_key)
                self.finished.emit(True, "모든 데이터 업로드 및 분석 요청 완료")
            else:
                self.finished.emit(False, "영상 업로드에 실패했습니다.")

        except Exception as e:
            self.finished.emit(False, f"오류 발생: {str(e)}")

class CalibrationWorker(QThread):
    """ 
    캘리브레이션 영상들을 업로드하고 AI 등록을 처리하는 일꾼
    """
    finished = Signal(bool, str)
    progress = Signal(str)

    def __init__(self, api_client: ApiClient, points_to_upload: List[Dict[str, Any]]):
        super().__init__()
        self.api = api_client
        self.points_to_upload = points_to_upload # [{"point_no": 1, "path": "...", "x": 0.1, "y": 0.1}]

    def run(self):
        try:
            uploaded_points = []
            for pt in self.points_to_upload:
                self.progress.emit(f"포인트 {pt['point_no']} 영상 업로드 중...")
                
                # Presigned URL 발급 및 파일 업로드
                url_res = self.api.request_presigned_url(file_type="video")
                if self.api.upload_file(url_res['presigned_url'], pt['path']):
                    uploaded_points.append({
                        "point_no": pt['point_no'],
                        "screen_x": pt['x'], # 비율 좌표[cite: 4]
                        "screen_y": pt['y'],
                        "video_object_key": url_res['object_key']
                    })
            
            # 5개 포인트 등록 API 호출
            self.api.register_calibration(uploaded_points)
            self.finished.emit(True, "캘리브레이션 등록 완료")
            
        except Exception as e:
            self.finished.emit(False, f"캘리브레이션 오류: {str(e)}")

class AnalysisStatusWorker(QThread):
    """ 
    서버의 분석 완료 여부를 주기적으로 체크(Polling)하는 일꾼
    """
    status_updated = Signal(str)
    analysis_finished = Signal(dict)

    def __init__(self, api_client: ApiClient):
        super().__init__()
        self.api = api_client
        self.is_running = True

    def run(self):
        while self.is_running:
            try:
                # check_calibration_status 또는 별도 리포트 API 사용
                # 여기서는 분석 결과 상태를 확인하는 용도로 사용
                data = self.api.check_calibration_status() 
                
                status = data.get("status", "unknown")
                self.status_updated.emit(f"현재 상태: {status}")

                if status == "done": # 완료 상태는 "done"
                    self.analysis_finished.emit(data)
                    break
                elif status == "failed":
                    self.status_updated.emit("분석 실패")
                    break
                
                time.sleep(2)  # 명세서 권장 폴링 간격: 1~2초
            except Exception as e:
                print(f"상태 체크 중 오류: {e}")
                time.sleep(5)

    def stop(self):
        self.is_running = False