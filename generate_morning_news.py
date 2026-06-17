#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# RSSを取得してNotion/OneNote風HTMLに流し込む個人用スクリプト。標準ライブラリのみ。
from __future__ import annotations
import json, re, sqlite3, sys, urllib.request, xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from html import escape, unescape
from pathlib import Path
from urllib.error import HTTPError, URLError

JST = timezone(timedelta(hours=9))
BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / 'output'
FEEDS_PATH = BASE_DIR / 'feeds.json'
DB_PATH = BASE_DIR / 'articles.db'
HTML_PATH = OUTPUT_DIR / 'morning_news.html'

DEFAULT_CONFIG = {
    'page_title': '自分用・朝のニュースまとめ',
    'max_articles': 20,
    'top_pick_count': 3,
    'request_timeout_seconds': 15,
    'keywords': ['AI', '生成AI', 'Microsoft', '半導体', '北海道', '函館'],
    'exclude_keywords': ['芸能ゴシップ', '占い'],
    'category_rules': {
        'AI・テック': ['AI', '生成AI', 'LLM', 'Microsoft', 'OpenAI', '半導体', 'クラウド', 'サイバー', 'セキュリティ'],
        'ビジネス': ['経済', '市場', '株', '決算', '企業', '為替', '金利', '日銀'],
        '北海道・函館': ['北海道', '函館', '札幌', '道南', '渡島', '檜山'],
        '国内': ['政府', '国会', '選挙', '首相', '省', '庁', '自治体']
    },
    'feeds': [{'name': 'NHKニュース', 'url': 'https://www.nhk.or.jp/rss/news/cat0.xml', 'category': '国内', 'trust_score': 15}]
}

def ensure_config():
    if not FEEDS_PATH.exists():
        FEEDS_PATH.write_text(json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f'feeds.json がなかったため、サンプルを作成しました: {FEEDS_PATH}')
    with FEEDS_PATH.open('r', encoding='utf-8') as f:
        user_config = json.load(f)
    merged = DEFAULT_CONFIG.copy()
    merged.update(user_config)
    return merged

def normalize_text(text):
    text = unescape(text or '')
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

def parse_date(value):
    if not value:
        return datetime.now(JST)
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(JST)
    except Exception:
        try:
            return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(JST)
        except Exception:
            return datetime.now(JST)

def fetch_url(url, timeout):
    req = urllib.request.Request(url, headers={'User-Agent': 'MorningNewsDigest/1.0 (+personal-use)', 'Accept': 'application/rss+xml, application/xml, text/xml, */*'})
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return res.read()

def find_child_text(item, names):
    wanted = set(names)
    for child in list(item):
        local_name = child.tag.split('}')[-1]
        if local_name in wanted:
            if local_name == 'link' and child.attrib.get('href'):
                return child.attrib.get('href', '')
            return child.text or ''
    return ''

def classify_category(title, description, default, config):
    text = f'{title} {description}'.lower()
    for category, words in config.get('category_rules', {}).items():
        if any(word.lower() in text for word in words):
            return category
    return default or '未分類'

def score_article(title, description, category, published_dt, feed, config):
    text = f'{title} {description}'.lower()
    score = 30
    score += sum(15 for kw in config.get('keywords', []) if kw.lower() in text)
    score -= sum(40 for kw in config.get('exclude_keywords', []) if kw.lower() in text)
    if category in {'AI・テック', '北海道・函館'}:
        score += 10
    score += int(feed.get('trust_score', 0))
    age_hours = max(0, (datetime.now(JST) - published_dt).total_seconds() / 3600)
    if age_hours <= 6:
        score += 20
    elif age_hours <= 24:
        score += 10
    elif age_hours <= 72:
        score += 3
    return max(0, min(100, score))

def make_simple_summary(title, description, category):
    desc = re.sub(r'\s+', ' ', description.strip())
    summary = (desc[:140] + '…') if len(desc) > 140 else desc
    if not summary:
        summary = f'「{title}」に関するニュースです。'
    why_map = {
        'AI・テック': '情報収集・仕事の自動化・技術トレンドに影響する可能性があります。',
        'ビジネス': '市場や企業活動の変化として、仕事や生活コストに関係する可能性があります。',
        '北海道・函館': '地域の生活・移動・イベント・行政情報として確認する価値があります。',
        '国内': '国内情勢や制度変更に関係する可能性があります。'
    }
    return summary, why_map.get(category, '関心キーワードや生活・仕事への関連度を確認する価値があります。')

