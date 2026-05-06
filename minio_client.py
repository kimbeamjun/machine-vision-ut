# ai_server/utils/minio_client.py
# MinIO 파일 업/다운로드 유틸리티

import io
import os
import tempfile
from minio import Minio
from minio.error import S3Error
from config import MINIO_ENDPOINT, MINIO_ACCESS, MINIO_SECRET, MINIO_BUCKET, MINIO_SECURE


def get_minio_client() -> Minio:
    return Minio(
        MINIO_ENDPOINT,
        access_key=MINIO_ACCESS,
        secret_key=MINIO_SECRET,
        secure=MINIO_SECURE,
    )


def download_video(object_key: str, local_path: str) -> str:
    """MinIO에서 영상 파일을 local_path에 다운로드"""
    client = get_minio_client()
    client.fget_object(MINIO_BUCKET, object_key, local_path)
    return local_path


def download_screenshot(screenshot_path: str) -> bytes:
    """MinIO에서 스크린샷 이미지를 bytes로 반환"""
    client = get_minio_client()
    response = client.get_object(MINIO_BUCKET, screenshot_path)
    try:
        return response.read()
    finally:
        response.close()
        response.release_conn()


def upload_json(object_key: str, data: bytes) -> str:
    """JSON 데이터를 MinIO에 업로드, object_key 반환"""
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
    """이미지(PNG)를 MinIO에 업로드, object_key 반환"""
    client = get_minio_client()
    client.put_object(
        MINIO_BUCKET,
        object_key,
        io.BytesIO(image_bytes),
        length=len(image_bytes),
        content_type="image/png",
    )
    return object_key


def delete_object(object_key: str):
    """MinIO에서 파일 삭제 (영상 분석 완료 후 즉시 삭제)"""
    client = get_minio_client()
    try:
        client.remove_object(MINIO_BUCKET, object_key)
    except S3Error as e:
        print(f"[MinIO] 삭제 실패 {object_key}: {e}")
