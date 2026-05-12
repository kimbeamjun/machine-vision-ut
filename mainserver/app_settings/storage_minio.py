from minio import Minio

# --- MinIO 클라이언트 객체 및 공통 설정 ---
minio_client = Minio(
    "10.10.10.113:9000", 
    access_key="minioadmin", 
    secret_key="minioadmin", 
    secure=False
)

BUCKET_NAME = "ut-platform"
