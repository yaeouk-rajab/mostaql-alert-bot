import os
import re
import sqlite3
import time
import smtplib
from urllib.parse import quote
from email.message import EmailMessage
import requests
from bs4 import BeautifulSoup

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GMAIL_USER = os.getenv("GMAIL_USER")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
RECIPIENT_EMAIL = os.getenv("RECIPIENT_EMAIL")
CF_WORKER_URL = os.getenv("CF_WORKER_URL", "").rstrip("/")

MOSTAQL_TARGET_URL = "https://mostaql.com/projects/development"
DB_FILE = "seen_projects.db"

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

def proxied_get(target_url: str):
    """Fetches any URL via the Cloudflare Worker proxy."""
    if CF_WORKER_URL:
        endpoint = f"{CF_WORKER_URL}/?url={quote(target_url)}"
    else:
        endpoint = target_url
    return requests.get(endpoint, timeout=20)

def send_email_alert(project_data: dict, proposal_content: str):
    msg = EmailMessage()
    msg["Subject"] = f"🎯 مشروع مستقل جديد: {project_data['title']} ({project_data['hire_rate']}%)"
    msg["From"] = GMAIL_USER
    msg["To"] = RECIPIENT_EMAIL

    plain_text = f"المشروع: {project_data['title']}\nالرابط: {project_data['url']}\n\nالعرض:\n{proposal_content}"
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
        <span class="badge">معدل التوظيف: {project_data['hire_rate']}%</span>
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
        print(f"Alert sent for: {project_data['title']}")
    except Exception as e:
        print(f"Failed sending alert: {e}")

def extract_hire_rate(soup: BeautifulSoup) -> float:
    elements = soup.find_all(string=re.compile(r"معدل\s+التوظيف"))
    for elem in elements:
        parent = elem.find_parent()
        if parent:
            m = re.search(r"(\d+(?:\.\d+)?)\s*%", parent.get_text())
            if m:
                return float(m.group(1))
    return 0.0

def fetch_details(url: str):
    res = proxied_get(url)
    if res.status_code != 200:
        return None
    soup = BeautifulSoup(res.text, "html.parser")
    rate = extract_hire_rate(soup)
    if rate < 20.0:
        return None

    title_el = soup.find("h1") or soup.find("title")
    title = title_el.get_text(strip=True) if title_el else "مشروع جديد"
    desc_el = soup.find("div", class_="project__brief") or soup.find("div", class_="card-body")
    desc = desc_el.get_text(strip=True) if desc_el else ""

    questions = [q.get_text(strip=True) for q in soup.find_all("div", class_="project-question") if q.get_text(strip=True)]

    return {"url": url, "title": title, "description": desc, "hire_rate": rate, "questions": questions}

def generate_proposal(project: dict) -> str:
    prompt = f"""
أنت مبرمج ومطور محترف تتقدم لمشروع على مستقل.
اكتب عرضاً (Bid Proposal) احترافياً ومقنعاً بلغة المشروع:
العنوان: {project['title']}
التفاصيل: {project['description']}
الأسئلة: {project['questions'] if project['questions'] else 'لا يوجد'}

الشروط:
1. فهم دقيق للهدف.
2. خطة تنفيذ عملية وواضحة (Execution Plan).
3. تحديد سعر ومدة واقعيين.
4. الإجابة عن أي أسئلة للمشروع.
"""
    api_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
    res = requests.post(api_url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=30)
    data = res.json()
    return data["candidates"][0]["content"]["parts"][0]["text"]

def main():
    init_db()
    res = proxied_get(MOSTAQL_TARGET_URL)
    if res.status_code != 200:
        print(f"Failed to fetch list page: Status {res.status_code}")
        return

    soup = BeautifulSoup(res.text, "html.parser")
    for a in soup.find_all("a", href=True):
        m = re.search(r"/project/(\d+)", a["href"])
        if not m:
            continue
        pid = m.group(1)
        full_url = a["href"] if a["href"].startswith("http") else f"https://mostaql.com{a['href']}"

        if is_project_seen(pid):
            continue

        details = fetch_details(full_url)
        mark_project_seen(pid)

        if details:
            print(f"Qualified project ({details['hire_rate']}%): {details['title']}")
            proposal = generate_proposal(details)
            send_email_alert(details, proposal)
        time.sleep(1)

if __name__ == "__main__":
    main()
    