def parse_feed(xml_bytes, feed, config):
    root = ET.fromstring(xml_bytes)
    items = root.findall('.//item')
    if not items:
        items = [elem for elem in root.iter() if elem.tag.split('}')[-1] == 'entry']
    articles = []
    for item in items:
        title = normalize_text(find_child_text(item, ['title']))
        url = normalize_text(find_child_text(item, ['link']))
        description = normalize_text(find_child_text(item, ['description', 'summary', 'content', 'encoded']))
        pub_raw = normalize_text(find_child_text(item, ['pubDate', 'published', 'updated', 'date']))
        if not title or not url:
            continue
        published_dt = parse_date(pub_raw)
        category = classify_category(title, description, feed.get('category', '未分類'), config)
        score = score_article(title, description, category, published_dt, feed, config)
        summary, why = make_simple_summary(title, description, category)
        articles.append({'title': title, 'url': url, 'source': feed.get('name', 'Unknown'), 'category': category, 'published_at': published_dt.isoformat(timespec='minutes'), 'description': description, 'summary': summary, 'why': why, 'score': score})
    return articles

def dedupe_articles(articles):
    by_key = {}
    for a in articles:
        title_key = re.sub(r'\W+', '', a['title'].lower())[:80]
        key = a['url'].strip() or title_key
        if key not in by_key or a['score'] > by_key[key]['score']:
            by_key[key] = a
    return list(by_key.values())

def save_articles(articles):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS articles (url TEXT PRIMARY KEY, title TEXT NOT NULL, source TEXT, category TEXT, published_at TEXT, description TEXT, summary TEXT, why_it_matters TEXT, importance_score INTEGER, inserted_at TEXT NOT NULL)')
        now = datetime.now(JST).isoformat(timespec='seconds')
        conn.executemany('INSERT OR REPLACE INTO articles VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', [(a['url'], a['title'], a['source'], a['category'], a['published_at'], a['description'], a['summary'], a['why'], a['score'], now) for a in articles])

def score_class(score):
    return 'score' if score >= 80 else 'score mid' if score >= 60 else 'score low'

def render_article_card(a):
    return f'''<article class="news"><div class="title">{escape(a['title'])}</div><div class="why"><strong>何が起きたか:</strong> {escape(a['summary'])}</div><div class="why"><strong>なぜ重要か:</strong> {escape(a['why'])}</div><div class="source">出典: {escape(a['source'])} / カテゴリ: {escape(a['category'])} / 重要度 <span class="{score_class(a['score'])}">{a['score']}</span> / <a href="{escape(a['url'])}" target="_blank" rel="noopener noreferrer">記事を開く</a></div></article>'''
#pipeline,#pipeline+.card{display:none}
def css():
    return ':root{--bg:#f7f5f0;--paper:#fffefb;--ink:#252525;--muted:#6b665f;--line:#e6e0d6;--accent:#4f7cff;--green:#2f8f5b;--yellow:#b58900;--red:#c64545}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Hiragino Sans","Yu Gothic UI",Meiryo,sans-serif;line-height:1.7}.app{display:grid;grid-template-columns:280px 1fr;min-height:100vh}aside{padding:28px 20px;border-right:1px solid var(--line);background:#fffefb;position:sticky;top:0;height:100vh}main{padding:40px clamp(22px,5vw,72px)}.page{max-width:1040px;margin:0 auto}.hero,.card{background:#fffefb;border:1px solid var(--line);border-radius:22px;padding:24px;box-shadow:0 10px 30px rgba(39,33,25,.08);margin-bottom:16px}.nav-item{display:block;padding:8px 10px;color:#38332e;text-decoration:none;border-radius:10px}.nav-item:hover{background:#f0ede6}.pill{display:inline-block;margin:4px;padding:6px 10px;border-radius:999px;background:#f3f0e9;border:1px solid var(--line);font-size:13px}.grid{display:grid;grid-template-columns:repeat(12,1fr);gap:16px}.span-4{grid-column:span 4}.span-8{grid-column:span 8}.span-12{grid-column:span 12}.step{padding:14px;border:1px solid var(--line);border-radius:15px;background:#fffdf7;margin:10px 0}.arrow{text-align:center;color:var(--muted);font-size:20px}.news{border-left:4px solid var(--accent);padding:12px 14px;background:#fffdf7;border-radius:12px;border:1px solid var(--line);margin:10px 0}.title{font-weight:800}.why{margin-top:6px}.source{font-size:12px;color:var(--muted);margin-top:8px}.score{font-weight:800;color:var(--green)}.score.mid{color:var(--yellow)}.score.low{color:var(--red)}@media(max-width:900px){.app{grid-template-columns:1fr}aside{position:static;height:auto;border-right:none;border-bottom:1px solid var(--line)}.span-4,.span-8{grid-column:span 12}}'

