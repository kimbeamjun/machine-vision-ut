import os
import sys
from typing import Any, Dict, List

# 루트 경로
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(CURRENT_DIR)
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from PySide6.QtCore import QThread, Signal, Slot
from core.recorder import ScreenRecorder


try:
    from core.api_client import ApiClient
except ImportError:
    from ..core.api_client import ApiClient

class UploadWorker(QThread):
    """ 영상 및 데이터를 백그라운드에서 업로드하는 일꾼 """
    finished = Signal(bool, str)  # (성공여부, 메시지)
    progress = Signal(str)        # 현재 진행 상태 메시지

    def __init__(self, api_client: ApiClient, session_id: int, video_path: str, metadata: Dict[str, Any]):
        super().__init__()
        self.api = api_client
        self.session_id = session_id
        self.video_path = video_path
        self.metadata = metadata

    def run(self):
        try:
            # 메타데이터 전송
            self.progress.emit("메타데이터 전송 중...")
            self.api.send_metadata(
                self.session_id,
                self.metadata['calibrations'],
                self.metadata['page_logs'],
                self.metadata['task_results']
            )

            # 영상 업로드를 위한 Presigned URL 요청
            self.progress.emit("영상 업로드 준비 중...")
            url_data = self.api.request_presigned_url(self.session_id, "video")
            presigned_url = url_data.get("url")

            if not presigned_url:
                self.finished.emit(False, "업로드 URL을 가져오지 못했습니다.")
                return

            # 실제 영상 파일 업로드 (시간이 오래 걸리는 작업)
            self.progress.emit("영상 파일 업로드 중 (이 작업은 수 분이 소요될 수 있습니다)...")
            success = self.api.upload_file(presigned_url, self.video_path)

            if success:
                # 분석 시작 요청
                self.api.request_analysis(self.session_id)
                self.finished.emit(True, "모든 데이터 업로드 및 분석 요청 완료")
            else:
                self.finished.emit(False, "영상 업로드에 실패했습니다.")

        except Exception as e:
            self.finished.emit(False, f"오류 발생: {str(e)}")

class AnalysisStatusWorker(QThread):
    """ 서버의 분석 완료 여부를 주기적으로 체크(Polling)하는 일꾼 """
    status_updated = Signal(str)
    analysis_finished = Signal(dict)

    def __init__(self, api_client: ApiClient, session_id: int):
        super().__init__()
        self.api = api_client
        self.session_id = session_id
        self.is_running = True

    def run(self):
        import time
        while self.is_running:
            try:
                response = self.api.report_status(self.session_id)
                data = response.json()
                
                # 서버에서 정의한 상태값에 따라 처리
                status = data.get("status", "unknown")
                self.status_updated.emit(f"분석 상태: {status}")

                if status == "completed":
                    self.analysis_finished.emit(data)
                    break
                
                time.sleep(5)  # 5초 간격으로 확인
            except Exception as e:
                print(f"상태 체크 중 오류: {e}")
                time.sleep(10)

    def stop(self):
        self.is_running = False
        
class RecordingWorker(QThread):
    """ UI 멈춤 방지를 위해 녹화를 백그라운드에서 실행하는 스레드 """
    def __init__(self, recorder: ScreenRecorder, region: dict, output_path: str):
        super().__init__()
        self.recorder = recorder
        self.region = region
        self.output_path = output_path

    def run(self):
        # recorder.py의 start 함수 실행
        self.recorder.start(self.region, self.output_path)