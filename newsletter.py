import os
import re
import requests
import xml.etree.ElementTree as ET
import smtplib
import email.utils
from datetime import datetime, timezone, timedelta
from difflib import SequenceMatcher
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from groq import Groq

GROQ_API_KEY = os.environ["GROQ_API_KEY"]
GMAIL_USER = os.environ["GMAIL_USER"]
GMAIL_APP_PASSWORD = os.environ["GMAIL_APP_PASSWORD"]
RECIPIENT_EMAIL = os.environ["RECIPIENT_EMAIL"]

# ─────────────────────────────────────────────
# [수정 1] 자주 출현하는 한자→한글 매핑 테이블
# Llama 모델이 자주 삽입하는 한자를 사전에 등록
# ─────────────────────────────────────────────
HANJA_MAP = {
    "韓國": "한국", "韓": "한국", "美國": "미국", "美": "미국",
    "中國": "중국", "中": "중국", "日本": "일본", "日": "일본",
    "人工知能": "인공지능", "知能": "지능", "技術": "기술",
    "産業": "산업", "産": "산", "企業": "기업", "業界": "업계",
    "政府": "정부", "政策": "정책", "規制": "규제", "市場": "시장",
    "更加": "더욱", "部門": "부문", "開發": "개발", "發表": "발표",
    "發": "발", "硏究": "연구", "硏": "연", "經濟": "경제",
    "金融": "금융", "投資": "투자", "戰略": "전략", "成長": "성장",
    "半導體": "반도체", "製造": "제조", "製品": "제품",
    "報告": "보고", "豫想": "예상", "現在": "현재", "今後": "향후",
}

def is_duplicate(title, existing_titles, threshold=0.8):
    """제목 유사도 기반 중복 제거"""
    for existing in existing_titles:
        ratio = SequenceMatcher(None, title, existing).ratio()
        if ratio >= threshold:
            return True
    return False


def fetch_ai_news():
    """구글 뉴스 RSS에서 AI 관련 기사 수집"""
    feeds = [
        "https://news.google.com/rss/search?q=\"국내+AI+산업\"+OR+\"한국+인공지능+트렌드\"&hl=ko&gl=KR&ceid=KR:ko",
        "https://news.google.com/rss/search?q=LLM+OR+GPT-5+OR+Claude+OR+Gemini+동향&hl=ko&gl=KR&ceid=KR:ko",
        "https://news.google.com/rss/search?q=\"AI+반도체\"+OR+엔비디아+OR+HBM+OR+CXL&hl=ko&gl=KR&ceid=KR:ko",
        "https://news.google.com/rss/search?q=AI+규제+OR+AI+윤리+OR+\"AI+정책\"+OR+OpenAI+OR+Anthropic&hl=ko&gl=KR&ceid=KR:ko",
        "https://news.google.com/rss/search?q=구글+AI+OR+마이크로소프트+AI+OR+메타+AI+OR+애플+AI&hl=ko&gl=KR&ceid=KR:ko",
        "https://news.google.com/rss/search?q=AI+latest+trends&hl=en&gl=US&ceid=US:en",
        "https://news.google.com/rss/search?q=LLM+GPT+Claude+Gemini&hl=en&gl=US&ceid=US:en",
        "https://news.google.com/rss/search?q=AI+semiconductor+NVIDIA+HBM&hl=en&gl=US&ceid=US:en",
        "https://news.google.com/rss/search?q=AI+regulation+ethics+policy&hl=en&gl=US&ceid=US:en",
    ]

    now = datetime.now(timezone.utc)
    hours = 72 if now.weekday() == 0 else 24
    cutoff = now - timedelta(hours=hours)

    seen_titles = []
    articles = []

    for url in feeds:
        try:
            res = requests.get(url, timeout=10)
            root = ET.fromstring(res.content)
            for item in root.findall(".//item")[:5]:
                title = item.findtext("title", "")
                link = item.findtext("link", "")
                pub_date = item.findtext("pubDate", "")

                if pub_date:
                    try:
                        parsed_date = email.utils.parsedate_to_datetime(pub_date)
                        if parsed_date < cutoff:
                            continue
                    except Exception:
                        pass

                if is_duplicate(title, seen_titles):
                    continue
                seen_titles.append(title)
                articles.append(f"- {title} ({pub_date})\n  {link}")
        except Exception as e:
            print(f"뉴스 수집 오류: {e}")

    print(f"총 {len(articles)}개 기사 수집됨")
    return "\n".join(articles)


