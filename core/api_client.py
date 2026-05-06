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
    timeout_sec: float = 30.0  # 일반 API 타임아웃은 30초 권장


class ApiClient:
    def __init__(self, config: ApiConfig) -> None:
        self.config = config
        self.session_id: Optional[str] = None  # [수정] 명세서 기준 UUID string 타입

    def create_session(self, viewport_region: Dict[str, float]) -> Dict[str, Any]:
        """세션 생성 및 ID 저장"""
        response_data = self._post("/api/v1/sessions", {"viewport_region": viewport_region})

        # [수정] session_id는 UUID string 그대로 유지 (int 변환 제거)
        raw_id = response_data.get("session_id")
        if raw_id is not None:
            self.session_id = str(raw_id)

        return response_data

    def request_presigned_url(
        self,
        file_type: str = "video",
        point_no: Optional[int] = None,  # [수정] calibration 타입 시 필수 파라미터 추가
    ) -> Dict[str, Any]:
        """
        MinIO 업로드를 위한 Presigned URL 발급 요청.
        :param file_type: 'calibration' | 'recording' | 'screenshot'
        :param point_no: file_type이 'calibration'인 경우 필수 (1~5)
        """
        if not self.session_id:
            raise ValueError("session_id가 없습니다.")

        payload: Dict[str, Any] = {
            "session_id": self.session_id,
            "file_type": file_type,  # [수정] 명세서 기준: calibration | recording | screenshot
        }

        # [수정] calibration 타입일 때 point_no 포함 (명세서 1단계)
        if file_type == "calibration":
            if point_no is None:
                raise ValueError("calibration 타입은 point_no가 필요합니다.")
            payload["point_no"] = point_no

        return self._post("/api/v1/sessions/presigned-url", payload)

    def upload_file(self, presigned_url: str, file_path: str) -> bool:
        """생성된 Presigned URL을 사용하여 파일을 MinIO에 직접 업로드합니다."""
        try:
            with open(file_path, 'rb') as f:
                # MinIO 업로드는 PUT 메서드를 사용하며 바이너리 데이터를 직접 전송합니다.
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
        페이지 로그 및 테스크 수행 결과를 전송합니다. (명세서 D 항목)
        주의: calibrations는 별도 엔드포인트(/calibrate)에서 처리하므로 제외됩니다.
        """
        if not self.session_id:
            raise ValueError("session_id가 없습니다.")

        return self._post(
            f"/api/v1/sessions/{self.session_id}/metadata",  # [수정] /api/v1 prefix 추가
            {
                "page_logs": page_logs,
                "task_results": task_results,
            },
        )

    def register_calibration(self, calibration_points: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        캘리브레이션 영상 정보 전송. (명세서 B 항목)
        :param calibration_points: [{"point_no", "screen_x", "screen_y", "video_object_key"}, ...]
        """
        if not self.session_id:
            raise ValueError("session_id가 없습니다.")

        # [수정] 명세서 B 항목 기준: body는 래퍼 없이 배열을 직접 전송
        return self._post_array(
            f"/api/v1/sessions/{self.session_id}/calibrate",  # [수정] /api/v1 prefix 추가
            calibration_points,
        )

    def check_calibration_status(self) -> Dict[str, Any]:
        """캘리브레이션 분석 상태 확인 폴링. (명세서 C 항목)"""
        if self.session_id is None:
            raise ValueError("session_id가 없습니다.")

        response = requests.get(
            self._url(f"/api/v1/sessions/{self.session_id}/calibrate/status"),  # [수정] /api/v1 prefix 추가
            timeout=self.config.timeout_sec,
        )
        response.raise_for_status()
        return response.json()

    def get_report_status(self) -> Dict[str, Any]:
        """
        [추가] 본녹화 분석 완료 후 리포트(PDF) 생성 상태를 폴링합니다. (명세서 12단계)
        응답 status: 'generating' | 'done' | 'failed'
        done 시 'pdf_url' 및 'pdf_presigned_url' 포함.
        """
        if self.session_id is None:
            raise ValueError("session_id가 없습니다.")

        response = requests.get(
            self._url(f"/api/v1/sessions/{self.session_id}/report"),
            timeout=self.config.timeout_sec,
        )
        response.raise_for_status()
        return response.json()

    # [수정] request_analysis 제거:
    #   명세서 4단계에 따라 MinIO 업로드 완료 시 웹훅으로 분석이 자동 트리거됩니다.
    #   클라이언트가 별도로 분석 시작을 요청하는 엔드포인트는 명세서에 없습니다.

    def _post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """POST 요청 공통 유틸리티 (JSON dict body)"""
        response = requests.post(
            self._url(path),
            json=payload,
            timeout=self.config.timeout_sec,
        )
        response.raise_for_status()
        return response.json()

    def _post_array(self, path: str, payload: List[Any]) -> Dict[str, Any]:
        """[추가] POST 요청 공통 유틸리티 (JSON array body — 명세서 B 항목용)"""
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