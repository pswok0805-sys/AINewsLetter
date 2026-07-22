import os
import re
import json
import html
import requests
import xml.etree.ElementTree as ET
import smtplib
import email.utils
from datetime import datetime, timezone, timedelta
from difflib import SequenceMatcher
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from groq import Groq
from googlenewsdecoder import gnewsdecoder

GROQ_API_KEY = os.environ["GROQ_API_KEY"]
GMAIL_USER = os.environ["GMAIL_USER"]
GMAIL_APP_PASSWORD = os.environ["GMAIL_APP_PASSWORD"]
RECIPIENT_EMAIL = os.environ["RECIPIENT_EMAIL"]

# 자주 출현하는 한자→한글 매핑 (Llama 모델이 자주 삽입하는 한자를 사전에 등록)
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

# (카테고리 키, 표시 라벨, 강조색) - 순서가 곧 중복 제거 우선순위(main > tech > global)
CATEGORY_META = [
    ("main", "🔥 주요 뉴스", "#dc2626"),
    ("tech", "📌 기술 동향", "#2563eb"),
    ("global", "🌐 글로벌 동향", "#16a34a"),
]


def is_duplicate(title, existing_titles, threshold=0.8):
    """제목 유사도 기반 중복 제거 (수집 단계, 원문 제목 기준)"""
    for existing in existing_titles:
        ratio = SequenceMatcher(None, title, existing).ratio()
        if ratio >= threshold:
            return True
    return False


def decode_google_news_link(link: str) -> str:
    """구글 뉴스 리다이렉트 링크를 실제 언론사 원문 URL로 디코딩.
    실패 시(429, 타임아웃 등) 원본 링크를 그대로 반환한다."""
    try:
        result = gnewsdecoder(link)
        if result.get("status") and result.get("decoded_url"):
            return result["decoded_url"]
    except Exception:
        pass
    return link


MAX_ARTICLES = 12


def fetch_ai_news() -> list[dict]:
    """구글 뉴스 RSS에서 AI 관련 기사 수집.
    각 기사를 {id, title, source, link} 딕셔너리로 반환한다.
    링크(구글 리다이렉트 URL)는 매우 길어 LLM 프롬프트 토큰을 크게 잡아먹으므로
    LLM에게는 넘기지 않고, id로만 나중에 매칭한다."""
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
                source = item.findtext("source", "") or ""

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

                articles.append({
                    "id": len(articles) + 1,
                    "title": title,
                    "source": source,
                    "link": link,
                })
        except Exception as e:
            print(f"뉴스 수집 오류: {e}")

    if len(articles) > MAX_ARTICLES:
        print(f"⚠️ 수집 기사 {len(articles)}개 → 상한 {MAX_ARTICLES}개로 제한")
        articles = articles[:MAX_ARTICLES]

    print(f"총 {len(articles)}개 기사 수집됨")
    return articles


# ─────────────────────────────────────────────
# 출력 품질 검증 (한자/가나 감지, 한글 비율)
# ─────────────────────────────────────────────
def has_chinese(text: str) -> bool:
    """한자(중국어/일본어 한자) 감지"""
    return bool(re.search(r'[一-鿿㐀-䶿]', text))


def has_japanese_kana(text: str) -> bool:
    """일본어 가나 감지 (히라가나, 가타카나)"""
    return bool(re.search(r'[぀-ゟ゠-ヿ]', text))


def korean_ratio(text: str) -> float:
    """전체 문자 중 한글 비율 계산 (영문/숫자/공백 제외)"""
    cleaned = re.sub(r'[\s\d\W_]+', '', text, flags=re.UNICODE)
    if not cleaned:
        return 1.0
    korean_chars = re.findall(r'[가-힯]', cleaned)
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
    """사전 매핑된 한자는 강제 치환 (최후 안전장치)"""
    for hanja, hangul in HANJA_MAP.items():
        text = text.replace(hanja, hangul)
    text = re.sub(r'[一-鿿㐀-䶿]', '', text)
    return text


# ─────────────────────────────────────────────
# 카테고리 간 중복 기사 제거 (동일 사건이 다른 표현의 제목으로
# 여러 섹션에 배치되는 경우, 제목 문자열 자체가 다르면 fetch 단계의
# SequenceMatcher로는 못 걸러지므로 토큰(단어) 유사도로 한 번 더 걸러낸다)
# ─────────────────────────────────────────────
def _tokenize(title: str) -> set:
    return set(re.findall(r'[가-힣a-zA-Z0-9]+', title))


