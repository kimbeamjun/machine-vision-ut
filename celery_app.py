# celery_app.py
from celery import Celery


REDIS_URL = 'redis://:1234@127.0.0.1:6379/0'

app = Celery(
    'my_task_system',
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=['tasks'] # 실행할 작업이 정의된 파일 이름(tasks.py)
)

# 신뢰성 및 최적화 설정
app.conf.update(
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='Asia/Seoul',
    enable_utc=False,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_routes={
        'celery_app.analyze_calibration': {'queue': 'ai_tasks'},
        'celery_app.analyze_session': {'queue': 'ai_tasks'},
        'tasks.process_calibration_result': {'queue': 'main_tasks'},
        'tasks.process_analysis_result': {'queue': 'main_tasks'},
    }
)