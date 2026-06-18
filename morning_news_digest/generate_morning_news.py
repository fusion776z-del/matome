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
from email.utils import parsedate_to_datetime
from html import escape, unescape
from pathlib import Path
from urllib.error import HTTPError, URLError

JST = timezone(timedelta(hours=9))
BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output"
FEEDS_PATH = BASE_DIR / "feeds.json"
DB_PATH = BASE_DIR / "articles.db"
HTML_PATH = OUTPUT_DIR / "morning_news.html"

ELLIPSIS_SUFFIXES = ("…", "...", "⋯")
SENTENCE_ENDINGS = ("。", "！", "？", ".", "!", "?")

DEFAULT_CONFIG = {
    "page_title": "自分用・朝のニュースまとめ",
    "max_articles": 40,
    "top_pick_count": 5,
    "max_topic_groups": 12,
    "max_articles_per_topic": 5,
    "request_timeout_seconds": 15,
    "ai_summary_enabled": True,
    "aggregate_summary_enabled": True,
    "ai_model": "gpt-4.1-mini",
    "keywords": ["AI", "生成AI", "Microsoft", "OpenAI", "半導体", "北海道", "函館", "経済", "物価", "セキュリティ", "クラウド"],
    "exclude_keywords": ["芸能ゴシップ", "占い"],
    "category_rules": {
        "AI・テック": ["AI", "生成AI", "LLM", "Microsoft", "OpenAI", "Gemini", "Claude", "半導体", "クラウド", "サイバー", "セキュリティ", "データセンター", "ロボット", "スマートフォン", "Android", "iPhone", "アプリ", "IT"],
        "ビジネス": ["経済", "市場", "株", "決算", "企業", "為替", "金利", "日銀", "物価", "値上げ", "賃上げ", "景気", "投資", "銀行", "消費"],
        "北海道・函館": ["北海道", "函館", "札幌", "道南", "渡島", "檜山", "知床", "旭川", "小樽"],
        "国内": ["政府", "国会", "選挙", "首相", "省", "庁", "自治体", "制度", "法案", "裁判", "警察", "事故"],
        "国際": ["米国", "中国", "韓国", "ロシア", "欧州", "EU", "中東", "ウクライナ", "外交", "国連"],
    },
    "feeds": [
        {"name": "NHKニュース", "url": "https://news.web.nhk/n-data/conf/na/rss/cat0.xml", "category": "国内", "trust_score": 15}
    ],
}

STOPWORDS = {
    "する", "した", "して", "いる", "ある", "なる", "から", "まで", "より", "など", "ため",
    "こと", "これ", "それ", "この", "その", "への", "にも", "では", "として", "について",
    "ニュース", "速報", "最新", "発表", "明らか", "見通し", "可能性", "確認", "記事",
}


def ensure_config() -> dict:
    if not FEEDS_PATH.exists():
        FEEDS_PATH.write_text(json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"feeds.json がなかったため、サンプルを作成しました: {FEEDS_PATH}")
    with FEEDS_PATH.open("r", encoding="utf-8") as f:
        user_config = json.load(f)
    merged = DEFAULT_CONFIG.copy()
    merged.update(user_config)
    return merged


