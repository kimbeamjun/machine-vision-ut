from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional
import requests

@dataclass(frozen=True)
class ApiConfig:
    """HTTP API 연결 설정[cite: 4]."""
    base_url: str = "http://10.10.10.113:8000" # 메인서버 포트 [cite: 4]
    timeout_sec: float = 30.0
    minio_url: str = "http://10.10.10.113:9000"

class ApiClient:
    def __init__(self, config: ApiConfig) -> None:
        self.config = config
        self.session_id: Optional[str] = None

    def _url(self, path: str) -> str:
        """베이스 URL과 경로를 안전하게 결합합니다."""
        return f"{self.config.base_url.rstrip('/')}/{path.lstrip('/')}"

    def _post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """POST 요청 공통 처리 함수."""
        response = requests.post(
            self._url(path),
            json=payload,
            timeout=self.config.timeout_sec,
        )
        response.raise_for_status()
        return response.json() if response.content else {}

    def create_session(self, viewport_region: Dict[str, float]) -> Dict[str, Any]:
        """CL-1: 세션 생성[cite: 15, 36]."""
        response_data = self._post("/api/v1/sessions", {"viewport_region": viewport_region})
        raw_id = response_data.get("session_id")
        if raw_id is not None:
            self.session_id = str(raw_id)
        return response_data

    def request_presigned_url(
        self,
        point_no: Optional[int] = None,
        screen_x: Optional[float] = None,
        screen_y: Optional[float] = None,
        file_type: str = "calibration",
        **kwargs  # 예상치 못한 다른 키('x' 등)가 들어와도 에러를 방지함
    ) -> Dict[str, Any]:
        if not self.session_id:
            raise ValueError("session_id가 없습니다.")

        if file_type == "calibration":
            # 인자가 None일 경우 kwargs에서 'x', 'y'라도 찾아서 할당 (필살기)
            s_x = screen_x if screen_x is not None else kwargs.get('x', 0.0)
            s_y = screen_y if screen_y is not None else kwargs.get('y', 0.0)
            p_no = point_no if point_no is not None else 1
            
            payload = {
                "point_no": int(p_no),
                "screen_x": float(s_x),
                "screen_y": float(s_y)
            }
            path = f"/api/v1/sessions/{self.session_id}/calibrate/presigned-url"
            return self._post(path, payload)
        else:
            # [FIX] file_type 파라미터를 그대로 전달 (이전에 "recording" 하드코딩 → 스크린샷 URL 오발급 버그)
            path = f"/api/v1/sessions/{self.session_id}/presigned-url"
            return self._post(path, {"file_type": file_type})
        
    def upload_file(self, presigned_url: str, file_path: str) -> bool:
        """
        CL-3 / CL-7: MinIO 파일 직접 업로드 [cite: 53, 104]
        SignatureDoesNotMatch 에러 해결을 위해 호스트 헤더를 고정하거나 
        서버 설정에 맞게 URL을 처리합니다.
        """
        try:
            # 1. localhost 주소 치환
            if "localhost" in presigned_url:
                # 서명은 URL의 호스트 부분에 민감하므로 치환 후 호스트 헤더를 명시할 수도 있습니다.
                presigned_url = presigned_url.replace("localhost", "10.10.10.113")

            with open(file_path, "rb") as file:
                # 2. PUT 요청 시 Content-Type을 명세서대로 지정 [cite: 57, 108]
                # 서버에서 서명을 만들 때 Content-Type을 포함했다면 여기서 일치해야 합니다.
                headers = {
                    'Content-Type': 'video/mp4' 
                }
                
                print(f"DEBUG: MinIO 업로드 시작 -> {presigned_url}")
                response = requests.put(
                    presigned_url, 
                    data=file, 
                    headers=headers,
                    timeout=None
                )
            
            if response.status_code in (200, 201, 204):
                print(f"✅ 업로드 성공: {file_path} [cite: 59, 110]")
                return True
            else:
                # 403 에러 발생 시 서버 응답 상세 출력
                print(f"❌ 업로드 실패 (HTTP {response.status_code}): {response.text}")
                return False

        except Exception as exc:
            print(f"MinIO 업로드 중 예외 발생: {exc}")
            return False

    def register_calibration(self, calibration_points: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        CL-4: 캘리브레이션 분석 시작 요청 (동기 대기).
        PDF 명세상 서버가 AI 큐B 결과를 받을 때까지 대기 후 응답반환.
        최대 120초 소요 → 타임아웃 150초로 설정.
        응답: {"status": "success"} | {"status": "failed", "failed_points": [...]} | {"status": "error"}
        """
        if not self.session_id:
            raise ValueError("session_id가 없습니다.")

        # [FIX] CL-4는 동기 처리(최대 120초) → _post의 30초 타임아웃을 우회, 150초로 직접 호출
        response = requests.post(
            self._url(f"/api/v1/sessions/{self.session_id}/calibrate/start"),
            json={},           # CL-4 요청 body 없음 (서버가 DB에서 calibration_points 조회)
            timeout=150.0,
        )
        response.raise_for_status()
        return response.json() if response.content else {}

    def check_calibration_status(self) -> Dict[str, Any]:
        """캘리브레이션 상태 확인 (v5 프로토타입은 동기 대기이나 하위 호환 유지)."""
        if not self.session_id:
            raise ValueError("session_id가 없습니다.")
        
        response = requests.get(
            self._url(f"/api/v1/sessions/{self.session_id}/calibrate/status"),
            timeout=self.config.timeout_sec,
        )
        return response.json() if response.content else {}

    def send_metadata(
        self,
        page_logs: List[Dict[str, Any]],
        task_results: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """CL-5: 테스트 메타데이터 전송[cite: 34, 80, 81]."""
        if not self.session_id:
            raise ValueError("session_id가 없습니다.")

        return self._post(
            f"/api/v1/sessions/{self.session_id}/metadata",
            {
                "page_logs": page_logs,
                "task_results": task_results,
            },
        )

    def start_analysis(self) -> Dict[str, Any]:
        """CL-8: 본분석 시작 요청[cite: 34, 111, 112]."""
        if self.session_id is None:
            raise ValueError("session_id가 없습니다.")
        return self._post(f"/api/v1/sessions/{self.session_id}/analyze", {})

    def get_report_status(self) -> Dict[str, Any]:
        """최종 리포트 생성 상태 확인[cite: 23, 176]."""
        if not self.session_id:
            raise ValueError("session_id가 없습니다.")

        response = requests.get(
            self._url(f"/api/v1/sessions/{self.session_id}/report"),
            timeout=self.config.timeout_sec,
        )
        if response.status_code == 202:
            return {"status": "generating"}
        
        response.raise_for_status()
        if "application/pdf" in response.headers.get("content-type", ""):
            return {"status": "done", "pdf_bytes": response.content}
        return response.json()

    def upload_bytes(self, presigned_url: str, data: bytes, content_type: str = "image/png") -> bool:
        """
        메모리 바이트를 MinIO Presigned URL에 직접 업로드한다 (스크린샷 등 파일 저장 없이 업로드 시 사용).
        upload_file과 동일한 localhost 치환 및 헤더 처리를 공유한다.
        """
        try:
            if "localhost" in presigned_url:
                presigned_url = presigned_url.replace("localhost", "10.10.10.113")

            print(f"DEBUG: 바이트 업로드 시작 ({len(data)} bytes) -> {presigned_url[:80]}...")
            response = requests.put(
                presigned_url,
                data=data,
                headers={"Content-Type": content_type},
                timeout=30,
            )
            if response.status_code in (200, 201, 204):
                print(f"✅ 바이트 업로드 성공")
                return True
            else:
                print(f"❌ 바이트 업로드 실패 (HTTP {response.status_code}): {response.text}")
                return False
        except Exception as exc:
            print(f"바이트 업로드 예외: {exc}")
            return False

    def abort_session(self) -> Dict[str, Any]:
        """세션 중단 및 리소스 삭제 요청."""
        if not self.session_id:
            return {}
        try:
            return self._post(f"/api/v1/sessions/{self.session_id}/abort", {})
        except Exception as e:
            print(f"세션 중단 중 오류 발생: {e}")
            return {"error": str(e)}