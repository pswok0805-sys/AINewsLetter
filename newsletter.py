import os
import requests
import xml.etree.ElementTree as ET
import smtplib
from groq import Groq
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime

GROQ_API_KEY = os.environ["GROQ_API_KEY"]
GMAIL_USER = os.environ["GMAIL_USER"]
GMAIL_APP_PASSWORD = os.environ["GMAIL_APP_PASSWORD"]
RECIPIENT_EMAIL = os.environ["RECIPIENT_EMAIL"]

def fetch_ai_news():
    from datetime import timezone, timedelta
    import email.utils

    feeds = [
        # 한국어 피드
        "https://news.google.com/rss/search?q=\"국내+AI+산업\"+OR+\"한국+인공지능+트렌드\"&hl=ko&gl=KR&ceid=KR:ko",
        "https://news.google.com/rss/search?q=LLM+OR+GPT-5+OR+Claude+OR+Gemini+동향&hl=ko&gl=KR&ceid=KR:ko",
        "https://news.google.com/rss/search?q=\"AI+반도체\"+OR+엔비디아+OR+HBM+OR+CXL&hl=ko&gl=KR&ceid=KR:ko",
        "https://news.google.com/rss/search?q=AI+규제+OR+AI+윤리+OR+\"AI+정책\"+OR+OpenAI+OR+Anthropic&hl=ko&gl=KR&ceid=KR:ko",
        "https://news.google.com/rss/search?q=구글+AI+OR+마이크로소프트+AI+OR+메타+AI+OR+애플+AI&hl=ko&gl=KR&ceid=KR:ko",
        # 영어 피드
        "https://news.google.com/rss/search?q=AI+latest+trends&hl=en&gl=US&ceid=US:en",
        "https://news.google.com/rss/search?q=LLM+GPT+Claude+Gemini&hl=en&gl=US&ceid=US:en",
        "https://news.google.com/rss/search?q=AI+semiconductor+NVIDIA+HBM&hl=en&gl=US&ceid=US:en",
        "https://news.google.com/rss/search?q=AI+regulation+ethics+policy&hl=en&gl=US&ceid=US:en",
    ]

    # 월요일이면 72시간, 나머지는 24시간
    now = datetime.now(timezone.utc)
    hours = 72 if now.weekday() == 0 else 24
    cutoff = now - timedelta(hours=hours)

    articles = []
    for url in feeds:
        try:
            res = requests.get(url, timeout=10)
            root = ET.fromstring(res.content)
            for item in root.findall(".//item")[:5]:
                title = item.findtext("title", "")
                link = item.findtext("link", "")
                pub_date = item.findtext("pubDate", "")
                # 날짜 필터링
                if pub_date:
                    try:
                        parsed_date = email.utils.parsedate_to_datetime(pub_date)
                        if parsed_date < cutoff:
                            continue  # 오래된 기사 제외
                    except:
                        pass
                articles.append(f"- {title} ({pub_date})\n  {link}")
        except Exception as e:
            print(f"뉴스 수집 오류: {e}")

    print(f"총 {len(articles)}개 기사 수집됨")
    return "\n".join(articles)

def summarize_with_groq(news_text):
    client = Groq(api_key=GROQ_API_KEY)
    prompt = f"""
다음은 오늘의 AI 관련 뉴스 목록입니다.
읽기 좋은 뉴스레터 형식으로 요약해주세요.
만약 외국어 뉴스라면 한국어로 자연스럽게 번역해주세요.
한자는 절대 없어야합니다.
아래 형식으로 작성해주세요:

### 🔥 주요 뉴스
(가장 중요한 뉴스 3개)

### 📌 기술 동향
(기술/연구 관련 뉴스)

### 🌐 글로벌 동향
(해외/빅테크 관련 뉴스)

각 뉴스는 아래 형식으로 작성해주세요:

1. [뉴스 제목]
   📝 요약 및 해석: 핵심 내용 요약과 AI나 기술을 모르는 초보자도 이해할 수 있도록 쉬운 말로 2~3줄 설명
   🔗 링크: [URL]
   
{news_text}
"""
    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}]
    )
    return response.choices[0].message.content

def send_email(content):
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
    <pre style="font-family:sans-serif; white-space:pre-wrap;">{content}</pre>
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
