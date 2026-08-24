# -*- coding: utf-8 -*-
"""
5官庁の新着情報を取得して docs/data/news.json を生成する。

取得方式:
  - 金融庁 / 財務省 / 証券取引等監視委員会: 公式RSS
  - 公正取引委員会 / 個人情報保護委員会: 新着一覧ページのHTML解析
    （公式RSSが提供されていないため。robots.txt上はいずれも取得が許可されている）

新着判定:
  - data/seen.json に「URL -> 初回検出日」を記録し、今回の実行で初めて見た
    ものを is_new=True とする。前回実行以降に増えた分だけが新着になるので、
    週明けの実行では金曜以降のぶんがまとめて拾われる（祝日カレンダー不要）。

取得結果:
  - 官庁ごとに status を持たせ、「新着が無い」のか「取得に失敗した」のかを
    表示側で区別できるようにする。
"""

import json
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

JST = timezone(timedelta(hours=9))
TODAY = datetime.now(JST).strftime("%Y-%m-%d")

ROOT = Path(__file__).resolve().parent.parent
SEEN_PATH = ROOT / "data" / "seen.json"
OUT_PATH = ROOT / "docs" / "data" / "news.json"

MAX_ITEMS_PER_AGENCY = 60
TIMEOUT = 30

# 既定の User-Agent（python-requests/x.y）はWAFに弾かれることがあるため、
# 一般的なブラウザと同等のヘッダを送る。取得は1日1回のみ。
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
}

SOURCES = [
    {
        "id": "fsa",
        "name": "金融庁",
        "seal": "金",
        "site": "https://www.fsa.go.jp/",
        "method": "rss",
        "feeds": [
            {"type": "rss", "url": "https://www.fsa.go.jp/fsaNewsListAll_rss2.xml"},
        ],
    },
    {
        "id": "mof",
        "name": "財務省",
        "seal": "財",
        "site": "https://www.mof.go.jp/",
        "method": "rss",
        "feeds": [
            {"type": "rss", "url": "https://www.mof.go.jp/news.rss"},
        ],
    },
    {
        "id": "sesc",
        "name": "証券取引等監視委員会",
        "seal": "監",
        "site": "https://www.fsa.go.jp/sesc/",
        "method": "rss",
        "feeds": [
            {"type": "rss", "url": "https://www.fsa.go.jp/sescReportList_rss2.xml"},
            {"type": "rss", "url": "https://www.fsa.go.jp/sescOtherList_rss2.xml"},
        ],
    },
    {
        "id": "jftc",
        "name": "公正取引委員会",
        "seal": "公",
        "site": "https://www.jftc.go.jp/",
        "method": "html",
        "feeds": [
            {"type": "jftc_html", "url": "https://www.jftc.go.jp/"},
        ],
    },
    {
        "id": "ppc",
        "name": "個人情報保護委員会",
        "seal": "個",
        "site": "https://www.ppc.go.jp/",
        "method": "html",
        "feeds": [
            {"type": "ppc_html", "url": "https://www.ppc.go.jp/information/"},
        ],
    },
]

WAREKI_RE = re.compile(r"令和\s*(\d+)\s*年\s*(\d+)\s*月\s*(\d+)\s*日")


def wareki_to_iso(text):
    """テキスト中の令和表記日付をISO形式に変換。見つからなければ None。"""
    m = WAREKI_RE.search(text or "")
    if not m:
        return None
    year = 2018 + int(m.group(1))  # 令和1年 = 2019年
    try:
        return datetime(year, int(m.group(2)), int(m.group(3))).strftime("%Y-%m-%d")
    except ValueError:
        return None