def normalize_text(text: str | None) -> str:
    text = unescape(text or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def clean_description(text: str | None) -> str:
    text = normalize_text(text)
    for pattern in [r"続きを読む。?$", r"詳しくはこちら。?$", r"詳細はこちら。?$", r"もっと見る。?$", r"この記事は.*?$"]:
        text = re.sub(pattern, "", text).strip()
    return text


def ends_with_ellipsis(text: str) -> bool:
    return text.rstrip().endswith(ELLIPSIS_SUFFIXES)


def normalize_ai_sentence(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip()).strip(" \"'「」")


def ai_sentence_is_complete(text: str) -> bool:
    text = normalize_ai_sentence(text)
    return bool(text) and not ends_with_ellipsis(text) and text.endswith(SENTENCE_ENDINGS)


def normalize_confidence(value: str) -> str:
    value = str(value or "").strip().lower()
    if value in {"high", "medium", "low"}:
        return value
    if "高" in value:
        return "high"
    if "低" in value:
        return "low"
    return "medium"


def confidence_label(value: str) -> str:
    return {"high": "高", "medium": "中", "low": "低"}.get(normalize_confidence(value), "中")


def trim_excerpt(text: str, max_length: int = 160) -> str:
    text = re.sub(r"\s+", " ", text.strip())
    if len(text) <= max_length:
        return text
    cut = text[:max_length].rstrip().rstrip("、,・:：;；")
    return cut if ends_with_ellipsis(cut) else cut + "…"


def parse_date(value: str) -> datetime:
    if not value:
        return datetime.now(JST)
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(JST)
    except Exception:
        pass
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(JST)
    except Exception:
        return datetime.now(JST)


def fetch_url(url: str, timeout: int) -> bytes:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "MorningNewsDigest/1.0 (+personal-use)",
            "Accept": "application/rss+xml, application/xml, text/xml, */*",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return res.read()


def local_name(tag: str) -> str:
    return tag.split("}")[-1]


def find_child_text(item: ET.Element, names: list[str]) -> str:
    wanted = set(names)
    for child in list(item):
        name = local_name(child.tag)
        if name in wanted:
            if name == "link" and child.attrib.get("href"):
                return child.attrib.get("href", "")
            return child.text or ""
    return ""


def classify_category(title: str, description: str, default: str, config: dict) -> str:
    text = f"{title} {description}".lower()
    for category, words in config.get("category_rules", {}).items():
        if any(word.lower() in text for word in words):
            return category
    return default or "未分類"


def score_article(title: str, description: str, category: str, published_dt: datetime, feed: dict, config: dict) -> int:
    text = f"{title} {description}".lower()
    score = 30
    score += sum(15 for kw in config.get("keywords", []) if kw.lower() in text)
    score -= sum(40 for kw in config.get("exclude_keywords", []) if kw.lower() in text)
    if category in {"AI・テック", "北海道・函館"}:
        score += 10
    score += int(feed.get("trust_score", 0))
    age_hours = max(0, (datetime.now(JST) - published_dt).total_seconds() / 3600)
    if age_hours <= 6:
        score += 20
    elif age_hours <= 24:
        score += 10
    elif age_hours <= 72:
        score += 3
    return max(0, min(100, score))


def make_simple_summary(title: str, description: str, category: str) -> tuple[str, str]:
    desc = clean_description(description)
    summary = trim_excerpt(desc, max_length=160) if desc else f"『{title}』に関するニュースです。"
    why_map = {
        "AI・テック": "情報収集・仕事の自動化・技術トレンドに影響する可能性があります。",
        "ビジネス": "市場や企業活動の変化として、仕事や生活コストに関係する可能性があります。",
        "北海道・函館": "地域の生活・移動・イベント・行政情報として確認する価値があります。",
        "国内": "国内情勢や制度変更に関係する可能性があります。",
        "国際": "海外情勢や市場・安全保障への波及を確認する価値があります。",
    }
    # DB互換用に why も返すが、HTML表示とAI総合要約では使わない。
    return summary, why_map.get(category, "関心キーワードや生活・仕事への関連度を確認する価値があります。")


def parse_feed(xml_bytes: bytes, feed: dict, config: dict) -> list[dict]:
    root = ET.fromstring(xml_bytes)
    items = root.findall(".//item") or [elem for elem in root.iter() if local_name(elem.tag) == "entry"]
    articles = []
    for item in items:
        title = normalize_text(find_child_text(item, ["title"]))
        url = normalize_text(find_child_text(item, ["link"]))
        description = clean_description(find_child_text(item, ["description", "summary", "content", "encoded"]))
        pub_raw = normalize_text(find_child_text(item, ["pubDate", "published", "updated", "date"]))
        if not title or not url:
            continue
        published_dt = parse_date(pub_raw)
        category = classify_category(title, description, feed.get("category", "未分類"), config)
        score = score_article(title, description, category, published_dt, feed, config)
        summary, why = make_simple_summary(title, description, category)
        articles.append({
            "title": title,
            "url": url,
            "source": feed.get("name", "Unknown"),
            "category": category,
            "published_at": published_dt.isoformat(timespec="minutes"),
            "description": description,
            "summary": summary,
            "why": why,
            "score": score,
        })
    return articles


def dedupe_articles(articles: list[dict]) -> list[dict]:
    by_key = {}
    for article in articles:
        title_key = re.sub(r"\W+", "", article["title"].lower())[:80]
        key = article["url"].strip() or title_key
        if key not in by_key or article["score"] > by_key[key]["score"]:
            by_key[key] = article
    return list(by_key.values())


def tokenize_for_grouping(text: str) -> set[str]:
    text = normalize_text(text).lower()
    words = re.findall(r"[a-zA-Z0-9]+|[一-龥ぁ-んァ-ヶー]{2,}", text)
    return {w for w in words if w not in STOPWORDS and len(w) >= 2}


def article_tokens(article: dict) -> set[str]:
    return tokenize_for_grouping(f"{article.get('title', '')} {article.get('description', '')}")


def token_similarity(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / max(1, min(len(a), len(b)))


def same_topic(article: dict, group: dict, threshold: float = 0.34) -> bool:
    article_tokens_set = article_tokens(article)
    representative = group["articles"][0]
    if article.get("url") == representative.get("url"):
        return True
    if article.get("category") == representative.get("category"):
        threshold = 0.30
    for existing in group["articles"]:
        if token_similarity(article_tokens_set, article_tokens(existing)) >= threshold:
            return True
    title_tokens = tokenize_for_grouping(article.get("title", ""))
    group_title_tokens = set()
    for existing in group["articles"]:
        group_title_tokens |= tokenize_for_grouping(existing.get("title", ""))
    return len(title_tokens & group_title_tokens) >= 2


def group_similar_articles(articles: list[dict], config: dict) -> list[dict]:
    max_articles = int(config.get("max_articles", 40))
    sorted_articles = sorted(articles, key=lambda x: x["score"], reverse=True)[:max_articles]
    groups: list[dict] = []
    for article in sorted_articles:
        placed = False
        for group in groups:
            if same_topic(article, group):
                group["articles"].append(article)
                group["score"] = max(group["score"], article["score"]) + min(10, len(group["articles"]) - 1)
                placed = True
                break
        if not placed:
            groups.append({"articles": [article], "score": article["score"]})
    for group in groups:
        group["articles"] = sorted(group["articles"], key=lambda x: x["score"], reverse=True)
        group["sources"] = sorted({a["source"] for a in group["articles"]})
        group["category"] = group["articles"][0].get("category", "未分類")
    groups = sorted(groups, key=lambda g: (len(g["sources"]), g["score"]), reverse=True)
    return groups[: int(config.get("max_topic_groups", 12))]


def extract_json_object(text: str) -> dict:
    raw = text.strip()
    raw = re.sub(r"^```(?:json)?", "", raw).strip()
    raw = re.sub(r"```$", "", raw).strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("JSON object not found in AI response")
    return json.loads(raw[start : end + 1])


def build_aggregate_prompt(group: dict, config: dict, retry: bool = False) -> str:
    max_articles_per_topic = int(config.get("max_articles_per_topic", 5))
    articles = group["articles"][:max_articles_per_topic]
    article_lines = []
    for index, article in enumerate(articles, start=1):
        article_lines.append(f"""
記事{index}:
出典: {article['source']}
タイトル: {article['title']}
カテゴリ: {article['category']}
概要: {article.get('description') or article.get('summary')}
URL: {article['url']}
""".strip())
    retry_note = "\n前回の出力が未完の文、または三点リーダー終わりでした。必ず完結した文に直してください。" if retry else ""
    return f"""
以下は同じ、または近い話題として収集された記事です。
複数ソースを比較し、共通して確認できる事実を優先して日本語で総合要約してください。
必ずJSONのみで返してください。Markdownや説明文は付けないでください。

出力形式:
{{
  "headline": "総合見出しを1文で",
  "summary": "何が起きたかを1〜2文で",
  "source_note": "出典の扱いを短く",
  "confidence": "high | medium | low"
}}

条件:
- 複数ソースで共通して確認できる事実を最優先する
- 1つのソースにしかない情報は「一部報道では」「○○によると」と表現する
- RSS概要に書かれていない背景、原因、影響を勝手に補完しない
- タイトルだけから断定しない
- 数字、金額、日付、人物名、組織名は入力記事にあるものだけ使う
- 出典間で内容がずれている場合は、断定せず「報道内容に差があります」と書く
- 情報が不足している場合は「詳細は記事本文で確認が必要です。」と書く
- headline は短く、煽らない
- summary は「何が起きたか」に集中する
- 文末を「…」「...」「⋯」で終えない
- headline、summary、source_note は完結した文にする
- 各項目は180文字以内
- confidence は次の基準で選ぶ
  - high: 複数ソースで同じ主要事実が確認できる
  - medium: 単独ソース、または近い話題だが主要事実は比較的明確
  - low: 情報不足、出典間差異、または同一話題か不確実{retry_note}

記事一覧:
{chr(10).join(article_lines)}
""".strip()


def call_ai_aggregate_once(client, model: str, group: dict, config: dict, retry: bool = False) -> dict:
    response = client.responses.create(model=model, input=build_aggregate_prompt(group, config=config, retry=retry))
    data = extract_json_object(response.output_text)
    return {
        "headline": normalize_ai_sentence(data.get("headline", "")),
        "summary": normalize_ai_sentence(data.get("summary", "")),
        "source_note": normalize_ai_sentence(data.get("source_note", "")),
        "confidence": normalize_confidence(data.get("confidence", "medium")),
    }


def aggregate_fallback(group: dict) -> dict:
    articles = group["articles"]
    main = articles[0]
    sources = sorted({a["source"] for a in articles})
    if len(sources) >= 2:
        source_note = "複数ソースの記事をもとにした抜粋です。"
        confidence = "medium"
    else:
        source_note = "単独ソースの記事をもとにした抜粋です。"
        confidence = "low"
    return {
        "headline": main["title"],
        "summary": main.get("summary") or trim_excerpt(main.get("description", ""), 160),
        "source_note": source_note,
        "confidence": confidence,
    }


def aggregate_summary(group: dict, config: dict) -> dict:
    if not config.get("aggregate_summary_enabled", True):
        return aggregate_fallback(group)
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return aggregate_fallback(group)
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        model = os.environ.get("OPENAI_MODEL") or config.get("ai_model", "gpt-4.1-mini")
        result = call_ai_aggregate_once(client, model, group, config=config, retry=False)
        required_sentence_fields = ["headline", "summary", "source_note"]
        if not all(ai_sentence_is_complete(result.get(k, "")) for k in required_sentence_fields):
            result = call_ai_aggregate_once(client, model, group, config=config, retry=True)
        if all(ai_sentence_is_complete(result.get(k, "")) for k in required_sentence_fields):
            result["confidence"] = normalize_confidence(result.get("confidence", "medium"))
            return result
        print("[warn] 総合AI要約が完結文にならなかったためフォールバックします", file=sys.stderr)
        return aggregate_fallback(group)
    except Exception as e:
        print(f"[warn] 総合AI要約に失敗しました: {e}", file=sys.stderr)
        return aggregate_fallback(group)


def enrich_groups_with_ai(groups: list[dict], config: dict) -> list[dict]:
    for index, group in enumerate(groups, start=1):
        group["aggregate"] = aggregate_summary(group, config)
        print(f"[aggregate-ai] {index}/{len(groups)}: {group['aggregate']['headline'][:60]}")
    return groups


def save_articles(articles: list[dict]) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS articles (
                url TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                source TEXT,
                category TEXT,
                published_at TEXT,
                description TEXT,
                summary TEXT,
                why_it_matters TEXT,
                importance_score INTEGER,
                inserted_at TEXT NOT NULL
            )
            """
        )
        now = datetime.now(JST).isoformat(timespec="seconds")
        conn.executemany(
            "INSERT OR REPLACE INTO articles VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(a["url"], a["title"], a["source"], a["category"], a["published_at"], a["description"], a["summary"], a["why"], a["score"], now) for a in articles],
        )


def score_class(score: int) -> str:
    if score >= 80:
        return "score"
    if score >= 60:
        return "score mid"
    return "score low"


def confidence_class(value: str) -> str:
    return f"confidence {normalize_confidence(value)}"


def render_related_links(articles: list[dict], config: dict) -> str:
    max_articles_per_topic = int(config.get("max_articles_per_topic", 5))
    links = []
    for article in articles[:max_articles_per_topic]:
        links.append(
            f"""
            <li>
              <a href="{escape(article['url'])}" target="_blank" rel="noopener noreferrer">
                {escape(article['source'])}: {escape(article['title'])}
              </a>
            </li>
            """
        )
    if len(articles) > max_articles_per_topic:
        links.append(f"<li>ほか {len(articles) - max_articles_per_topic} 件</li>")
    return '<ul class="related">' + "\n".join(links) + "</ul>"


def render_topic_card(group: dict, config: dict) -> str:
    aggregate = group["aggregate"]
    articles = group["articles"]
    displayed_articles = articles[: int(config.get("max_articles_per_topic", 5))]
    sources = " / ".join(sorted({a["source"] for a in displayed_articles}))
    max_score = max(a["score"] for a in articles)
    badge = "複数ソース" if len({a["source"] for a in displayed_articles}) >= 2 else "単独ソース"
    confidence = normalize_confidence(aggregate.get("confidence", "medium"))
    return f"""
    <article class="news">
      <div class="topic-meta">
        <span class="badge">{escape(badge)}</span>
        <span class="{confidence_class(confidence)}">信頼度: {escape(confidence_label(confidence))}</span>
        <span class="source-inline">{escape(sources)}</span>
      </div>
      <div class="title">{escape(aggregate['headline'])}</div>
      <p><strong>何が起きたか:</strong> {escape(aggregate['summary'])}</p>
      <p><strong>出典メモ:</strong> {escape(aggregate['source_note'])}</p>
      <div class="source">
        カテゴリ: {escape(group.get('category', '未分類'))} /
        重要度: <span class="{score_class(max_score)}">{max_score}</span> /
        参照記事: {len(articles)}件
      </div>
      {render_related_links(articles, config)}
    </article>
    """


def css() -> str:
    return """
    :root{--bg:#f7f5f0;--paper:#fffefb;--ink:#252525;--muted:#6b665f;--line:#e6e0d6;--accent:#4f7cff;--green:#2f8f5b;--yellow:#b58900;--red:#c64545}
    html{scroll-behavior:smooth}body{margin:0;background:var(--bg);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Hiragino Sans","Yu Gothic UI",Meiryo,sans-serif;line-height:1.7}.app{display:grid;grid-template-columns:280px 1fr;min-height:100vh}aside{padding:28px 20px;border-right:1px solid var(--line);background:var(--paper);position:sticky;top:0;height:100vh;box-sizing:border-box}main{padding:40px clamp(22px,5vw,72px)}.page{max-width:1040px;margin:0 auto}.hero,.card{background:var(--paper);border:1px solid var(--line);border-radius:22px;padding:24px;box-shadow:0 10px 30px rgba(39,33,25,.08);margin-bottom:16px}.nav-item,.nav-button{display:block;width:100%;box-sizing:border-box;padding:8px 10px;color:#38332e;text-decoration:none;border-radius:10px;background:transparent;border:none;text-align:left;font:inherit;cursor:pointer}.nav-item:hover,.nav-button:hover{background:#f0ede6}.nav-divider{height:1px;background:var(--line);margin:12px 0}.pill{display:inline-block;margin:4px;padding:6px 10px;border-radius:999px;background:#f3f0e9;border:1px solid var(--line);font-size:13px}.news{border-left:4px solid var(--accent);padding:14px 16px;background:#fffdf7;border-radius:12px;border:1px solid var(--line);margin:12px 0}.title{font-weight:800;font-size:18px;margin:4px 0 10px}.source{font-size:12px;color:var(--muted);margin-top:8px}.score{font-weight:800;color:var(--green)}.score.mid{color:var(--yellow)}.score.low{color:var(--red)}.topic-meta{display:flex;gap:8px;flex-wrap:wrap;align-items:center;font-size:12px;color:var(--muted)}.badge{padding:2px 8px;border-radius:999px;background:#eef3ff;color:#315dcc;border:1px solid #d9e4ff;font-weight:700}.confidence{padding:2px 8px;border-radius:999px;border:1px solid var(--line);font-weight:700}.confidence.high{background:#edf8f1;color:#2f8f5b}.confidence.medium{background:#fff8e5;color:#9a7300}.confidence.low{background:#fff0f0;color:#c64545}.source-inline{color:var(--muted)}.related{margin:10px 0 0 0;padding-left:20px;font-size:13px}.related a{color:#315dcc;text-decoration:none}.related a:hover{text-decoration:underline}.floating-actions{position:fixed;right:18px;bottom:18px;display:flex;gap:8px;z-index:20}.floating-actions button,.floating-actions a{border:1px solid var(--line);background:var(--paper);color:var(--ink);border-radius:999px;padding:10px 14px;box-shadow:0 8px 24px rgba(39,33,25,.16);text-decoration:none;font:inherit;cursor:pointer}.floating-actions button:hover,.floating-actions a:hover{background:#f0ede6}@media(max-width:900px){.app{grid-template-columns:1fr}aside{position:static;height:auto;border-right:none;border-bottom:1px solid var(--line)}.floating-actions{right:12px;bottom:12px}.floating-actions button,.floating-actions a{padding:9px 12px;font-size:14px}}
    """


def render_html(groups: list[dict], articles: list[dict], config: dict) -> str:
    now_label = datetime.now(JST).strftime("%Y年%m月%d日 %H:%M")
    title = escape(config.get("page_title", "自分用・朝のニュースまとめ"))
    top_pick_count = int(config.get("top_pick_count", 5))
    top_groups = groups[:top_pick_count]
    top_cards = "\n".join(render_topic_card(g, config) for g in top_groups) or "<p>記事がありません。</p>"
    preferred_categories = ["国内", "AI・テック", "ビジネス", "北海道・函館", "国際", "未分類"]
    sections = []
    for category in preferred_categories:
        category_groups = [g for g in groups if g.get("category") == category]
        if not category_groups:
            continue
        cards = "\n".join(render_topic_card(g, config) for g in category_groups)
        sections.append(f'<section class="card"><h2>{escape(category)}</h2>{cards}</section>')
    source_names = sorted({a["source"] for a in articles})
    source_label = " / ".join(source_names) if source_names else "なし"
    return f"""<!doctype html>
<html lang="ja">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>{title}</title><style>{css()}</style></head>
<body>
  <div class="app">
    <aside>
      <h2>🗞️ Morning Digest</h2>
      <a class="nav-item" href="#overview">🏠 概要</a>
      <a class="nav-item" href="#digest">☕ 今日の朝刊</a>
      <a class="nav-item" href="#categories">🗂️ カテゴリ別</a>
      <div class="nav-divider"></div>
      <button class="nav-button" type="button" onclick="window.location.reload()">🔄 再読み込み</button>
      <a class="nav-button" href="#overview">⬆️ TOPに戻る</a>
    </aside>
    <main>
      <section class="page" id="overview">
        <div class="hero"><div style="font-size:46px">☕</div><h1>{title}</h1><p>複数RSSから記事を取得し、近い話題を束ねてAIで総合要約しました。</p><span class="pill">📅 生成日時: {escape(now_label)}</span><span class="pill">📰 取得記事: {len(articles)}件</span><span class="pill">🧩 話題グループ: {len(groups)}件</span><span class="pill">🗞️ 出典: {escape(source_label)}</span></div>
        <h2 id="digest">今日押さえるべき{top_pick_count}つ</h2><div class="card">{top_cards}</div>
        <h2 id="categories">カテゴリ別ニュース</h2>{''.join(sections)}
      </section>
    </main>
  </div>
  <div class="floating-actions"><button type="button" onclick="window.location.reload()">🔄 再読み込み</button><a href="#overview">⬆️ TOP</a></div>
</body>
</html>
"""


def main() -> None:
    config = ensure_config()
    all_articles = []
    for feed in config.get("feeds", []):
        name = feed.get("name", "Unknown")
        url = feed.get("url")
        if not url:
            print(f"[skip] {name}: URLがありません")
            continue
        try:
            print(f"[fetch] {name}: {url}")
            articles = parse_feed(fetch_url(url, int(config.get("request_timeout_seconds", 15))), feed, config)
            print(f"       -> {len(articles)}件")
            all_articles.extend(articles)
        except (HTTPError, URLError, TimeoutError) as e:
            print(f"[error] {name}: 取得に失敗しました: {e}", file=sys.stderr)
        except ET.ParseError as e:
            print(f"[error] {name}: XML/RSSの解析に失敗しました: {e}", file=sys.stderr)
        except Exception as e:
            print(f"[error] {name}: 予期しないエラー: {e}", file=sys.stderr)
    deduped = sorted(dedupe_articles(all_articles), key=lambda x: x["score"], reverse=True)
    groups = group_similar_articles(deduped, config)
    groups = enrich_groups_with_ai(groups, config)
    save_articles(deduped)
    OUTPUT_DIR.mkdir(exist_ok=True)
    HTML_PATH.write_text(render_html(groups, deduped, config), encoding="utf-8")
    print(f"\n完了: {len(all_articles)}件取得 / {len(deduped)}件保存 / {len(groups)}グループをHTMLに出力")
    print(f"HTML: {HTML_PATH}")
    print(f"DB:   {DB_PATH}")


if __name__ == "__main__":
    main()