def dedupe_across_categories(categories: dict, threshold: float = 0.6, min_overlap: int = 2) -> dict:
    """우선순위(main > tech > global)가 높은 카테고리에 이미 실린 사건은 이후 카테고리에서 제외.
    "AI", "인공지능" 같은 공통 단어 한두 개만 겹쳐도 중복으로 오판하지 않도록
    Jaccard 임계값(0.6)과 최소 겹치는 단어 수(2개) 조건을 함께 요구한다."""
    kept_tokens = []
    result = {key: [] for key, _, _ in CATEGORY_META}
    for key, _, _ in CATEGORY_META:
        for article in categories.get(key, []):
            tokens = _tokenize(article.get("title", ""))
            is_dup = any(
                tokens and kt
                and len(tokens & kt) >= min_overlap
                and len(tokens & kt) / len(tokens | kt) >= threshold
                for kt in kept_tokens
            )
            if is_dup:
                continue
            kept_tokens.append(tokens)
            result[key].append(article)
    return result


SYSTEM_PROMPT = """당신은 한국어 AI 뉴스레터 에디터입니다.

[절대 규칙 - 언어]
- 출력은 100% 한국어(한글)로만 작성합니다.
- 한자(漢字), 중국어 간체/번체, 일본어 가나는 단 한 글자도 사용 금지입니다.
- 영문 고유명사(OpenAI, NVIDIA 등)와 숫자는 그대로 사용 가능합니다.
- 한자어는 반드시 한글 음으로 표기합니다. 예: 韓國 → 한국, 開發 → 개발, 産業 → 산업

[절대 규칙 - 편집]
- "병합"은 두 기사가 완전히 동일한 사건(예: 같은 회사의 같은 발표, 같은 날 같은 계약)을 다른 매체가 보도한 경우에만 적용합니다. 같은 산업/주제(예: "AI 반도체")를 다루더라도 회사·발표·사건이 다르면 절대 병합하지 말고 각각 별도 항목으로 유지합니다. 예를 들어 "AMD의 HBM4 채택" 기사와 "구글의 자체 AI 칩 개발" 기사는 둘 다 반도체 관련이지만 서로 다른 사건이므로 반드시 별도 항목입니다.
- [뉴스 원문]에 있는 기사 수가 9개 이상이면, 최종 출력(main+tech+global 합계)도 반드시 9개 이상을 포함해야 합니다. 기사 수가 9개 미만이면 원문에 있는 기사 수만큼 전부 포함합니다. 병합은 정말로 동일 사건일 때만 예외적으로 적용하고, 임의로 기사 수를 줄이지 않습니다.
- "주요 뉴스"는 게재 순서가 아니라 산업/기술적 파급력, 독점성, 최초성을 기준으로 직접 판단해 선정합니다. 단순 보도자료성 뉴스(윤리 헌장 제정 등)는 실질적 파급력이 없으면 주요 뉴스에서 제외하되, tech 또는 global 섹션으로는 반드시 포함시킵니다.
- 요약에는 다음과 같은 상투적 문구를 사용하지 않습니다: "~에 큰 영향을 미칠 수 있습니다", "~에 영향을 미칠 것으로 보입니다", "주목할 만합니다", "관심이 집중되고 있습니다".

[요약 구성 - summary와 insight를 반드시 구분]
- summary: 기사의 핵심 사실만 3~4문장으로 설명합니다. 누가, 무엇을, 언제, 얼마나(수치/일정)를 포함한 사실 위주 설명이며 해석은 넣지 않습니다.
- insight: 이 뉴스를 읽는 독자가 생각해봐야 할 함의를 3~4문장으로 씁니다. 다음 중 최소 2가지를 포함합니다: (1) 이 사건이 경쟁사/업계 구도를 어떻게 바꾸는지 구체적 비교, (2) 왜 지금 이 시점에 일어났는지 배경, (3) 단기(수개월)와 중장기(1~2년) 전망의 차이, (4) 독자(개발자/투자자/일반 소비자 등)에게 실질적으로 달라지는 점. insight는 summary의 문장을 재진술하지 않고 반드시 새로운 관점을 추가합니다.

[출력 형식 - 절대 규칙]
- 아래 JSON 스키마 외의 텍스트(설명, 코드블록 표시 등)는 절대 출력하지 않습니다. 순수 JSON 객체 하나만 출력합니다.
- 원문 각 기사에는 id 번호가 붙어 있습니다. 링크(URL)는 절대 직접 작성하지 말고, 해당 기사의 id 번호만 정확히 그대로 적습니다.

{
  "main": [ {"id": 0, "title": "...", "summary": "...", "insight": "..."} ],
  "tech": [ {"id": 0, "title": "...", "summary": "...", "insight": "..."} ],
  "global": [ {"id": 0, "title": "...", "summary": "...", "insight": "..."} ]
}

- main은 최대 3개, 나머지는 원문 기사 수에 맞춰 tech/global에 배분합니다(전체 합계는 원문 기사 수 이상 줄이지 않습니다).
- 두 기사를 병합한 경우 id는 그중 더 정보가 풍부한 기사 하나의 id를 사용합니다."""


