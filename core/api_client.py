from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import requests


@dataclass(frozen=True)
class ApiConfig:
    """HTTP API connection settings."""

    base_url: str = "http://10.10.10.113:8001"
    timeout_sec: float = 30.0

    minio_url: str = "http://10.10.10.113:9000"

class ApiClient:
    def __init__(self, config: ApiConfig) -> None:
        self.config = config
        self.session_id: Optional[str] = None

    def create_session(self, viewport_region: Dict[str, float]) -> Dict[str, Any]:
        response_data = self._post("/api/v1/sessions", {"viewport_region": viewport_region})

        raw_id = response_data.get("session_id")
        if raw_id is not None:
            self.session_id = str(raw_id)

        return response_data

    def request_presigned_url(
        self,
        file_type: str = "recording",
        point_no: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Request a MinIO presigned upload URL.

        file_type follows the API spec: calibration, recording, or screenshot.
        point_no is required only for calibration uploads.
        """
        if not self.session_id:
            raise ValueError("session_id가 없습니다.")

        payload: Dict[str, Any] = {
            "session_id": self.session_id,
            "file_type": file_type,
        }

        if file_type == "calibration":
            if point_no is None:
                raise ValueError("calibration 업로드에는 point_no가 필요합니다.")
            payload["point_no"] = point_no

        return self._post("/api/v1/sessions/presigned-url", payload)

    def upload_file(self, presigned_url: str, file_path: str) -> bool:
        """Upload a local file directly to MinIO with the presigned URL."""
        try:
            with open(file_path, "rb") as file:
                response = requests.put(presigned_url, data=file, timeout=None)
            return response.status_code in (200, 201, 204)
        except Exception as exc:
            print(f"MinIO 업로드 중 오류 발생: {exc}")
            return False

    def send_metadata(
        self,
        page_logs: List[Dict[str, Any]],
        task_results: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Send page logs and task results.

        v6 keeps calibration data out of /metadata; it is handled by /calibrate.
        """
        if not self.session_id:
            raise ValueError("session_id가 없습니다.")

        return self._post(
            f"/api/v1/sessions/{self.session_id}/metadata",
            {
                "page_logs": page_logs,
                "task_results": task_results,
            },
        )

    def register_calibration(self, calibration_points: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Register uploaded calibration videos for AI-side calibration analysis."""
        if not self.session_id:
            raise ValueError("session_id가 없습니다.")

        return self._post_array(
            f"/api/v1/sessions/{self.session_id}/calibrate",
            calibration_points,
        )

    def check_calibration_status(self) -> Dict[str, Any]:
        """Poll calibration analysis status."""
        if self.session_id is None:
            raise ValueError("session_id가 없습니다.")

        response = requests.get(
            self._url(f"/api/v1/sessions/{self.session_id}/calibrate/status"),
            timeout=self.config.timeout_sec,
        )
        response.raise_for_status()
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError:
            return {"raw_response": response.text}

    def start_analysis(self) -> Dict[str, Any]:
        """Notify the server that the final recording upload is complete."""
        if self.session_id is None:
            raise ValueError("session_id가 없습니다.")

        return self._post(f"/api/v1/sessions/{self.session_id}/analyze", {})

    def get_report_status(self) -> Dict[str, Any]:
        """Poll final PDF report status."""
        if self.session_id is None:
            raise ValueError("session_id가 없습니다.")

        response = requests.get(
            self._url(f"/api/v1/sessions/{self.session_id}/report"),
            timeout=self.config.timeout_sec,
        )
        if response.status_code == 202:
            try:
                data = response.json()
            except ValueError:
                data = {}
            return {"status": data.get("status", "generating"), **data}

        response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        if "application/pdf" in content_type:
            return {"status": "done", "pdf_bytes": response.content}

        try:
            return response.json()
        except ValueError:
            return {"status": "done", "raw_response": response.text}

    def _post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        response = requests.post(
            self._url(path),
            json=payload,
            timeout=self.config.timeout_sec,
        )
        response.raise_for_status()
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError:
            return {"raw_response": response.text}

    def _post_array(self, path: str, payload: List[Any]) -> Dict[str, Any]:
        response = requests.post(
            self._url(path),
            json=payload,
            timeout=self.config.timeout_sec,
        )
        response.raise_for_status()
        return response.json()

    def _url(self, path: str) -> str:
        return f"{self.config.base_url.rstrip('/')}/{path.lstrip('/')}"
    
    def abort_session(self) -> Dict[str, Any]:
        """
        서버에 현재 세션 중단을 알리고, 업로드된 임시 파일 등의 리소스 삭제를 요청합니다.
        """
        if self.session_id is None:
            return {}

        try:
            # 서버 명세에 따라 엔드포인트를 조정하세요. 
            # 예: /api/v1/sessions/{id}/abort 또는 DELETE /api/v1/sessions/{id}
            return self._post(f"/api/v1/sessions/{self.session_id}/abort", {})
        except Exception as e:
            print(f"세션 중단 요청 중 오류 발생: {e}")
            return {"error": str(e)}
