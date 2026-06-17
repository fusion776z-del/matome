# Morning News Digest

自分用の「朝のニュースまとめ」アプリの最小構成です。
RSSを取得し、重複除去・カテゴリ分類・重要度スコアリングを行い、Notion/OneNote風HTMLに記事を流し込みます。

## 同梱ファイル

- generate_morning_news.py
  - RSSを取得して output/morning_news.html を生成するPythonスクリプトです。
  - 標準ライブラリのみで動きます。

- feeds_sample.json
  - RSSソース、関心キーワード、カテゴリ分類ルールのサンプル設定です。
  - 実行時は feeds_sample.json を feeds.json にリネームまたはコピーしてください。

- morning_news_notion_style.html
  - Notion/OneNote風の静的HTMLテンプレートです。
  - デザイン確認や雛形として使えます。

## 使い方

1. ZIPを展開します。
2. feeds_sample.json を feeds.json にコピーまたはリネームします。
3. ターミナルで以下を実行します。

```bash
python generate_morning_news.py
```

4. 生成されたHTMLを開きます。

```text
output/morning_news.html
```

## RSSを追加する方法

feeds.json の feeds 配列に以下のような項目を追加します。

```json
{
  "name": "ニュースソース名",
  "url": "RSSのURL",
  "category": "AI・テック",
  "trust_score": 10
}
```

## AI要約を入れる場所

generate_morning_news.py の以下の関数を、LLM API呼び出しに差し替える想定です。

```python
def make_simple_summary(title: str, description: str, category: str) -> tuple[str, str]:
```

## 注意

RSSや記事の利用は、各ニュースサイトの利用規約に従ってください。
個人利用では、記事本文の転載ではなく、短い要約・出典・リンク中心の運用がおすすめです。
