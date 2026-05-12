import os
import markdown
import tempfile
from weasyprint import HTML
from app_settings.storage_minio import minio_client, BUCKET_NAME
from datetime import timedelta

def generate_and_upload_pdf(session_id: int, llm_text: str, page_summaries: list) -> str:
    """
    LLM이 작성한 마크다운 리포트와 히트맵 이미지들을 조합하여 PDF로 생성 후 MinIO에 업로드합니다.
    """
    # 1. 마크다운을 HTML로 변환
    html_body = markdown.markdown(llm_text, extensions=['tables', 'fenced_code'])
    
    # 2. 히트맵 이미지 삽입을 위한 HTML 구성
    heatmaps_html = ""
    if page_summaries:
        heatmaps_html += "<h2>📊 페이지별 시선 히트맵</h2>"
        for p in page_summaries:
            if p.heatmap_path:
                try:
                    # 임시 접근 URL 발급 (1시간)
                    img_url = minio_client.presigned_get_object(
                        BUCKET_NAME, p.heatmap_path, expires=timedelta(hours=1)
                    )
                    heatmaps_html += f"<h3>Page {p.page_no} ({p.url})</h3>"
                    heatmaps_html += f"<img src='{img_url}' style='max-width: 100%; max-height: 400px; margin-bottom: 20px;'/>"
                except Exception as e:
                    print(f"히트맵 이미지 로드 실패 (Page {p.page_no}): {e}")

    # 3. 전체 HTML 템플릿 (구글 폰트 적용)
    full_html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <style>
            @import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;700&display=swap');
            body {{
                font-family: 'Noto Sans KR', sans-serif;
                line-height: 1.6;
                color: #333;
                padding: 20px;
            }}
            h1, h2, h3 {{
                color: #2c3e50;
            }}
            table {{
                border-collapse: collapse;
                width: 100%;
                margin-bottom: 20px;
            }}
            th, td {{
                border: 1px solid #ddd;
                padding: 8px;
                text-align: left;
            }}
            th {{
                background-color: #f2f2f2;
            }}
            img {{
                display: block;
                margin: 0 auto;
            }}
        </style>
    </head>
    <body>
        {html_body}
        <hr/>
        {heatmaps_html}
    </body>
    </html>
    """
    
    # 4. 임시 파일에 PDF 저장 후 업로드
    object_key = f"sessions/session_{session_id}/report.pdf"
    
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp_path = tmp.name
        
    try:
        # WeasyPrint 렌더링
        HTML(string=full_html).write_pdf(tmp_path)
        
        # MinIO 업로드
        minio_client.fput_object(
            BUCKET_NAME,
            object_key,
            tmp_path,
            content_type="application/pdf"
        )
        print(f"[PDF] PDF Generation & Upload Success: {object_key}")
        return object_key
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
