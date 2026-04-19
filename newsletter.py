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
feeds = [
# 1. 국내 전반적 AI 뉴스 (산업/트렌드 중심)
"https://news.google.com/rss/search?q=\"국내+AI+산업\"+OR+\"한국+인공지능+트렌드\"&hl=ko&gl=KR&ceid=KR:ko",

# 2. 주요 AI 모델 동향 (핵심 모델명 구체화)
"https://news.google.com/rss/search?q=LLM+OR+GPT-5+OR+Claude+OR+Gemini+동향&hl=ko&gl=KR&ceid=KR:ko",

# 3. 하드웨어/인프라 트렌드 (반도체 핵심 부품 포함)
"https://news.google.com/rss/search?q=\"AI+반도체\"+OR+엔비디아+OR+HBM+OR+CXL&hl=ko&gl=KR&ceid=KR:ko",

# 4. 기술/정책 동향
"https://news.google.com/rss/search?q=AI+규제+OR+AI+윤리+OR+\"AI+정책\"+OR+OpenAI+OR+Anthropic&hl=ko&gl=KR&ceid=KR:ko",

# 5. 글로벌 빅테크 AI 동향
"https://news.google.com/rss/search?q=구글+AI+OR+마이크로소프트+AI+OR+메타+AI+OR+애플+AI&hl=ko&gl=KR&ceid=KR:ko",
]
    articles = []
    for url in feeds:
        try:
            res = requests.get(url, timeout=10)
            root = ET.fromstring(res.content)
            for item in root.findall(".//item")[:5]:
                title = item.findtext("title", "")
                link = item.findtext("link", "")
                pub_date = item.findtext("pubDate", "")
                articles.append(f"- {title} ({pub_date})\n  {link}")
        except Exception as e:
            print(f"뉴스 수집 오류: {e}")
    return "\n".join(articles)

def summarize_with_groq(news_text):
    client = Groq(api_key=GROQ_API_KEY)
    prompt = f"""
다음은 오늘의 AI 관련 뉴스 목록입니다.
한국어로 읽기 좋은 뉴스레터 형식으로 요약해주세요.
각 뉴스를 2~3줄로 요약하고, 중요도 순으로 정리해주세요.
각 뉴스 요약 마지막 줄에 반드시 원문 링크를 포함해주세요. 형식: 🔗 링크: [URL]

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
    msg["To"] = RECIPIENT_EMAIL

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
        server.sendmail(GMAIL_USER, RECIPIENT_EMAIL, msg.as_string())
    print("이메일 전송 완료!")

if __name__ == "__main__":
    print("뉴스 수집 중...")
    news = fetch_ai_news()
    print("Groq 요약 중...")
    summary = summarize_with_groq(news)
    print("이메일 전송 중...")
    send_email(summary)
