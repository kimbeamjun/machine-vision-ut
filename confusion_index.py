# ai_server/analysis/confusion_index.py
# 혼란도 점수 산출 (6개 요소 가중합 + 30프레임 롤링 스무딩)

import json
import numpy as np
from collections import defaultdict

from config import CI_W1, CI_W2, CI_W3, CI_W4, CI_W5, CI_W6, ROLLING_WINDOW


def _rolling_average(values: list[float], window: int) -> list[float]:
    """1D 롤링 평균 스무딩"""
    if not values:
        return []
    arr    = np.array(values, dtype=np.float32)
    kernel = np.ones(window) / window
    return np.convolve(arr, kernel, mode="same").tolist()


def _stay_sec_zscore_outlier(page_logs: list[dict]) -> dict[int, float]:
    """
    체류시간 z-score 이상치 → 이상치일수록 1.0에 가까운 점수 반환
    {page_no: outlier_score (0.0~1.0)}
    """
    stays = []
    for pl in page_logs:
        s = pl.get("start_video_ts", 0.0)
        e = pl.get("end_video_ts")
        stays.append((pl["page_no"], (e - s) if e else 0.0))

    if len(stays) < 2:
        return {pno: 0.0 for pno, _ in stays}

    vals   = np.array([v for _, v in stays], dtype=np.float32)
    mean_v = vals.mean()
    std_v  = vals.std() + 1e-8

    result = {}
    for pno, v in stays:
        z = abs((v - mean_v) / std_v)
        result[pno] = float(np.clip(z / 3.0, 0.0, 1.0))   # z=3 → 1.0

    return result


