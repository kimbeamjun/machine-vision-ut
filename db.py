# ai_server/utils/db.py
# DB 연결 및 쿼리 유틸리티

import pymysql
from contextlib import contextmanager
from config import DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME


def get_connection():
    return pymysql.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )


@contextmanager
def db_cursor():
    """with db_cursor() as cur: 형태로 사용"""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ── 세션 데이터 로드 ─────────────────────────────────────────────
def load_session(session_id: int) -> dict:
    """sessions 테이블에서 video_path, viewport_region 조회"""
    with db_cursor() as cur:
        cur.execute(
            "SELECT video_path, viewport_region FROM sessions WHERE id = %s",
            (session_id,),
        )
        row = cur.fetchone()
    if row is None:
        raise ValueError(f"session_id={session_id} not found")
    return row


def load_calibrations(session_id: int) -> list[dict]:
    """calibrations 5쌍 조회"""
    with db_cursor() as cur:
        cur.execute(
            """
            SELECT point_no, screen_x, screen_y, gaze_x, gaze_y
            FROM calibrations
            WHERE session_id = %s
            ORDER BY point_no
            """,
            (session_id,),
        )
        rows = cur.fetchall()
    if len(rows) < 5:
        raise ValueError(f"캘리브레이션 데이터 부족: {len(rows)}개 (5개 필요)")
    return rows


def load_page_logs(session_id: int) -> list[dict]:
    """page_logs 조회 (start_video_ts / end_video_ts 절대 타임스탬프)"""
    with db_cursor() as cur:
        cur.execute(
            """
            SELECT page_no, url, start_video_ts, end_video_ts, screenshot_path
            FROM page_logs
            WHERE session_id = %s
            ORDER BY page_no
            """,
            (session_id,),
        )
        return cur.fetchall()


def load_task_results(session_id: int) -> list[dict]:
    """task_results 조회"""
    with db_cursor() as cur:
        cur.execute(
            """
            SELECT task_order, result, duration_sec
            FROM task_results
            WHERE session_id = %s
            ORDER BY task_order
            """,
            (session_id,),
        )
        return cur.fetchall()


# ── 분석 결과 저장 ───────────────────────────────────────────────
def save_stt_segments(session_id: int, segments: list[dict]):
    """
    stt_segments 테이블에 발화 구간 저장
    segments: [{"start_ts", "end_ts", "text", "silence_sec"}, ...]
    """
    if not segments:
        return
    sql = """
        INSERT INTO stt_segments (session_id, start_ts, end_ts, text, silence_sec)
        VALUES (%s, %s, %s, %s, %s)
    """
    rows = [
        (session_id, s["start_ts"], s["end_ts"], s.get("text"), s["silence_sec"])
        for s in segments
    ]
    with db_cursor() as cur:
        cur.executemany(sql, rows)


def save_page_summaries(session_id: int, summaries: list[dict]):
    """
    page_summaries 테이블에 페이지 단위 집계 결과 저장 (UPSERT)
    summaries: [{"page_no", "url", "start_video_ts", "end_video_ts",
                 "dominant_emotion", "neg_ratio", "gaze_escape_ratio",
                 "confusion_avg", "task_confusion_json",
                 "stt_summary", "avg_silence_sec",
                 "task_success_rate", "avg_task_duration_sec",
                 "heatmap_path", "detail_json_path"}, ...]
    """
    sql = """
        INSERT INTO page_summaries (
            session_id, page_no, url,
            start_video_ts, end_video_ts,
            dominant_emotion, neg_ratio, gaze_escape_ratio,
            confusion_avg, task_confusion_json,
            stt_summary, avg_silence_sec,
            task_success_rate, avg_task_duration_sec,
            heatmap_path, detail_json_path
        ) VALUES (
            %(session_id)s, %(page_no)s, %(url)s,
            %(start_video_ts)s, %(end_video_ts)s,
            %(dominant_emotion)s, %(neg_ratio)s, %(gaze_escape_ratio)s,
            %(confusion_avg)s, %(task_confusion_json)s,
            %(stt_summary)s, %(avg_silence_sec)s,
            %(task_success_rate)s, %(avg_task_duration_sec)s,
            %(heatmap_path)s, %(detail_json_path)s
        )
        ON DUPLICATE KEY UPDATE
            dominant_emotion      = VALUES(dominant_emotion),
            neg_ratio             = VALUES(neg_ratio),
            gaze_escape_ratio     = VALUES(gaze_escape_ratio),
            confusion_avg         = VALUES(confusion_avg),
            task_confusion_json   = VALUES(task_confusion_json),
            stt_summary           = VALUES(stt_summary),
            avg_silence_sec       = VALUES(avg_silence_sec),
            task_success_rate     = VALUES(task_success_rate),
            avg_task_duration_sec = VALUES(avg_task_duration_sec),
            heatmap_path          = VALUES(heatmap_path),
            detail_json_path      = VALUES(detail_json_path)
    """
    import json
    with db_cursor() as cur:
        for s in summaries:
            s = dict(s)
            s["session_id"] = session_id
            if isinstance(s.get("task_confusion_json"), dict):
                s["task_confusion_json"] = json.dumps(s["task_confusion_json"])
            cur.execute(sql, s)


def update_session_status(session_id: int, status: str):
    """sessions.status 업데이트"""
    with db_cursor() as cur:
        cur.execute(
            "UPDATE sessions SET status = %s WHERE id = %s",
            (status, session_id),
        )


def update_report_status(session_id: int, status: str):
    """reports.status 업데이트"""
    with db_cursor() as cur:
        cur.execute(
            "UPDATE reports SET status = %s WHERE session_id = %s",
            (status, session_id),
        )
