# -*- coding: utf-8 -*-
"""
新着項目のリンク先を読み、Gemini APIで要約して docs/data/news.json に埋める。

fetch_news.py とは別スクリプトにしてある。要約は「あると便利」な付加機能で、
Gemini側の障害や無料枠の上限で失敗しても、新着一覧そのものは無事に更新され
なければならないため。ワークフローでも fetch_news.py の後に独立して走らせる。

環境変数:
  GEMINI_API_KEY  必須。無ければ何もせず正常終了する（要約なしで運用できる）
  GEMINI_MODEL    任意。既定は gemini-2.0-flash-lite。無料枠の対象モデルは
                  変わることがあるので、コードを直さず差し替えられるようにする
  SUMMARY_LIMIT   任意。1回の実行で生成する上限。既定40。
                  無料枠のレート制限に当たらないよう抑え、余りは翌日に回す

要約は data/summaries.json にキャッシュする。一度作ったものは作り直さない。
"""

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

JST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parent.parent
NEWS_PATH = ROOT / "docs" / "data" / "news.json"
CACHE_PATH = ROOT / "data" / "summaries.json"

API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash-lite").strip()
LIMIT = int(os.environ.get("SUMMARY_LIMIT", "40"))

ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{}:generateContent"

TIMEOUT = 45
MAX_CHARS = 6000          # Geminiに渡す本文の上限
CACHE_KEEP_DAYS = 180

HEADERS = {
    "User-Agent": "reg-watch/1.0 (personal news reader; 1 request/day)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
}

PROMPT = """次は日本の官庁が公表した文書のページです。実務者が「自分の担当に関係するか」を判断できるように要約してください。

条件:
- 日本語で、200字以内
- 誰に対する何の文書か、何が変わるのか、いつからかを優先して書く
- ページから読み取れないことは書かない。推測や一般論で補わない
- 内容が読み取れない場合は「本文を取得できませんでした」とだけ返す
- 前置きや見出しは不要。要約本文だけを返す

タイトル: {title}

本文:
{body}
"""


def load_json(path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return default


def item_key(it):
    return "{}|{}".format(it["url"], it.get("date") or "")


def extract_text(url):
    """リンク先の本文を取り出す。取得できなければ None。"""
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()

    ctype = r.headers.get("Content-Type", "").lower()
    if "pdf" in ctype or url.lower().endswith(".pdf"):
        # PDFは本文を取れない。要約対象外として扱う
        return None
    if "html" not in ctype and "xml" not in ctype:
        return None

    if r.encoding in (None, "ISO-8859-1"):
        r.encoding = r.apparent_encoding
    soup = BeautifulSoup(r.text, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer"]):
        tag.decompose()
    main = soup.find("main") or soup.find(id="main") or soup.body or soup
    text = main.get_text("\n", strip=True)
    return text[:MAX_CHARS] if len(text) > 200 else None


def summarize(title, body):
    """Geminiに要約させる。失敗時は例外を投げる。"""
    resp = requests.post(
        ENDPOINT.format(MODEL),
        params={"key": API_KEY},
        json={
            "contents": [{"parts": [{"text": PROMPT.format(title=title, body=body)}]}],
            "generationConfig": {"temperature": 0.2, "maxOutputTokens": 400},
        },
        timeout=TIMEOUT,
    )
    if resp.status_code == 429:
        raise RuntimeError("rate-limited")
    resp.raise_for_status()
    data = resp.json()
    parts = data["candidates"][0]["content"]["parts"]
    return "".join(p.get("text", "") for p in parts).strip()


def main():
    if not API_KEY:
        print("GEMINI_API_KEY が未設定のため要約は生成しません（一覧は通常どおり）")
        return 0

    news = load_json(NEWS_PATH, None)
    if not news:
        print("news.json が読めないため何もしません", file=sys.stderr)
        return 0

    cache = load_json(CACHE_PATH, {})
    targets = []
    for agency in news.get("agencies", []):
        for it in agency.get("items", []):
            key = item_key(it)
            if key in cache:
                it["summary"] = cache[key].get("summary")
                it["summary_status"] = cache[key].get("status", "ok")
            elif it.get("is_new"):
                targets.append((agency["name"], it, key))

    print("要約の対象: {}件（上限 {}）  モデル: {}".format(
        len(targets), LIMIT, MODEL))

    done = failed = skipped = 0
    for name, it, key in targets[:LIMIT]:
        try:
            body = extract_text(it["url"])
        except Exception as e:
            print("  本文取得に失敗 [{}] {}: {}".format(name, it["title"][:24], e),
                  file=sys.stderr)
            body = None

        if not body:
            cache[key] = {"summary": None, "status": "unavailable",
                          "at": datetime.now(JST).strftime("%Y-%m-%d")}
            it["summary"] = None
            it["summary_status"] = "unavailable"
            skipped += 1
            continue

        try:
            text = summarize(it["title"], body)
        except Exception as e:
            # レート制限や一時障害。キャッシュに残さず次回に持ち越す
            print("  要約に失敗 [{}] {}: {}".format(name, it["title"][:24], e),
                  file=sys.stderr)
            failed += 1
            if "rate-limited" in str(e):
                print("  レート制限のため以降は次回に回します", file=sys.stderr)
                break
            continue

        cache[key] = {"summary": text, "status": "ok", "model": MODEL,
                      "at": datetime.now(JST).strftime("%Y-%m-%d")}
        it["summary"] = text
        it["summary_status"] = "ok"
        done += 1
        time.sleep(1.0)   # 無料枠の分あたり上限に当たらないよう間隔をあける

    # キャッシュの肥大化を防ぐ
    cutoff = (datetime.now(JST) - timedelta(days=CACHE_KEEP_DAYS)).strftime("%Y-%m-%d")
    cache = {k: v for k, v in cache.items() if v.get("at", "9999") >= cutoff}

    CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=0),
                          encoding="utf-8")
    news["summary_model"] = MODEL
    NEWS_PATH.write_text(json.dumps(news, ensure_ascii=False, indent=1),
                         encoding="utf-8")

    remaining = max(0, len(targets) - LIMIT)
    print("要約: 生成{} / 本文なし{} / 失敗{} / 未処理{}".format(
        done, skipped, failed, remaining))
    return 0


if __name__ == "__main__":
    sys.exit(main())