# ─────────────────────────────────────────────
# [수정 2] 출력 품질 검증 함수 (다층 검사)
# ─────────────────────────────────────────────
def has_chinese(text: str) -> bool:
    """한자(중국어/일본어 한자) 감지"""
    return bool(re.search(r'[\u4e00-\u9fff\u3400-\u4dbf]', text))


def has_japanese_kana(text: str) -> bool:
    """일본어 가나 감지 (히라가나, 가타카나)"""
    return bool(re.search(r'[\u3040-\u309f\u30a0-\u30ff]', text))


def korean_ratio(text: str) -> float:
    """전체 문자 중 한글 비율 계산 (영문/숫자/공백 제외)"""
    cleaned = re.sub(r'[\s\d\W_]+', '', text, flags=re.UNICODE)
    if not cleaned:
        return 1.0
    korean_chars = re.findall(r'[\uac00-\ud7af]', cleaned)
    english_chars = re.findall(r'[a-zA-Z]', cleaned)
    total_meaningful = len(korean_chars) + len(english_chars)
    if total_meaningful == 0:
        return 0.0
    return len(korean_chars) / len(cleaned)


def is_output_valid(text: str) -> tuple[bool, str]:
    """출력 검증: 통과 여부와 사유 반환"""
    if has_chinese(text):
        return False, "한자 포함"
    if has_japanese_kana(text):
        return False, "일본어 가나 포함"
    if korean_ratio(text) < 0.4:
        return False, f"한글 비율 부족 ({korean_ratio(text):.0%})"
    return True, "정상"


def force_replace_hanja(text: str) -> str:
    """[수정 3] 사전 매핑된 한자는 강제 치환 (최후 안전장치)"""
    for hanja, hangul in HANJA_MAP.items():
        text = text.replace(hanja, hangul)
    # 그래도 남은 한자는 [?]로 표시 (메일 가독성 보호)
    text = re.sub(r'[\u4e00-\u9fff\u3400-\u4dbf]', '', text)
    return text


# ─────────────────────────────────────────────
# [수정 4] LLM 호출 - system 프롬프트 분리 + 파라미터 명시
# ─────────────────────────────────────────────
SYSTEM_PROMPT = """당신은 한국어 뉴스레터 에디터입니다.

[절대 규칙]
- 출력은 100% 한국어(한글)로만 작성합니다.
- 한자(漢字), 중국어 간체, 번체, 일본어 가나는 단 한 글자도 사용 금지입니다.
- 영문 고유명사(OpenAI, NVIDIA 등)와 숫자는 그대로 사용 가능합니다.
- 한자어는 반드시 한글 음으로 표기합니다. 예: 韓國 → 한국, 開發 → 개발, 産業 → 산업

[위반 시 결과]
- 한자가 한 글자라도 포함되면 응답은 즉시 폐기됩니다.

[출력 형식]
지정된 마크다운 형식을 정확히 따릅니다."""


def call_groq(client, system: str, user: str, temperature: float = 0.2) -> str:
    """Groq API 호출 래퍼 - 일관된 파라미터 적용"""
    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=temperature,   # [핵심] 낮은 temperature로 안정성 확보
        max_tokens=4096,
        top_p=0.9,
    )
    return response.choices[0].message.content