def calc_confusion_index(
    frame_emotions: list[dict],      # from emotion_analysis
    gaze_points:    list[dict],      # from gaze_analysis (w/ escaped 없음)
    gaze_escape_ratio_per_page: dict[int, float],
    stt_segments:   list[dict],      # from whisper_analysis
    page_logs:      list[dict],
    task_results:   list[dict],
    heatmap_paths:  dict[int, str],  # page_no → MinIO 경로
    emotion_detail_json_path: str,
    gaze_detail_json_path:    str,
) -> list[dict]:
    """
    6가지 요소를 가중합하여 페이지·태스크 단위 혼란도 산출

    반환: page_summaries INSERT용 dict 리스트
    """

    # ── w1: 부정감정 비율 (페이지별) ─────────────────────────────
    # frame_emotions에서 페이지 타임스탬프 기준으로 분류
    def get_page_no_for_ts(ts: float) -> int | None:
        for pl in page_logs:
            s = pl["start_video_ts"]
            e = pl["end_video_ts"] or float("inf")
            if s <= ts < e:
                return pl["page_no"]
        return None

    emotion_by_page = defaultdict(list)
    for fe in frame_emotions:
        pno = get_page_no_for_ts(fe["timestamp"])
        if pno is not None:
            emotion_by_page[pno].append(fe["emotion"])

    neg_ratio_per_page    = {}
    dominant_emotion_per_page = {}
    for pl in page_logs:
        pno    = pl["page_no"]
        emots  = emotion_by_page.get(pno, [])
        if emots:
            from collections import Counter
            cnt   = Counter(emots)
            total = len(emots)
            neg   = cnt.get("negative", 0) + cnt.get("confusion", 0)
            neg_ratio_per_page[pno]         = round(neg / total, 4)
            dominant_emotion_per_page[pno]  = cnt.most_common(1)[0][0]
        else:
            neg_ratio_per_page[pno]        = 0.0
            dominant_emotion_per_page[pno] = "neutral"

    # ── w3: 발화 공백 (페이지별 평균) ────────────────────────────
    stt_by_page = defaultdict(list)
    for seg in stt_segments:
        # stt_segments에는 page_no 없음 → 타임스탬프 기준
        pno = get_page_no_for_ts(seg["start_ts"])
        if pno is not None:
            stt_by_page[pno].append(seg["silence_sec"])

    avg_silence_per_page = {}
    for pl in page_logs:
        pno    = pl["page_no"]
        silences = stt_by_page.get(pno, [])
        avg_silence_per_page[pno] = round(np.mean(silences), 3) if silences else 0.0

    # ── w4: 발화 속도 급변 (페이지별) ────────────────────────────
    def _speech_speed_variance(segs: list[dict]) -> float:
        """발화 구간 길이(end-start) 분산으로 속도 급변 점수화"""
        durations = [s["end_ts"] - s["start_ts"] for s in segs if s["end_ts"] > s["start_ts"]]
        if len(durations) < 2:
            return 0.0
        return float(np.clip(np.std(durations) / (np.mean(durations) + 1e-8), 0.0, 1.0))

    speech_variance_per_page = {}
    for pl in page_logs:
        pno  = pl["page_no"]
        segs = [s for s in stt_segments if get_page_no_for_ts(s["start_ts"]) == pno]
        speech_variance_per_page[pno] = _speech_speed_variance(segs)

    # ── w5: 체류시간 이상치 ──────────────────────────────────────
    stay_outlier_per_page = _stay_sec_zscore_outlier(page_logs)

    # ── w6: 태스크 실패 여부 (태스크 → 페이지 매핑은 단순 순서 기준) ──
    # task_confusion_json: {"task_order": CI_score}
    task_fail_scores = {}
    for tr in task_results:
        task_fail_scores[str(tr["task_order"])] = 1.0 if tr["result"] == "실패" else 0.0

    # ── 페이지별 CI 계산 ─────────────────────────────────────────
    # 태스크가 특정 페이지에 귀속되는 정보가 없으므로
    # task_confusion_json은 페이지 전체 task 결과로 저장
    task_success_total = (
        sum(1 for t in task_results if t["result"] == "완료") / len(task_results)
        if task_results else 1.0
    )
    avg_task_duration = (
        np.mean([t["duration_sec"] for t in task_results if t["duration_sec"] is not None])
        if task_results else 0.0
    )
    w6_score = 1.0 - task_success_total   # 실패율

    summaries = []
    for pl in page_logs:
        pno = pl["page_no"]

        w1 = neg_ratio_per_page.get(pno, 0.0)
        w2 = gaze_escape_ratio_per_page.get(pno, 0.0)
        w3 = float(np.clip(avg_silence_per_page.get(pno, 0.0) / 30.0, 0.0, 1.0))
        w4 = speech_variance_per_page.get(pno, 0.0)
        w5 = stay_outlier_per_page.get(pno, 0.0)

        ci = (
            CI_W1 * w1 +
            CI_W2 * w2 +
            CI_W3 * w3 +
            CI_W4 * w4 +
            CI_W5 * w5 +
            CI_W6 * w6_score
        )
        ci = float(np.clip(ci, 0.0, 1.0))

        # STT 요약 (발화 텍스트 병합, 최대 500자)
        page_stt_texts = [
            s["text"] for s in stt_segments
            if get_page_no_for_ts(s["start_ts"]) == pno and s.get("text")
        ]
        stt_summary = " ".join(page_stt_texts)[:500] or None

        summaries.append({
            "page_no":              pno,
            "url":                  pl.get("url"),
            "start_video_ts":       pl["start_video_ts"],
            "end_video_ts":         pl.get("end_video_ts"),
            "dominant_emotion":     dominant_emotion_per_page.get(pno, "neutral"),
            "neg_ratio":            w1,
            "gaze_escape_ratio":    w2,
            "confusion_avg":        round(ci, 4),
            "task_confusion_json":  task_fail_scores,
            "stt_summary":          stt_summary,
            "avg_silence_sec":      avg_silence_per_page.get(pno, 0.0),
            "task_success_rate":    round(task_success_total, 4),
            "avg_task_duration_sec": round(float(avg_task_duration), 3),
            "heatmap_path":         heatmap_paths.get(pno),
            "detail_json_path":     gaze_detail_json_path,
        })

    return summaries