GROQ_MODEL = "llama-3.3-70b-versatile"


def call_groq(client, system: str, user: str, temperature: float = 0.2, json_mode: bool = False) -> str:
    """Groq API 호출 래퍼 - 일관된 파라미터 적용"""
    kwargs = dict(
        model=GROQ_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=temperature,
        max_tokens=4096,
        top_p=0.9,
    )
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    response = client.chat.completions.create(**kwargs)
    return response.choices[0].message.content


def _extract_json_object(text: str) -> str:
    """모델이 코드블록 등 부가 텍스트를 덧붙인 경우를 대비해 최외곽 JSON 객체만 추출"""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        return text
    return text[start:end + 1]


def summarize_with_groq(articles: list[dict]) -> dict:
    """뉴스 요약 - 검증/재시도/강제치환 3중 안전장치 + JSON 구조화 출력.
    링크는 프롬프트에 넣지 않고 id로만 참조시켜 토큰(TPM) 사용량을 줄인다."""
    client = Groq(api_key=GROQ_API_KEY)

    article_count = len(articles)
    news_lines = "\n".join(
        f"- id={a['id']} [{a['source']}] {a['title']}" if a["source"] else f"- id={a['id']} {a['title']}"
        for a in articles
    )
    user_prompt = f"""다음은 오늘의 AI 관련 뉴스 목록입니다. 총 {article_count}개 기사가 있습니다.
지정된 JSON 형식으로만 응답하세요. main+tech+global 합계는 {article_count}개(동일 사건 병합 제외 시) 이상이어야 합니다.

[뉴스 원문]
{news_lines}
"""

    result = call_groq(client, SYSTEM_PROMPT, user_prompt, temperature=0.2, json_mode=True)

    max_retries = 3
    for attempt in range(1, max_retries + 1):
        valid, reason = is_output_valid(result)
        if valid:
            print(f"✅ 출력 검증 통과 (시도 {attempt}회차)")
            break

        print(f"⚠️ 출력 검증 실패 ({reason}) → 재요청 {attempt}/{max_retries}")

        fix_prompt = f"""아래 JSON에는 한자 또는 일본어가 포함되어 있어 사용할 수 없습니다.
모든 외국 문자를 한국어 한글로 바꿔서 동일한 JSON 구조로 다시 출력하세요.

[변환 예시]
韓國 → 한국,  美國 → 미국,  中國 → 중국,  日本 → 일본
人工知能 → 인공지능,  半導體 → 반도체,  企業 → 기업,  産業 → 산업
更加 → 더욱,  開發 → 개발,  發表 → 발표,  硏究 → 연구

JSON 키(id/title/summary/insight)와 구조는 그대로 유지하고, 문자열 값 안의 외국 문자만 한글로 바꾸세요.

[원문]
{result}
"""
        result = call_groq(client, SYSTEM_PROMPT, fix_prompt, temperature=0.1, json_mode=True)

    valid, reason = is_output_valid(result)
    if not valid:
        print(f"⛑️ 재시도 모두 실패 → 강제 치환 적용 ({reason})")
        result = force_replace_hanja(result)

    try:
        data = json.loads(_extract_json_object(result))
    except json.JSONDecodeError as e:
        print(f"⚠️ JSON 파싱 실패, 빈 뉴스레터로 대체: {e}")
        data = {}

    for key, _, _ in CATEGORY_META:
        data.setdefault(key, [])

    link_by_id = {a["id"]: a["link"] for a in articles}
    for key, _, _ in CATEGORY_META:
        for item in data[key]:
            item["link"] = link_by_id.get(item.get("id"), "")

    return dedupe_across_categories(data)


def decode_links_in_categories(categories: dict) -> dict:
    """최종 게재 대상 기사(보통 10개 이하)에 한해 구글 뉴스 링크를 원문 URL로 디코딩.
    전체 수집 단계(최대 12개)에서 디코딩하면 무료 API 호출량이 과도해지므로
    요약·중복 제거가 끝난 뒤 이 시점에만 수행한다."""
    for key, _, _ in CATEGORY_META:
        for article in categories.get(key, []):
            article["link"] = decode_google_news_link(article.get("link", ""))
    return categories


