# summarizer/gpt_summarizer.py

import openai
import os
from dotenv import load_dotenv

load_dotenv()
openai.api_key = os.getenv("OPENAI_API_KEY")

def summarize_notice(content: str) -> str:
    if not content or len(content.strip()) < 100:
        return "📭 공고 내용이 부족하여 요약할 수 없습니다."

    prompt = f"""
다음은 정부 사업 공고 내용입니다. 아래 항목에 맞춰 핵심 정보를 요약해 주세요:

1. 📌 사업 목적
2. 👤 지원 대상
3. 📅 공고 기간 (접수 시작일 ~ 마감일)
4. 💰 예산 규모 (있다면)
5. 📝 신청 방법 및 제출 서류

공고 원문:
\"\"\"
{content}
\"\"\"

요약 결과:
"""

    try:
        response = openai.ChatCompletion.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        return f"[GPT 요약 실패] {e}"
