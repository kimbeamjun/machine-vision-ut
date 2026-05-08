from pydantic import BaseModel, Field
from typing import List, Literal, Optional

class ViewportRegion(BaseModel):
    x: float = Field(..., ge=0.0, le=1.0)
    y: float = Field(..., ge=0.0, le=1.0)
    w: float = Field(..., ge=0.0, le=1.0)
    h: float = Field(..., ge=0.0, le=1.0)

class SessionCreateReq(BaseModel):
    viewport_region: ViewportRegion

class CalibPresignedUrlReq(BaseModel):
    point_no: int
    screen_x: float
    screen_y: float

class PresignedUrlReq(BaseModel):
    file_type: Literal["video", "recording", "screenshot", "calibration"]

class PageLog(BaseModel):
    page_no: int
    url: Optional[str] = None
    start_video_ts: float
    end_video_ts: Optional[float] = None
    screenshot_path: Optional[str] = None

class TaskResult(BaseModel):
    task_order: int
    result: Literal["success", "fail"]
    duration_sec: Optional[float] = None

class MetadataReq(BaseModel):
    page_logs: List[PageLog]
    task_results: List[TaskResult]
