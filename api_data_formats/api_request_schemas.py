from pydantic import BaseModel, Field
from typing import List, Literal, Optional

class ViewportRegion(BaseModel):
    x: float = Field(..., ge=0.0, le=1.0)
    y: float = Field(..., ge=0.0, le=1.0)
    w: float = Field(..., ge=0.0, le=1.0)
    h: float = Field(..., ge=0.0, le=1.0)

class SessionCreateReq(BaseModel):
    viewport_region: ViewportRegion

class PresignedUrlReq(BaseModel):
    session_id: int
    file_type: Literal["video", "screenshot", "calibration"]

class CalibrateReq(BaseModel):
    object_keys: List[str]
    screen_xs: List[float]
    screen_ys: List[float]

class PageLog(BaseModel):
    page_no: int
    url: Optional[str] = None
    start_video_ts: float
    end_video_ts: Optional[float] = None
    screenshot_path: Optional[str] = None

class TaskResult(BaseModel):
    task_order: int
    result: Literal["완료", "실패"]
    duration_sec: Optional[float] = None

class MetadataReq(BaseModel):
    page_logs: List[PageLog]
    task_results: List[TaskResult]