def summarize_with_groq(news_text: str) -> str:
    """뉴스 요약 - 검증/재시도/강제치환 3중 안전장치"""
    client = Groq(api_key=GROQ_API_KEY)

    user_prompt = f"""다음은 오늘의 AI 관련 뉴스 목록입니다.
읽기 좋은 뉴스레터 형식으로 한국어로 요약해 주세요.
외국어 뉴스는 자연스러운 한국어로 번역해 주세요.

[형식]
### 🔥 주요 뉴스
(가장 중요한 뉴스 3개)

### 📌 기술 동향
(기술/연구 관련 뉴스)

### 🌐 글로벌 동향
(해외/빅테크 관련 뉴스)

각 뉴스 항목 형식:
1. [뉴스 제목]
   📝 요약 및 해석: 핵심 내용을 초보자도 이해할 수 있게 3~4줄로 설명. 왜 중요한지, 어떤 영향이 있을지 포함.
   🔗 링크: [URL]

(뉴스 사이에는 빈 줄 한 줄)

[뉴스 원문]
{news_text}
"""

    # 1차 호출
    result = call_groq(client, SYSTEM_PROMPT, user_prompt, temperature=0.2)

    # 검증 및 재시도 (최대 3회, 단계별로 더 강하게)
    max_retries = 3
    for attempt in range(1, max_retries + 1):
        valid, reason = is_output_valid(result)
        if valid:
            print(f"✅ 출력 검증 통과 (시도 {attempt}회차)")
            break

        print(f"⚠️ 출력 검증 실패 ({reason}) → 재요청 {attempt}/{max_retries}")

        # [수정 5] 재요청 프롬프트에 구체적 예시 포함
        fix_prompt = f"""아래 텍스트에는 한자 또는 일본어가 포함되어 있어 사용할 수 없습니다.
모든 외국 문자를 한국어 한글로 바꿔서 전체 텍스트를 다시 출력하세요.

[변환 예시]
韓國 → 한국,  美國 → 미국,  中國 → 중국,  日本 → 일본
人工知能 → 인공지능,  半導體 → 반도체,  企業 → 기업,  産業 → 산업
更加 → 더욱,  開發 → 개발,  發表 → 발표,  硏究 → 연구

내용과 형식은 그대로 유지하되, 외국 문자만 한글로 바꿔서 다시 작성하세요.

[원문]
{result}
"""
        # 재시도 시 temperature를 더 낮춤
        result = call_groq(client, SYSTEM_PROMPT, fix_prompt, temperature=0.1)

    # [수정 6] 최종 안전장치: 그래도 한자가 남았으면 강제 치환
    valid, reason = is_output_valid(result)
    if not valid:
        print(f"⛑️ 재시도 모두 실패 → 강제 치환 적용 ({reason})")
        result = force_replace_hanja(result)

    return result


def send_email(content: str):
    """Gmail SMTP로 뉴스레터 발송"""
    today = datetime.now().strftime("%Y년 %m월 %d일")
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"🤖 AI 뉴스레터 {today}"
    msg["From"] = GMAIL_USER

    recipients = [r.strip() for r in RECIPIENT_EMAIL.split(",")]
    msg["To"] = ", ".join(recipients)

    html = f"""
    <html><body>
      <h2>🤖 오늘의 AI 뉴스레터</h2>
      <p>{today}</p>
      <hr>
      <pre style="font-family:'Malgun Gothic','Apple SD Gothic Neo',sans-serif; white-space:pre-wrap; line-height:1.6;">{content}</pre>
      <hr>
      <p style="color:gray; font-size:12px;">자동 발송된 뉴스레터입니다.</p>
    </body></html>
    """
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_USER, recipients, msg.as_string())
    print("이메일 전송 완료!")


if __name__ == "__main__":
    print("뉴스 수집 중...")
    news = fetch_ai_news()
    print("Groq 요약 중...")
    summary = summarize_with_groq(news)
    print("이메일 전송 중...")
    send_email(summary)