def render_html(articles, config):
    now_label = datetime.now(JST).strftime('%Y年%m月%d日 %H:%M')
    max_articles = int(config.get('max_articles', 20))
    top_pick_count = int(config.get('top_pick_count', 3))
    articles = sorted(articles, key=lambda x: x['score'], reverse=True)[:max_articles]
    top_cards = '\n'.join(render_article_card(a) for a in articles[:top_pick_count]) or '<p>記事がありません。</p>'
    cats = ['国内', 'AI・テック', 'ビジネス', '北海道・函館', '未分類']
    sections = []
    all_cats = cats + sorted({a['category'] for a in articles if a['category'] not in set(cats)})
    for cat in all_cats:
        items = [a for a in articles if a['category'] == cat]
        if items:
            sections.append(f'<section class="card span-12"><h3>{escape(cat)}</h3>' + ''.join(render_article_card(a) for a in items) + '</section>')
    title = escape(config.get('page_title', '自分用・朝のニュースまとめ'))
    return f'''<!doctype html><html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>{title}</title><style>{css()}</style></head><body><div class="app"><aside><h2>🗞️ Morning Digest</h2><a class="nav-item" href="#overview">🏠 概要</a><a class="nav-item" href="#pipeline">🔁 パイプライン</a><a class="nav-item" href="#digest">☕ 今日の朝刊</a></aside><main><section class="page" id="overview"><div class="hero"><div style="font-size:46px">☕</div><h1>{title}</h1><p>RSSから記事を取得し、Notion/OneNote風HTMLとして出力しました。</p><span class="pill">📅 生成日時: {escape(now_label)}</span><span class="pill">📰 表示記事: {len(articles)}件</span></div><div class="card" id="pipeline"><div class="step">1. RSS / News API</div><div class="arrow">↓</div><div class="step">2. 記事収集</div><div class="arrow">↓</div><div class="step">3. 重複除去・カテゴリ分類</div><div class="arrow">↓</div><div class="step">4. 重要度スコアリング</div><div class="arrow">↓</div><div class="step">5. AI要約</div><div class="arrow">↓</div><div class="step">6. 毎朝配信</div></div><h2 id="digest">今日押さえるべき{top_pick_count}つ</h2><div class="card">{top_cards}</div><h2>カテゴリ別ニュース</h2>{''.join(sections)}</section></main></div></body></html>'''

def main():
    config = ensure_config()
    all_articles = []
    for feed in config.get('feeds', []):
        name, url = feed.get('name', 'Unknown'), feed.get('url')
        if not url:
            print(f'[skip] {name}: URLがありません')
            continue
        try:
            print(f'[fetch] {name}: {url}')
            articles = parse_feed(fetch_url(url, int(config.get('request_timeout_seconds', 15))), feed, config)
            print(f'       -> {len(articles)}件')
            all_articles.extend(articles)
        except (HTTPError, URLError, TimeoutError) as e:
            print(f'[error] {name}: 取得に失敗しました: {e}', file=sys.stderr)
        except ET.ParseError as e:
            print(f'[error] {name}: XML/RSSの解析に失敗しました: {e}', file=sys.stderr)
        except Exception as e:
            print(f'[error] {name}: 予期しないエラー: {e}', file=sys.stderr)
    deduped = sorted(dedupe_articles(all_articles), key=lambda x: x['score'], reverse=True)
    save_articles(deduped)
    OUTPUT_DIR.mkdir(exist_ok=True)
    HTML_PATH.write_text(render_html(deduped, config), encoding='utf-8')
    print(f'\n完了: {len(all_articles)}件取得 / {len(deduped)}件をHTMLに出力')
    print(f'HTML: {HTML_PATH}')
    print(f'DB:   {DB_PATH}')

if __name__ == '__main__':
    main()
