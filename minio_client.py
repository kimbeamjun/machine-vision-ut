# minio_client.py
# MinIO 파일 업/다운로드 유틸리티
#
# 변경사항 (명세서 v5 기준):
# - delete_object 제거 (MinIO 영상 삭제는 메인 서버 담당)
# - AI 서버는 로컬 임시파일만 삭제

import io
from minio import Minio
from config import MINIO_ENDPOINT, MINIO_ACCESS, MINIO_SECRET, MINIO_BUCKET, MINIO_SECURE


def get_minio_client() -> Minio:
    return Minio(
        MINIO_ENDPOINT,
        access_key=MINIO_ACCESS,
        secret_key=MINIO_SECRET,
        secure=MINIO_SECURE,
    )


def download_video(object_key: str, local_path: str) -> str:
    """MinIO에서 본녹화 영상을 local_path에 다운로드
    object_key 예시: sessions/session_1/recording.mp4
    ※ 다운로드 후 로컬 임시파일 삭제는 호출자(celery_app.py finally)가 담당
    """
    client = get_minio_client()
    client.fget_object(MINIO_BUCKET, object_key, local_path)
    return local_path


def download_calibration_video(object_key: str, local_path: str) -> str:
    """MinIO에서 캘리브레이션 영상을 local_path에 다운로드
    object_key 예시: sessions/session_1/calibration_1.mp4
    ※ 다운로드 후 로컬 임시파일 삭제는 호출자(celery_app.py finally)가 담당
    """
    client = get_minio_client()
    client.fget_object(MINIO_BUCKET, object_key, local_path)
    return local_path


def download_screenshot(screenshot_path: str) -> bytes:
    """MinIO에서 스크린샷 이미지를 bytes로 반환"""
    client   = get_minio_client()
    response = client.get_object(MINIO_BUCKET, screenshot_path)
    try:
        return response.read()
    finally:
        response.close()
        response.release_conn()


def upload_json(object_key: str, data: bytes) -> str:
    """JSON 데이터를 MinIO에 업로드, object_key 반환
    경로: sessions/session_{id}/detail.json
    """
    client = get_minio_client()
    client.put_object(
        MINIO_BUCKET,
        object_key,
        io.BytesIO(data),
        length=len(data),
        content_type="application/json",
    )
    return object_key


def upload_image(object_key: str, image_bytes: bytes) -> str:
    """이미지(PNG)를 MinIO에 업로드, object_key 반환
    경로: sessions/session_{id}/heatmap.png
    """
    client = get_minio_client()
    client.put_object(
        MINIO_BUCKET,
        object_key,
        io.BytesIO(image_bytes),
        length=len(image_bytes),
        content_type="image/png",
    )
    return object_key