def build_email_html(categories: dict, today: str) -> str:
    """카테고리별 카드 레이아웃 HTML 생성"""
    section_html = ""
    for key, label, color in CATEGORY_META:
        articles = categories.get(key, [])
        if not articles:
            continue

        section_html += f"""
        <tr><td style="background:{color}; color:#ffffff; font-size:16px; font-weight:700; padding:12px 20px;">{html.escape(label)}</td></tr>
        """
        for i, article in enumerate(articles, 1):
            title = html.escape(article.get("title", ""))
            summary = html.escape(article.get("summary", ""))
            insight = html.escape(article.get("insight", ""))
            link = html.escape(article.get("link", "#"), quote=True)
            insight_html = f"""
          <div style="font-size:12px; color:#6b7280; font-weight:700; margin-bottom:4px;">💡 왜 중요한가</div>
          <div style="font-size:13px; color:#333333; line-height:1.6; margin-bottom:10px; padding:10px 12px; background:#f9fafb; border-left:3px solid {color}; border-radius:4px;">{insight}</div>
            """ if insight else ""
            section_html += f"""
        <tr><td style="padding:16px 20px; border-bottom:1px solid #eeeeee;">
          <div style="font-size:15px; font-weight:600; color:#111111; margin-bottom:6px;">{i}. {title}</div>
          <div style="font-size:13px; color:#444444; line-height:1.6; margin-bottom:10px;">{summary}</div>
          {insight_html}
          <a href="{link}" style="display:inline-block; font-size:12px; color:#ffffff; background:{color}; padding:6px 14px; border-radius:4px; text-decoration:none;">기사 보기 →</a>
        </td></tr>
            """

    return f"""
    <html>
    <body style="margin:0; padding:0; background:#f4f4f4;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f4f4f4; padding:24px 0;">
        <tr><td align="center">
          <table role="presentation" width="600" cellpadding="0" cellspacing="0" style="background:#ffffff; border-radius:8px; overflow:hidden; font-family:'Malgun Gothic','Apple SD Gothic Neo',sans-serif;">
            <tr><td style="background:#111111; color:#ffffff; padding:24px 20px;">
              <div style="font-size:20px; font-weight:700;">🤖 오늘의 AI 뉴스레터</div>
              <div style="font-size:13px; color:#cccccc; margin-top:4px;">{today}</div>
            </td></tr>
            {section_html}
            <tr><td style="padding:16px 20px; color:#999999; font-size:11px; text-align:center;">자동 발송된 뉴스레터입니다.</td></tr>
          </table>
        </td></tr>
      </table>
    </body>
    </html>
    """


def send_email(categories: dict):
    """Gmail SMTP로 뉴스레터 발송"""
    today = datetime.now().strftime("%Y년 %m월 %d일")
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"🤖 AI 뉴스레터 {today}"
    msg["From"] = GMAIL_USER

    recipients = [r.strip() for r in RECIPIENT_EMAIL.split(",")]
    msg["To"] = ", ".join(recipients)

    msg.attach(MIMEText(build_email_html(categories, today), "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_USER, recipients, msg.as_string())
    print("이메일 전송 완료!")


def send_failure_notice(error: Exception):
    """파이프라인 실패 시에도 받는사람이 상태를 알 수 있도록 최소 안내 메일 발송"""
    today = datetime.now().strftime("%Y년 %m월 %d일")
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"⚠️ AI 뉴스레터 생성 실패 {today}"
    msg["From"] = GMAIL_USER
    recipients = [r.strip() for r in RECIPIENT_EMAIL.split(",")]
    msg["To"] = ", ".join(recipients)
    body = f"오늘({today}) 뉴스레터 생성 중 오류가 발생해 발송하지 못했습니다.\n\n오류 내용: {error}"
    msg.attach(MIMEText(body, "plain"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_USER, recipients, msg.as_string())


if __name__ == "__main__":
    try:
        print("뉴스 수집 중...")
        news = fetch_ai_news()
        print("Groq 요약 중...")
        summary = summarize_with_groq(news)
        print("원문 링크 디코딩 중...")
        summary = decode_links_in_categories(summary)
        print("이메일 전송 중...")
        send_email(summary)
    except Exception as e:
        print(f"❌ 파이프라인 실패: {e}")
        send_failure_notice(e)
        raise
