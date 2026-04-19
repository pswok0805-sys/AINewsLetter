import os
import requests
import xml.etree.ElementTree as ET
from groq import Groq
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime

# 환경변수에서 키 가져오기
GEMINI_API_KEY = os.environ["GROQ_API_KEY"]
GMAIL_USER = os.environ["GMAIL_USER"]
GMAIL_APP_PASSWORD = os.environ["GMAIL_APP_PASSWORD"]
RECIPIENT_EMAIL = os.environ["RECIPIENT_EMAIL"]

# 뉴스 수집 (Google News RSS)
def fetch_ai_news():
    feeds = [
        "https://news.google.com/rss/search?q=AI+인공지능&hl=ko&gl=KR&ceid=KR:ko",
        "https://news.google.com/rss/search?q=ChatGPT+Claude+Gemini&hl=ko&gl=KR&ceid=KR:ko",
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

# Gemini로 요약
def summarize_with_gemini(news_text):
    client = Groq(api_key=os.environ["GROQ_API_KEY"])
    prompt = f"""
다음은 오늘의 AI 관련 뉴스 목록입니다.
한국어로 읽기 좋은 뉴스레터 형식으로 요약해주세요.
각 뉴스를 2~3줄로 요약하고, 중요도 순으로 정리해주세요.

{news_text}
"""
    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}]
    )
    return response.choices[0].message.content

# 이메일 전송
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

# 실행
if __name__ == "__main__":
    print("뉴스 수집 중...")
    news = fetch_ai_news()
    print("Gemini 요약 중...")
    summary = summarize_with_gemini(news)
    print("이메일 전송 중...")
    send_email(summary)
