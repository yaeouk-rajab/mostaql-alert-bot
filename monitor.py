import os
import re
import sqlite3
import time
import smtplib
from email.message import EmailMessage
import xml.etree.ElementTree as ET
import requests
from bs4 import BeautifulSoup
from curl_cffi import requests as cffi_requests

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GMAIL_USER = os.getenv("GMAIL_USER")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
RECIPIENT_EMAIL = os.getenv("RECIPIENT_EMAIL")

MOSTAQL_RSS_URL = "https://mostaql.com/rss?category=development"
DB_FILE = "seen_projects.db"

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ar,en-US;q=0.9,en;q=0.8",
}

session = cffi_requests.Session(impersonate="chrome124")

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("CREATE TABLE IF NOT EXISTS projects (project_id TEXT PRIMARY KEY)")
    conn.commit()
    conn.close()

def is_project_seen(project_id: str) -> bool:
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT 1 FROM projects WHERE project_id = ?", (project_id,))
    seen = c.fetchone() is not None
    conn.close()
    return seen

def mark_project_seen(project_id: str):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO projects (project_id) VALUES (?)", (project_id,))
    conn.commit()
    conn.close()

def send_email_alert(project_data: dict, proposal_content: str):
    msg = EmailMessage()
    msg["Subject"] = f"🎯 مشروع مستقل جديد: {project_data['title']} ({project_data.get('hire_rate', 'غير محدد')}%)"
    msg["From"] = GMAIL_USER
    msg["To"] = RECIPIENT_EMAIL

    plain_text = f"المشروع: {project_data['title']}\nالرابط: {project_data['url']}\n\nالعرض المقترح:\n{proposal_content}"
    msg.set_content(plain_text)

    html_content = f"""
    <!DOCTYPE html>
    <html dir="rtl" lang="ar">
    <head><meta charset="utf-8">
      <style>
        body {{ font-family: -apple-system, sans-serif; background: #f8fafc; padding: 16px; margin: 0; }}
        .card {{ background: #fff; border-radius: 8px; border: 1px solid #e2e8f0; max-width: 600px; margin: auto; padding: 20px; direction: rtl; }}
        .badge {{ display: inline-block; padding: 4px 10px; border-radius: 999px; background: #e0f2fe; color: #0369a1; font-weight: bold; }}
        .btn {{ display: inline-block; background-color: #2563eb; color: #fff !important; padding: 10px 18px; border-radius: 6px; text-decoration: none; font-weight: bold; margin-top: 14px; }}
        .box {{ background: #f1f5f9; border-radius: 6px; padding: 14px; margin-top: 16px; white-space: pre-wrap; line-height: 1.6; text-align: right; }}
      </style>
    </head>
    <body>
      <div class="card">
        <span class="badge">معدل التوظيف: {project_data.get('hire_rate', 'غير محدد')}%</span>
        <h2>{project_data['title']}</h2>
        <a href="{project_data['url']}" class="btn" target="_blank">فتح المشروع على مستقل</a>
        <h3 style="margin-top: 20px;">الخطة والعرض المقترح:</h3>
        <div class="box">{proposal_content}</div>
      </div>
    </body>
    </html>
    """
    msg.add_alternative(html_content, subtype="html")

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
            server.send_message(msg)
        print(f"[✓] Alert sent: {project_data['title']}")
    except Exception as e:
        print(f"[X] Failed sending email: {e}")

def extract_hire_rate(soup: BeautifulSoup) -> float:
    elements = soup.find_all(string=re.compile(r"معدل\s+التوظيف"))
    for elem in elements:
        parent = elem.find_parent()
        if parent:
            m = re.search(r"(\d+(?:\.\d+)?)\s*%", parent.get_text())
            if m:
                return float(m.group(1))
    return 0.0

def fetch_project_page_details(url: str, fallback_desc: str):
    try:
        res = session.get(url, headers=BROWSER_HEADERS, timeout=15)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, "html.parser")
            rate = extract_hire_rate(soup)
            
            desc_el = soup.find("div", class_="project__brief") or soup.find("div", class_="card-body")
            desc = desc_el.get_text(strip=True) if desc_el else fallback_desc
            
            questions = [q.get_text(strip=True) for q in soup.find_all("div", class_="project-question") if q.get_text(strip=True)]
            return rate, desc, questions
    except Exception as e:
        print(f"Project page request error: {e}")

    # إذا تعذر جلب صفحة المشروع بسبب الحظر، نعتمد على وصف الـ RSS
    return 100.0, fallback_desc, []

def generate_proposal(title: str, description: str, questions: list) -> str:
    prompt = f"""
أنت مبرمج ومطور محترف تتقدم لمشروع على منصة مستقل.
اكتب عرضاً (Bid Proposal) احترافياً ومقنعاً بلغة المشروع:

العنوان: {title}
التفاصيل: {description}
الأسئلة الإضافية: {questions if questions else 'لا يوجد'}

الشروط:
1. فهم دقيق للهدف المطلوب.
2. خطة تنفيذ عملية ومقسمة على مراحل (Execution Plan).
3. تحديد سعر ومدة واقعيين.
4. إجابات محددة ومقنعة على الأسئلة إن وجدت.
"""
    api_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
    res = requests.post(api_url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=30)
    data = res.json()
    return data["candidates"][0]["content"]["parts"][0]["text"]

def fetch_rss_feed():
    try:
        # RSS endpoints are often served without Cloudflare anti-bot blocks
        res = session.get(MOSTAQL_RSS_URL, headers=BROWSER_HEADERS, timeout=15)
        if res.status_code != 200:
            print(f"Could not load Mostaql RSS feed (HTTP {res.status_code})")
            return []
        
        root = ET.fromstring(res.content)
        items = []
        for item in root.findall(".//item"):
            link = item.findtext("link", "").strip()
            title = item.findtext("title", "").strip()
            desc = item.findtext("description", "").strip()
            
            # Clean HTML from RSS description
            clean_desc = BeautifulSoup(desc, "html.parser").get_text(strip=True)
            
            m = re.search(r"/project/(\d+)", link)
            if m:
                items.append({
                    "id": m.group(1),
                    "url": link,
                    "title": title,
                    "description": clean_desc
                })
        return items
    except Exception as e:
        print(f"Error parsing RSS: {e}")
        return []

def main():
    init_db()
    projects = fetch_rss_feed()
    print(f"Found {len(projects)} projects in RSS feed.")

    for p in projects:
        pid = p["id"]
        if is_project_seen(pid):
            continue

        hire_rate, full_desc, questions = fetch_project_page_details(p["url"], p["description"])
        mark_project_seen(pid)

        # فلترة معدل التوظيف 20%
        if hire_rate >= 20.0:
            print(f"[+] Processing new project ({hire_rate}%): {p['title']}")
            project_data = {
                "url": p["url"],
                "title": p["title"],
                "hire_rate": hire_rate
            }
            proposal = generate_proposal(p["title"], full_desc, questions)
            send_email_alert(project_data, proposal)
        else:
            print(f"[-] Skipped project (hire rate {hire_rate}% < 20%): {p['title']}")

        time.sleep(2)

if __name__ == "__main__":
    main()
