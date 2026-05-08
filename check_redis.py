"""
check_redis.py
Redis 연결 진단 스크립트
AI 서버(10.10.10.128)에서 실행
"""

import socket
import subprocess
import sys

GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
RESET  = "\033[0m"

def ok(msg):   print(f"{GREEN}[OK]{RESET}   {msg}")
def fail(msg): print(f"{RED}[FAIL]{RESET} {msg}")
def info(msg): print(f"{YELLOW}[INFO]{RESET} {msg}")
def sep():     print("-" * 50)


def check_port(host, port, label):
    try:
        s = socket.create_connection((host, port), timeout=3)
        s.close()
        ok(f"{label} 포트 열림 ({host}:{port})")
        return True
    except ConnectionRefusedError:
        fail(f"{label} 포트 거부됨 ({host}:{port}) — Redis가 안 켜진 상태")
        return False
    except OSError as e:
        fail(f"{label} 포트 접근 불가 ({host}:{port}) — {e}")
        return False


def check_redis_process():
    sep()
    info("로컬 Redis 프로세스 확인")
    try:
        result = subprocess.run(
            ["ps", "aux"],
            capture_output=True, text=True
        )
        redis_lines = [l for l in result.stdout.splitlines() if "redis" in l.lower()]
        if redis_lines:
            for l in redis_lines:
                ok(f"  {l.strip()}")
        else:
            fail("Redis 프로세스 없음")
    except Exception as e:
        fail(f"프로세스 확인 실패: {e}")


def check_redis_config():
    sep()
    info("Redis 설정 파일 확인 (bind 주소)")
    paths = [
        "/etc/redis/redis.conf",
        "/etc/redis.conf",
        "/usr/local/etc/redis/redis.conf",
    ]
    for path in paths:
        try:
            with open(path) as f:
                lines = f.readlines()
            bind_lines = [l.strip() for l in lines if l.strip().startswith("bind")]
            port_lines = [l.strip() for l in lines if l.strip().startswith("port")]
            if bind_lines or port_lines:
                ok(f"설정 파일: {path}")
                for l in bind_lines: info(f"  {l}")
                for l in port_lines: info(f"  {l}")
            return
        except FileNotFoundError:
            continue
    fail("Redis 설정 파일을 찾을 수 없음")


if __name__ == "__main__":
    print("=" * 50)
    print("Redis 연결 진단")
    print("=" * 50)

    sep()
    info("1. 포트 연결 테스트")
    q_a = check_port("10.10.10.113", 6379, "큐A (메인서버)")
    q_b = check_port("10.10.10.128", 6380, "큐B (AI서버 로컬)")
    check_port("127.0.0.1",     6379, "큐A 로컬루프백")
    check_port("127.0.0.1",     6380, "큐B 로컬루프백")

    check_redis_process()
    check_redis_config()

    sep()
    print("\n[조치 방법]")
    if not q_a:
        print(f"{YELLOW}큐A (10.10.10.113:6379) 연결 실패{RESET}")
        print("  → 메인 서버(10.10.10.113)에서 Redis 실행 여부 확인:")
        print("    sudo systemctl status redis")
        print("    sudo systemctl start redis")
        print("  → Redis bind 설정 확인 (0.0.0.0 또는 10.10.10.113이어야 함)")
        print("    sudo nano /etc/redis/redis.conf  # bind 0.0.0.0")
        print("  → 방화벽 확인:")
        print("    sudo ufw allow 6379")

    if not q_b:
        print(f"{YELLOW}큐B (10.10.10.128:6380) 연결 실패{RESET}")
        print("  → AI 서버(10.10.10.128) 로컬에서 6380 포트 Redis 실행 여부 확인:")
        print("    sudo systemctl status redis")
        print("    redis-cli -p 6380 ping")
        print("  → 6380 포트로 별도 Redis 인스턴스 실행하는 방법:")
        print("    redis-server --port 6380 --daemonize yes")
        print("  → 방화벽 확인:")
        print("    sudo ufw allow 6380")
