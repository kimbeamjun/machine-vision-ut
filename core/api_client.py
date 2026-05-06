# core/api_client.py
from __future__ import annotations

import requests
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class ApiConfig:
    """API 설정 정보를 담는 데이터 클래스"""
    base_url: str = "http://127.0.0.1:8000"
    timeout_sec: float = 30.0 # 일반 API 타임아웃은 30초 권장


class ApiClient:
    def __init__(self, config: ApiConfig) -> None:
        self.config = config
        self.session_id: Optional[int] = None

    def create_session(self, viewport_region: Dict[str, float]) -> Dict[str, Any]:
        """ [엔드포인트 1] 세션 생성 및 ID 저장 """
        response_data = self._post("/sessions", {"viewport_region": viewport_region})
        
        # [수정] 내부 session_id도 int로 유지
        raw_id = response_data.get("session_id")
        if raw_id is not None:
            self.session_id = int(raw_id)
            
        return response_data

    def request_presigned_url(self, file_type: str = "video") -> Dict[str, Any]:
        """
        [엔드포인트 2] file_type에 'calibration' 추가 대응
        """
        if not self.session_id:
            raise ValueError("session_id가 없습니다.")

        payload = {
            "session_id": self.session_id,
            "file_type": file_type # 'video', 'screenshot', 'calibration' 지원
        }
        return self._post("/sessions/presigned-url", payload)

    def upload_file(self, presigned_url: str, file_path: str) -> bool:
        """
        [STEP 3] 생성된 Presigned URL을 사용하여 파일을 MinIO에 직접 업로드합니다.[cite: 4]
        """
        try:
            with open(file_path, 'rb') as f:
                # MinIO 업로드는 PUT 메서드를 사용하며 바이너리 데이터를 직접 전송합니다.[cite: 4]
                response = requests.put(presigned_url, data=f, timeout=None) 
            return response.status_code == 200
        except Exception as e:
            print(f"MinIO 업로드 중 오류 발생: {e}")
            return False

    def send_metadata(
        self,
        page_logs: List[Dict[str, Any]],
        task_results: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        [엔드포인트 3] 페이지 로그 및 테스크 수행 결과를 전송합니다.[cite: 5]
        주의: calibrations는 별도 엔드포인트(/calibrate)에서 처리하므로 제외되었습니다.[cite: 5]
        """
        if not self.session_id:
            raise ValueError("session_id가 없습니다.")

        return self._post(
            f"/sessions/{self.session_id}/metadata",
            {
                "page_logs": page_logs,
                "task_results": task_results,
            },
        )

    def register_calibration(self, calibration_points: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        [v6 엔드포인트 3] 캘리브레이션 영상 정보 전송 (/sessions/{id}/calibrate)
        :param calibration_points: [{"point_no", "screen_x", "screen_y", "video_object_key"}...]
        """
        if not self.session_id:
            raise ValueError("session_id가 없습니다.")

        return self._post(
            f"/sessions/{self.session_id}/calibrate",
            {"calibration_points": calibration_points}
        )

    def check_calibration_status(self) -> Dict[str, Any]:
        """ [v6] 분석 상태 확인 폴링[cite: 2, 5] """
        if self.session_id is None:
            raise ValueError("session_id가 없습니다.")

        response = requests.get(
            self._url(f"/sessions/{self.session_id}/calibrate/status"),
            timeout=self.config.timeout_sec
        )
        response.raise_for_status()
        return response.json()

    def request_analysis(self, video_object_key: str) -> Dict[str, Any]:
        """
        [엔드포인트 8] 최종 테스트 영상 업로드 후 분석 시작을 요청합니다.[cite: 5]
        :param video_object_key: MinIO에 업로드된 영상의 경로(object_key)[cite: 5]
        """
        if not self.session_id:
            raise ValueError("session_id가 없습니다.")

        return self._post(
            f"/sessions/{self.session_id}/analyze",
            {"video_object_key": video_object_key}
        )

    def _post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """POST 요청 공통 유틸리티 (JSON 형식)[cite: 4]"""
        response = requests.post(
            self._url(path),
            json=payload,
            timeout=self.config.timeout_sec,
        )
        # 200(성공) 또는 202(처리중) 이외의 경우 에러 발생[cite: 4]
        response.raise_for_status()
        return response.json()

    def _url(self, path: str) -> str:
        """URL 경로 병합 유틸리티"""
        return f"{self.config.base_url.rstrip('/')}/{path.lstrip('/')}"