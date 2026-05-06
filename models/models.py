import time
from dataclasses import dataclass, field
from typing import List, Dict, Any

@dataclass
class ViewportRegion:
    """녹화 범위 좌표 (비율 0.0 ~ 1.0)"""
    x: float = 0.0
    y: float = 0.0
    w: float = 1.0
    h: float = 1.0

    def as_payload(self) -> Dict[str, float]:
        return {"x": self.x, "y": self.y, "w": self.w, "h": self.h}

@dataclass
class PageLog:
    """ 페이지 체류 기록 체류 시간(stay_sec)은 서버에서 계산 가능하므로, 정확한 매칭을 위해 영상 기준 절대 시각을 기록합니다. """
    page_no: int
    url: str
    start_video_ts: float  # 녹화 시작(0.0) 대비 진입 시각 (초)
    end_video_ts: float = 0.0 # 이탈 시각 (초)
    screenshot_path: str = "" # MinIO 업로드 후 저장될 경로

@dataclass
class ClientState:
    """클라이언트의 전체 상태 관리"""
    server_url: str = "http://localhost:8000"
    session_id: int | None = None
    viewport_region: ViewportRegion = field(default_factory=ViewportRegion)
    calibrations: List[Dict[str, Any]] = field(default_factory=list)
    page_logs: List[PageLog] = field(default_factory=list)
    task_results: List[Dict[str, Any]] = field(default_factory=list)
    
    # 절대 타임스탬프 동기화의 기준점 (Unix Timestamp)
    recording_start_time: float = 0.0

    def get_video_timestamp(self) -> float:
        """
        [이슈 3] 현재 시점이 녹화 시작 후 몇 초가 지났는지 반환.
        이 값이 gaze_points의 timestamp와 100% 매칭되는 기준이 됩니다.
        """
        if self.recording_start_time == 0:
            return 0.0
        return time.time() - self.recording_start_time