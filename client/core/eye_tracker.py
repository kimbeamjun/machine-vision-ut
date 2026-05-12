import time
from typing import Optional, Dict, Any
from PySide6.QtCore import QThread, Signal
from core.api_client import ApiClient

class EyeTracker(QThread):
    """
    서버로부터 시선 추적 결과(Gaze Data)를 실시간 또는 주기적으로 수신하여 
    UI에 전달하는 역할을 담당하는 클래스입니다.
    v6 요구사항에 따라 클라이언트는 직접 계산하지 않고 서버의 분석 결과를 활용합니다.
    """
    
    # 시선 좌표 수신 시 발생 (x, y 좌표는 화면 비율 0.0 ~ 1.0)
    gaze_data_received = Signal(float, float)
    # 오류 발생 시 메시지 전달
    error_occurred = Signal(str)

    def __init__(self, api_client: ApiClient, session_id: Optional[str] = None):
        """
        초기화 메서드
        :param api_client: 서버 통신을 위한 ApiClient 인스턴스
        :param session_id: 현재 진행 중인 테스트 세션 ID
        """
        super().__init__()
        self.api = api_client
        self.session_id = session_id
        self.is_running = False
        self.polling_interval = 0.1  # 10Hz 정도로 서버 데이터 확인 (필요 시 조절)

    def set_session_id(self, session_id: str):
        """
        세션 ID를 설정합니다. (세션 생성 후 호출)
        :param session_id: 서버에서 발급받은 세션 식별자
        """
        self.session_id = session_id

    def stop(self):
        """
        추적 루프를 안전하게 종료합니다.
        """
        self.is_running = False

    def run(self):
        """
        스레드 실행 루프: 서버에서 최신 시선 데이터를 가져와 시그널을 보냅니다.
        """
        if not self.session_id:
            self.error_occurred.emit("세션 ID가 설정되지 않아 EyeTracker를 시작할 수 없습니다.")
            return

        self.is_running = True
        
        while self.is_running:
            try:
                # [V6 기준 로직] 
                # 서버의 /sessions/{id}/gaze/latest 등의 엔드포인트가 있다고 가정하고 호출합니다.
                # 실제 API 명세에 따라 이 부분은 _get() 또는 _post()로 조정될 수 있습니다.
                
                # 시뮬레이션: 현재는 API 클라이언트에 gaze 전용 메서드가 없으므로 
                # 구조적 예시를 위해 일반 _post 혹은 _get을 사용한다고 가정합니다.
                # response = self.api._get(f"/api/v1/sessions/{self.session_id}/gaze/latest")
                
                # 임시로 시뮬레이션 데이터 전송 (서버 연동 시 위 API 호출로 대체)
                # if response and "x" in response and "y" in response:
                #     self.gaze_data_received.emit(response["x"], response["y"])
                
                pass 

            except Exception as e:
                self.error_occurred.emit(f"시선 데이터 수신 중 오류 발생: {str(e)}")
                self.is_running = False
            
            time.sleep(self.polling_interval)

    def get_overlay_coordinates(self, x_ratio: float, y_ratio: float, screen_w: int, screen_h: int) -> tuple[int, int]:
        """
        서버로부터 받은 비율 좌표(0~1)를 실제 스크린 픽셀 좌표로 변환합니다.
        :param x_ratio: 가로 비율
        :param y_ratio: 세로 비율
        :param screen_w: 현재 모니터 너비
        :param screen_h: 현재 모니터 높이
        :return: (pixel_x, pixel_y)
        """
        pixel_x = int(x_ratio * screen_w)
        pixel_y = int(y_ratio * screen_h)
        return pixel_x, pixel_y