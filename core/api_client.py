from __future__ import annotations

import requests
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class ApiConfig:
    base_url: str
    timeout_sec: float = 10.0


class ApiClient:
    def __init__(self, config: ApiConfig) -> None:
        self.config = config

    def create_session(self, user_id: str, project_id: int) -> Dict[str, Any]:
        """새로운 UT 세션을 생성합니다. (Source 8 엔드포인트 준수)"""
        return self._post("/sessions/", {"user_id": user_id, "project_id": project_id})

    def request_presigned_url(self, session_id: int, file_type: str = "video") -> Dict[str, Any]:
        """
        대용량 파일 업로드를 위한 Presigned URL을 요청합니다.
        file_type에 따라 video_url 또는 screenshot_url로 분기합니다.
        """
        endpoint = f"/sessions/{session_id}/video_url" if file_type == "video" else f"/sessions/{session_id}/screenshot_url"
        # GET 요청으로 설계된 경우를 대비해 처리
        response = requests.get(self._url(endpoint), timeout=self.config.timeout_sec)
        response.raise_for_status()
        return response.json()

    def upload_file(self, presigned_url: str, file_path: str) -> bool:
        """
        생성된 Presigned URL을 사용하여 파일을 MinIO에 직접 업로드합니다.
        이 함수는 파일 전체를 읽으므로 나중에 Worker에서 실행해야 UI가 멈추지 않습니다.
        """
        try:
            with open(file_path, 'rb') as f:
                # S3/MinIO 업로드는 보통 PUT을 사용
                response = requests.put(presigned_url, data=f, timeout=None) # 업로드는 타임아웃 제외
            return response.status_code == 200
        except Exception as e:
            print(f"파일 업로드 중 오류 발생: {e}")
            return False

    def send_metadata(
        self,
        session_id: int,
        calibrations: List[Dict[str, Any]],
        page_logs: List[Dict[str, Any]],
        task_results: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        [이슈 3] 절대 타임스탬프가 포함된 페이지 로그 및 메타데이터를 전송합니다.
        """
        return self._post(
            f"/sessions/{session_id}/metadata",
            {
                "calibrations": calibrations,
                "page_logs": page_logs,
                "task_results": task_results,
            },
        )

    def request_analysis(self, session_id: int) -> Dict[str, Any]:
        """분석 서버에 분석 시작을 요청합니다."""
        return self._post(f"/sessions/{session_id}/analyze", {})

    def report_status(self, session_id: int) -> requests.Response:
        """분석 결과 리포트 상태를 확인합니다."""
        return requests.get(
            self._url(f"/sessions/{session_id}/report"),
            timeout=self.config.timeout_sec,
        )

    def _post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """POST 요청 공통 유틸리티"""
        response = requests.post(
            self._url(path),
            json=payload,
            timeout=self.config.timeout_sec,
        )
        response.raise_for_status()
        return response.json()

    def _url(self, path: str) -> str:
        """URL 경로 병합 유틸리티"""
        return f"{self.config.base_url.rstrip('/')}/{path.lstrip('/')}"