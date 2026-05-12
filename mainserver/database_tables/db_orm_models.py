from sqlalchemy import Column, Integer, String, Float, Text, JSON, ForeignKey, DateTime, func, UniqueConstraint
from sqlalchemy.orm import relationship
from app_settings.db_connection import Base

class SessionModel(Base):
    __tablename__ = "sessions"
    id = Column(Integer, primary_key=True, autoincrement=True)
    video_path = Column(String(500), nullable=True)
    viewport_region = Column(JSON, nullable=True)
    status = Column(String(20), nullable=False, default="uploaded")
    failed_points = Column(JSON, nullable=True)
    created_at = Column(DateTime, nullable=False, default=func.now())
    updated_at = Column(DateTime, nullable=True, onupdate=func.now())

    calibrations = relationship("CalibrationModel", back_populates="session", cascade="all, delete-orphan")
    calibration_points = relationship("CalibrationPointModel", back_populates="session", cascade="all, delete-orphan")
    page_logs = relationship("PageLogModel", back_populates="session", cascade="all, delete-orphan")
    page_summaries = relationship("PageSummaryModel", back_populates="session", cascade="all, delete-orphan")
    report = relationship("ReportModel", back_populates="session", uselist=False, cascade="all, delete-orphan")
    stt_segments = relationship("SttSegmentModel", back_populates="session", cascade="all, delete-orphan")
    task_results = relationship("TaskResultModel", back_populates="session", cascade="all, delete-orphan")

class CalibrationPointModel(Base):
    __tablename__ = "calibration_points"
    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(Integer, ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False)
    point_no = Column(Integer, nullable=False)
    screen_x = Column(Float, nullable=False)
    screen_y = Column(Float, nullable=False)
    object_key = Column(String(500), nullable=False)
    session = relationship("SessionModel", back_populates="calibration_points")

    __table_args__ = (
        UniqueConstraint('session_id', 'point_no', name='uq_calibration_points'),
    )

class CalibrationModel(Base):
    __tablename__ = "calibrations"
    __table_args__ = (UniqueConstraint("session_id", "point_no", name="uq_calibrations"),)
    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(Integer, ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False)
    point_no = Column(Integer, nullable=False)
    screen_x = Column(Float, nullable=False)
    screen_y = Column(Float, nullable=False)
    gaze_x = Column(Float, nullable=False)
    gaze_y = Column(Float, nullable=False)
    session = relationship("SessionModel", back_populates="calibrations")

class PageLogModel(Base):
    __tablename__ = "page_logs"
    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(Integer, ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False)
    page_no = Column(Integer, nullable=False)
    url = Column(Text, nullable=True)
    start_video_ts = Column(Float, nullable=False)
    end_video_ts = Column(Float, nullable=True)
    screenshot_path = Column(String(500), nullable=True)
    session = relationship("SessionModel", back_populates="page_logs")

class PageSummaryModel(Base):
    __tablename__ = "page_summaries"
    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(Integer, ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False)
    page_no = Column(Integer, nullable=False)
    url = Column(Text, nullable=True)
    start_video_ts = Column(Float, nullable=False)
    end_video_ts = Column(Float, nullable=True)
    dominant_emotion = Column(String(20), nullable=True)
    neg_ratio = Column(Float, nullable=True)
    gaze_escape_ratio = Column(Float, nullable=True)
    confusion_avg = Column(Float, nullable=True)
    task_confusion_json = Column(JSON, nullable=True)
    stt_summary = Column(Text, nullable=True)
    avg_silence_sec = Column(Float, nullable=True)
    task_success_rate = Column(Float, nullable=True)
    avg_task_duration_sec = Column(Float, nullable=True)
    heatmap_path = Column(String(500), nullable=True)
    detail_json_path = Column(String(500), nullable=True)
    session = relationship("SessionModel", back_populates="page_summaries")

class ReportModel(Base):
    __tablename__ = "reports"
    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(Integer, ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, unique=True)
    pdf_path = Column(String(500), nullable=True)
    llm_text = Column(Text, nullable=True)
    status = Column(String(20), nullable=False, default="generating")
    created_at = Column(DateTime, nullable=False, default=func.now())
    updated_at = Column(DateTime, nullable=True, onupdate=func.now())
    session = relationship("SessionModel", back_populates="report")

class SttSegmentModel(Base):
    __tablename__ = "stt_segments"
    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(Integer, ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False)
    start_ts = Column(Float, nullable=False)
    end_ts = Column(Float, nullable=False)
    text = Column(Text, nullable=True)
    silence_sec = Column(Float, nullable=False, default=0.0)
    session = relationship("SessionModel", back_populates="stt_segments")

class TaskResultModel(Base):
    __tablename__ = "task_results"
    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(Integer, ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False)
    task_order = Column(Integer, nullable=False)
    result = Column(String(10), nullable=False)
    duration_sec = Column(Float, nullable=True)
    session = relationship("SessionModel", back_populates="task_results")