def parse_rfc822(text):
    """RSSのpubDate(RFC822等)をISO形式に変換。失敗したら None。"""
    if not text:
        return None
    text = text.strip()
    for fmt in (
        "%a, %d %b %Y %H:%M:%S %z",
        "%a, %d %b %Y %H:%M:%S %Z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def get(url):
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    # 官庁サイトはcharset申告が不正確なことがあるため補正
    if r.encoding in (None, "ISO-8859-1"):
        r.encoding = r.apparent_encoding
    return r


def fetch_rss(url):
    """RSS 2.0 / RDF をゆるく解析して item のリストを返す。"""
    r = get(url)
    items = []
    root = ET.fromstring(r.content)
    # 名前空間の有無に依存しないよう、タグ名の末尾一致で走査する
    for item in root.iter():
        if not item.tag.endswith("item"):
            continue
        title, link, pub = None, None, None
        for child in item:
            tag = child.tag.split("}")[-1].lower()
            if tag == "title":
                title = (child.text or "").strip()
            elif tag == "link":
                link = (child.text or "").strip()
            elif tag in ("pubdate", "date", "dc:date"):
                pub = (child.text or "").strip()
        if title and link:
            date = parse_rfc822(pub) or wareki_to_iso(title)
            items.append({"title": title, "url": link, "date": date})
    if not items:
        raise ValueError("フィードから項目を取得できませんでした")
    return items


def fetch_jftc(url):
    """公取委トップページから『(令和N年M月F日)件名』形式のリンクを抽出。"""
    r = get(url)
    soup = BeautifulSoup(r.text, "html.parser")
    items = []
    for a in soup.find_all("a", href=True):
        text = a.get_text(" ", strip=True)
        if not text:
            continue
        date = wareki_to_iso(text)
        if date is None:
            # リンク周轺（親要素）のテキストにも日付がないか確認
            parent_text = a.parent.get_text(" ", strip=True) if a.parent else ""
            date = wareki_to_iso(parent_text)
            if date is None:
                continue
        title = WAREKI_RE.sub("", text).strip("（）() 　")
        if len(title) < 6:  # ナビゲーション等の短いリンクを除外
            continue
        href = requests.compat.urljoin(url, a["href"])
        if "jftc.go.jp" not in href:
            continue
        items.append({"title": title, "url": href, "date": date})
    if not items:
        raise ValueError("ページ構造が変わった可能性があります（項目を抽出できません）")
    return items


def fetch_ppc(url):
    """個情委の新着情報一覧ページから項目を抽出。"""
    r = get(url)
    soup = BeautifulSoup(r.text, "html.parser")
    items = []
    for a in soup.find_all("a", href=True):
        text = a.get_text(" ", strip=True)
        if not text or len(text) < 6:
            continue
        # 日付はリンク自体か、リンクを含む行（親要素）のテキストから拾う
        context = a.parent.get_text(" ", strip=True) if a.parent else text
        date = wareki_to_iso(text) or wareki_to_iso(context)
        if date is None:
            continue
        href = requests.compat.urljoin(url, a["href"])
        if "ppc.go.jp" not in href:
            continue
        title = WAREKI_RE.sub("", text).strip("（）() 　")
        items.append({"title": title, "url": href, "date": date})
    if not items:
        raise ValueError("ページ構造が変わった可能性があります（項目を抽出できません）")
    return items


FETCHERS = {"rss": fetch_rss, "jftc_html": fetch_jftc, "ppc_html": fetch_ppc}


def cap_items(items):
    """上限件数まで絞る。新着は必ず残し、余った枴を既知の項目で埋める。"""
    if len(items) <= MAX_ITEMS_PER_AGENCY:
        return items
    fresh = [i for i in items if i["is_new"]]
    known = [i for i in items if not i["is_new"]]
    room = max(0, MAX_ITEMS_PER_AGENCY - len(fresh))
    return fresh + known[:room]


def main():
    seen = {}
    if SEEN_PATH.exists():
        seen = json.loads(SEEN_PATH.read_text(encoding="utf-8"))

    # 直前の出力から前回取得時刻を引き継぀（新着の対象期間を表示するため）
    previous_generated_at = None
    if OUT_PATH.exists():
        try:
            previous_generated_at = json.loads(
                OUT_PATH.read_text(encoding="utf-8")
            ).get("generated_at")
        except (ValueError, OSError):
            previous_generated_at = None

    agencies_out = []
    all_errors = []

    for src in SOURCES:
        collected = []
        errors = []

        for feed in src["feeds"]:
            try:
                collected.extend(FETCHERS[feed["type"]](feed["url"]))
            except Exception as e:  # 1系統失敗しても他は続行
                msg = f"{feed['url']} -> {e}"
                errors.append(msg)
                all_errors.append(f"{src['name']}: {msg}")

        # 全系統が落ちたのか、一部だけかを区別する
        if not errors:
            status = "ok"
        elif len(errors) < len(src["feeds"]):
            status = "partial"
        else:
            status = "error"

        # URLで重複排除
        dedup = {}
        for it in collected:
            dedup.setdefault(it["url"], it)
        merged = list(dedup.values())

        # 初回検出日を記録し、is_new を付与
        for it in merged:
            first_seen = seen.get(it["url"])
            if first_seen is None:
                seen[it["url"]] = TODAY
                first_seen = TODAY
            it["first_seen"] = first_seen
            it["is_new"] = first_seen == TODAY

        # 日付降順（日付なしは末尾）に並べてから上限を適用
        merged.sort(key=lambda x: (x["date"] or "0000-00-00"), reverse=True)
        merged = cap_items(merged)

        agencies_out.append(
            {
                "id": src["id"],
                "name": src["name"],
                "seal": src["seal"],
                "site": src["site"],
                "method": src["method"],
                "status": status,
                "errors": errors,
                "items": merged,
            }
        )

    # seen.json の肥大化防止: 180日より前に初回検出したURLは破棄
    cutoff = (datetime.now(JST) - timedelta(days=180)).strftime("%Y-%m-%d")
    seen = {u: d for u, d in seen.items() if d >= cutoff}

    out = {
        "generated_at": datetime.now(JST).strftime("%Y-%m-%d %H:%M"),
        "generated_date": TODAY,
        "previous_generated_at": previous_generated_at,
        "agencies": agencies_out,
        "errors": all_errors,
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    SEEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    SEEN_PATH.write_text(
        json.dumps(seen, ensure_ascii=False, indent=0), encoding="utf-8"
    )

    total = sum(len(a["items"]) for a in agencies_out)
    fresh = sum(len([i for i in a["items"] if i["is_new"]]) for a in agencies_out)
    print(f"OK: {total} items ({fresh} new). errors={len(all_errors)}")
    for a in agencies_out:
        mark = {"ok": "OK", "partial": "PARTIAL", "error": "FAILED"}[a["status"]]
        n = len([i for i in a["items"] if i["is_new"]])
        print(f"  [{mark}] {a['name']}: {len(a['items'])} items, {n} new")
    for e in all_errors:
        print("WARN:", e, file=sys.stderr)


if __name__ == "__main__":
    main()
