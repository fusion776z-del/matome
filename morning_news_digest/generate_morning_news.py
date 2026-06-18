#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from html import escape, unescape
from pathlib import Path

JST = timezone(timedelta(hours=9))
BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output"
HTML_PATH = OUTPUT_DIR / "morning_news.html"

DEFAULT_CONFIG = {
    "page_title": "自分用・朝のニュースまとめ",
    "max_articles": 40,
    "top_pick_count": 5,
    "max_articles_per_topic": 5,
    "feeds": [
        {
            "name": "NHK",
            "url": "https://news.web.nhk/n-data/conf/na/rss/cat0.xml"
        }
    ],
}

# ========= RSS =========
def fetch(url):
    with urllib.request.urlopen(url, timeout=10) as r:
        return r.read()

def parse_feed(xml):
    root = ET.fromstring(xml)
    items = root.findall(".//item")
    res = []
    for i in items:
        title = i.findtext("title")
        link = i.findtext("link")
        desc = i.findtext("description") or ""
        if title and link:
            res.append({
                "title": title.strip(),
                "url": link.strip(),
                "description": re.sub("<.*?>", "", desc)
            })
    return res

# ========= AI =========
def build_prompt(articles):
    lines = []
    for i,a in enumerate(articles,1):
        lines.append(f"""
記事{i}:
タイトル: {a['title']}
概要: {a['description']}
""")

    return f"""
以下の記事を要約してください。JSONのみ。

{{
 "headline": "",
 "summary": "",
 "why": "",
}}

条件:
- summary = 事実のみ
- why = 「誰に」「何が」「どう変わるか」を必ず含める
- 抽象表現禁止（重要です等）
- 不明なら "" にする

{chr(10).join(lines)}
"""

def call_ai(prompt):
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        return {"headline":"","summary":"","why":""}

    from openai import OpenAI
    client = OpenAI(api_key=key)

    r = client.responses.create(
        model="gpt-4.1-mini",
        input=prompt
    )

    txt = r.output_text.strip()
    try:
        start = txt.find("{")
        end = txt.rfind("}")
        return json.loads(txt[start:end+1])
    except:
        return {"headline":"","summary":"","why":""}

# ========= HTML =========
def render_card(a):
    why = ""
    if a.get("why"):
        why = f"<p><b>影響:</b> {escape(a['why'])}</p>"

    return f"""
    <div class="card">
      <h3>{escape(a['headline'])}</h3>
      <p>{escape(a['summary'])}</p>
      {why}
    </div>
    """

def css():
    return """
    body{font-family:sans-serif;background:#f5f5f5;margin:0}
    .app{display:flex}
    aside{width:200px;padding:20px;background:#fff}
    main{flex:1;padding:20px}
    .card{background:#fff;padding:15px;margin:10px 0;border-radius:8px}
    .nav-button{display:block;margin:6px 0;cursor:pointer}
    .floating-actions{position:fixed;right:20px;bottom:20px;display:flex;gap:10px}
    """

def render_html(cards):
    return f"""
<html>
<head><style>{css()}</style></head>
<body>

<div class="app">
<aside>
<h2>🗞️ News</h2>

<a href="#top">🏠 TOP</a>
<br>

<button class="nav-button" onclick="location.reload()">🔄 更新</button>
<a class="nav-button" href="#top">⬆️ TOPに戻る</a>

</aside>

<main id="top">
{''.join(cards)}
</main>
</div>

<div class="floating-actions">
<button onclick="location.reload()">🔄</button>
<a href="#top">⬆️</a>
</div>

</body>
</html>
"""

# ========= main =========
def main():
    cfg = DEFAULT_CONFIG
    all_articles = []

    for f in cfg["feeds"]:
        xml = fetch(f["url"])
        all_articles += parse_feed(xml)

    all_articles = all_articles[:cfg["max_articles"]]

    cards = []
    for a in all_articles[:cfg["top_pick_count"]]:
        prompt = build_prompt([a])
        ai = call_ai(prompt)
        cards.append(render_card(ai))

    OUTPUT_DIR.mkdir(exist_ok=True)
    HTML_PATH.write_text(render_html(cards), encoding="utf-8")

    print("done:", HTML_PATH)

if __name__ == "__main__":
    main()
