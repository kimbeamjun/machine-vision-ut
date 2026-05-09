import os
import asyncio
from dotenv import load_dotenv
from groq import AsyncGroq

load_dotenv()
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

async_client = AsyncGroq(api_key=GROQ_API_KEY)

async def generate_ut_report_llm(task_results: list, page_summaries: list, stt_segments: list) -> str:
    # 1. 데이터 포맷팅
    task_res_text = "\n".join([
        f"- Task {t.task_order}: 결과: {t.result} / 소요 시간: {t.duration_sec}초" 
        for t in task_results
    ])

    page_sum_text = "\n".join([
        f"- 페이지 URL: {p.url}\n  - 지배적 감정: {p.dominant_emotion} / 부정적 감정 비율: {int(p.neg_ratio * 100) if p.neg_ratio else 0}%\n  - 시선 이탈률: {int(p.gaze_escape_ratio * 100) if p.gaze_escape_ratio else 0}%\n  - 마우스/화면 혼란도 수치: {p.confusion_avg}/10\n  - 구간 내 요약된 음성: {p.stt_summary}"
        for p in page_summaries
    ])

    stt_text = "\n".join([
        f"- [{s.start_ts}s ~ {s.end_ts}s] (침묵 {s.silence_sec}초) : \"{s.text}\""
        for s in stt_segments
    ])

    prompt = f"""아래는 한 사용자가 우리 웹사이트(또는 앱)에서 특정 태스크(미션)를 수행하면서 수집된 사용성 테스트 데이터야. 이 데이터를 분석해서 최종 UT 리포트를 작성해 줘.

### 📊 1. 태스크 수행 결과 (Task Results)
{task_res_text}

### 📈 2. 페이지별 분석 데이터 (Page Summaries)
{page_sum_text}

### 🗣️ 3. 주요 사용자 발화 (STT Segments)
{stt_text}

---
위 데이터를 바탕으로 아래 양식에 맞추어 마크다운(Markdown) 리포트를 작성해 줘.

# 📋 사용성 테스트(UT) 종합 리포트

## 1. Executive Summary (요약)
- 테스트 전반에 대한 요약 및 전반적인 사용자의 감정/반응 평가

## 2. Key Findings (주요 발견 사항 & 페인포인트)
- 데이터(시선 이탈, 부정적 감정, STT 발화 등)를 근거로 사용자가 가장 크게 어려움을 겪은 2~3가지 핵심 페인포인트 도출

## 3. 페이지/태스크별 상세 분석
- 각 태스크와 페이지별로 겪은 긍정적/부정적 경험 상세 분석

## 4. Actionable Recommendations (UX 개선 제안)
- 발견된 페인포인트를 해결하기 위한 구체적이고 실현 가능한 UI/UX 디자인 수정 제안
"""

    # 2. 재시도 로직 구현 (최대 3회)
    max_retries = 3
    for attempt in range(max_retries):
        try:
            print(f"[LLM] Attempt {attempt + 1}/{max_retries} - Generating UT Report...")
            completion = await async_client.chat.completions.create(
                model="openai/gpt-oss-120b",
                messages=[
                    {"role": "system", "content": "너는 10년 차 수석 UX/UI 리서처이자 사용성 테스트(Usability Test) 전문가야. 복합적인 데이터를 종합하여 전문적인 비즈니스 보고서 형식으로 작성해."},
                    {"role": "user", "content": prompt}
                ],
                temperature=1,
                max_tokens=4000,
                top_p=1,
                stream=True
            )
            
            result_text = ""
            async for chunk in completion:
                result_text += chunk.choices[0].delta.content or ""
            
            print(f"[LLM] UT Report Generation Success!")
            return result_text
        except Exception as e:
            print(f"[LLM ERROR] Attempt {attempt + 1}/{max_retries} failed: {e}")
            if attempt == max_retries - 1:
                raise e
            await asyncio.sleep(2)
