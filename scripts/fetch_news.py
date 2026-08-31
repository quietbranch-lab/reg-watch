# -*- coding: utf-8 -*-
"""
8官庁の新着情報を取得して docs/data/news.json を生成する。

取得方式:
  - 金融庁 / 財務省 / SESC / 日本銀行 / 消費者庁: 公式RSS
  - 公正取引委員会 / 個人情報保護委員会 / 警察庁JAFIC: 新着一覧のHTML解析
    （公式RSSが無いため。robots.txt上はいずれも取得が許可されている）

新着判定:
  - data/seen.json に「識別キー -> 初回検出日」を記録し、今回の実行で初めて見た
    ものを is_new=True とする。前回実行以降に増えた分だけが新着になるので、
    週明けの実行では金曜以降のぶんがまとめて拾われる（祝日カレンダー不要）。
  - 識別キーはURLと日付の組。JAFICのように「同じページを日付違いで再掲する」
    官庁があり、URLだけだと再掲を検出できないため。

取得結果:
  - 官庁ごとに status を持たせ、「新着が無い」のか「取得に失敗した」のかを
    表示側で区別できるようにする。
"""

import json
import os
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

# 取得元に対して素性を正直に名乗る。ブラウザを騙っていた時期もあるが、
# それでも公取委の403は変わらず（IP遮断が理由）偽装する利点が無かったため。
# 取得は1日1回のみ。
HEADERS = {
    "User-Agent": "reg-watch/1.0 (personal news reader; 1 request/day)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
}

# この環境からは取得できない機関のid（カンマ区切り）。
# 公取委はGitHub ActionsのIPが遮断されるが国内の一般回線からは取れるため、
# Actions側だけ REG_WATCH_SKIP=jftc で除外する。除外した機関は直前の出力を
# 引き継ぐので、PCからの実行が入れた内容を空で上書きしてしまうことがない。
SKIP_SOURCES = {
    s.strip() for s in os.environ.get("REG_WATCH_SKIP", "").split(",") if s.strip()
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
        "id": "boj",
        "name": "日本銀行",
        "seal": "銀",
        "site": "https://www.boj.or.jp/",
        "method": "rss",
        "feeds": [
            {"type": "rss", "url": "https://www.boj.or.jp/rss/whatsnew.xml"},
        ],
    },
    {
        # 消費者庁は総合フィードの大半が食品表示・製品事故・採用等で、
        # 公益通報者保護制度に関するものだけに絞る。
        "id": "caa",
        "name": "消費者庁（公益通報）",
        "seal": "消",
        "site": (
            "https://www.caa.go.jp/policies/policy/consumer_partnerships/"
            "whisleblower_protection_system/"
        ),
        "method": "html",
        "feeds": [
            # 制度の専用ページに載る新着情報。ここが本命。
            {
                "type": "dated_list",
                "url": (
                    "https://www.caa.go.jp/policies/policy/consumer_partnerships/"
                    "whisleblower_protection_system/"
                ),
            },
            # 専用ページへの掲載が遅れる場合の取りこぼし対策として、
            # 総合フィードからキーワードに一致するものだけを拾う。
            {
                "type": "rss",
                "url": "https://www.caa.go.jp/news.rss",
                "keywords": ["公益通報", "内部通報", "通報者保護"],
            },
        ],
    },
    {
        # GitHub ActionsのIPレンジはWAFで遮断されるが、国内の一般回線からは取れる。
        # Actions側は REG_WATCH_SKIP=jftc で除外し、PCからの実行に任せる。
        "id": "jftc",
        "name": "公正取引委員会",
        "seal": "公",
        "site": "https://www.jftc.go.jp/",
        "method": "html",
        "feeds": [
            {"type": "dated_list", "url": "https://www.jftc.go.jp/"},
        ],
    },
    {
        "id": "ppc",
        "name": "個人情報保護委員会",
        "seal": "個",
        "site": "https://www.ppc.go.jp/",
        "method": "html",
        "feeds": [
            # 新着情報と注意喚起はトップページに載る。/information/ は年度別の
            # アーカイブ索引なので、ここを見ると最新分が取れない。
            {"type": "dated_list", "url": "https://www.ppc.go.jp/"},
        ],
    },
    {
        "id": "jafic",
        "name": "警察庁 JAFIC",
        "seal": "警",
        "site": "https://www.npa.go.jp/sosikihanzai/jafic/index.htm",
        "method": "html",
        "feeds": [
            {
                "type": "dated_list",
                "url": "https://www.npa.go.jp/sosikihanzai/jafic/index.htm",
            },
        ],
    },
    {
        "id": "moj",
        "name": "法務省",
        "seal": "法",
        "site": "https://www.moj.go.jp/",
        "method": "rss",
        "feeds": [
            # 新着・更新情報とお知らせの2系統。試験関係(test.xml)は対象外。
            {"type": "rss", "url": "https://www.moj.go.jp/news.xml"},
            {"type": "rss", "url": "https://www.moj.go.jp/info.xml"},
        ],
    },
    {
        "id": "mhlw",
        "name": "厚生労働省",
        "seal": "厚",
        "site": "https://www.mhlw.go.jp/",
        "method": "rss",
        "feeds": [
            {"type": "rss", "url": "https://www.mhlw.go.jp/stf/news.rdf"},
        ],
    },
]

# 全角数字も含めて拾う（\d はUnicodeの十進数字に一致し、int() もそれを解釈する）
WAREKI_RE = re.compile(r"令和\s*(\d+)\s*年\s*(\d+)\s*月\s*(\d+)\s*日")

# 内閣府が公開している国民の祝日の一覧。営業日の判定に使う。
HOLIDAY_CSV = "https://www8.cao.go.jp/chosei/shukujitsu/syukujitsu.csv"


def fetch_holidays():
    """祝日をISO形式の集合で返す。取得できなければ空集合（土日のみで判定）。"""
    try:
        r = requests.get(HOLIDAY_CSV, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        r.encoding = "cp932"
        days = set()
        for line in r.text.splitlines()[1:]:
            head = line.split(",")[0].strip()
            try:
                days.add(datetime.strptime(head, "%Y/%m/%d").strftime("%Y-%m-%d"))
            except ValueError:
                continue
        return days
    except Exception as e:
        print("WARN: 祝日一覧を取得できませんでした（土日のみで判定）:", e,
              file=sys.stderr)
        return set()


def is_business_day(iso_date, holidays):
    """土日祝でなければ営業日とみなす。"""
    d = datetime.strptime(iso_date, "%Y-%m-%d")
    return d.weekday() < 5 and iso_date not in holidays


def previous_business_day(iso_date, holidays):
    """直前の営業日を返す。新着の基準日に使う。

    基準日を保存せずその場で計算するのが要点。保存方式にすると、同じ日の
    2回目の実行が基準日を当日に進めてしまい、1回目に見つけた分が消える。
    Actionsの3回とPCの1回で1日に最大4回走るため、毎日それが起きていた。
    """
    d = datetime.strptime(iso_date, "%Y-%m-%d")
    for _ in range(30):  # 連休が続いても抜けられる範囲
        d -= timedelta(days=1)
        iso = d.strftime("%Y-%m-%d")
        if is_business_day(iso, holidays):
            return iso
    return d.strftime("%Y-%m-%d")


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


# RFC822のタイムゾーン略称。Python の %Z は UTC / GMT と実行環境のローカル名
# しか解釈できず、"JST" を渡すと解析に失敗する。金融庁とSESCのフィードが
# JST 表記のため、そのままでは日付が全て None になっていた。
TZ_ABBR = {
    "JST": "+0900", "UTC": "+0000", "GMT": "+0000", "UT": "+0000",
    "EST": "-0500", "EDT": "-0400", "CST": "-0600", "CDT": "-0500",
    "MST": "-0700", "MDT": "-0600", "PST": "-0800", "PDT": "-0700",
}


def parse_rfc822(text):
    """RSSのpubDate(RFC822等)やISO日付をISO形式に変換。失敗したら None。"""
    if not text:
        return None
    text = text.strip()

    # 末尾が略称なら数値オフセットに置き換えてから解析する
    head, _, tail = text.rpartition(" ")
    if head and tail.upper() in TZ_ABBR:
        text = head + " " + TZ_ABBR[tail.upper()]

    for fmt in (
        "%a, %d %b %Y %H:%M:%S %z",
        "%a, %d %b %Y %H:%M:%S %Z",
        "%a, %d %b %Y %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S",
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
            elif tag in ("pubdate", "date"):
                pub = (child.text or "").strip()
        if title and link:
            items.append(
                {
                    "title": title,
                    "url": link,
                    "date": parse_rfc822(pub) or wareki_to_iso(title),
                }
            )
    if not items:
        # 構造は正しいのに項目が無いことがある（SESCのその他広報など、
        # 単に配信が空の期間）。解析できないのとは別物なので失敗にしない。
        looks_like_feed = (
            root.tag.endswith("rss")
            or root.tag.endswith("RDF")
            or any(x.tag.endswith("channel") for x in root.iter())
        )
        if not looks_like_feed:
            raise ValueError("フィードを解析できませんでした")
    return items


def fetch_dated_list(url):
    """新着一覧ページから「日付を持つリスト項目」を抽出する。

    ページ全体のリンクを総当たりすると案内リンクや旧記事を大量に拾うため、
    li / dd / tr のうち「日付とリンクを持つ末端の項目」だけを対象にする。
    """
    r = get(url)
    soup = BeautifulSoup(r.text, "html.parser")
    items = []

    for el in soup.find_all(["li", "dd", "tr"]):
        a = el.find("a", href=True)
        if a is None:
            continue
        # 入れ子の外側（日付付き項目を内包する親）は飛ばし、末端だけを採る
        if any(
            WAREKI_RE.search(c.get_text(" ", strip=True))
            for c in el.find_all(["li", "dd", "tr"])
        ):
            continue

        # <time datetime="2026-08-21"> があればそれを優先する
        date = None
        t = el.find("time")
        if t is not None and t.get("datetime"):
            date = parse_rfc822(t["datetime"].strip())
        if date is None:
            date = wareki_to_iso(el.get_text(" ", strip=True))
        if date is None:
            continue

        title = WAREKI_RE.sub("", a.get_text(" ", strip=True)).strip("（）() 　")
        if len(title) < 6:  # 「一覧へ」等の短いリンクを除外
            continue

        href = requests.compat.urljoin(url, a["href"])
        if not href.startswith(("http://", "https://")):
            continue

        items.append({"title": title, "url": href, "date": date})

    if not items:
        raise ValueError("ページ構造が変わった可能性があります（項目を抽出できません）")
    return items


FETCHERS = {"rss": fetch_rss, "dated_list": fetch_dated_list}


def item_key(it):
    """項目の識別キー。

    JAFICのように同じページを日付違いで繰り返し告知する官庁があるため、
    URLだけでは再掲を新着として検出できない。日付と組にして一意にする。
    """
    return "{}|{}".format(it["url"], it["date"] or "")


def cap_items(items):
    """上限件数まで絞る。新着は必ず残し、余った枠を既知の項目で埋める。"""
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

    # 直前の出力から前回取得時刻を引き継ぐ（新着の対象期間を表示するため）。
    # 同じ日に複数回走ったときは境界を動かさない。is_new は「その日に初めて
    # 見たか」で判定するので、対象期間はその日の最初の実行時点から始まる。
    previous = {}
    if OUT_PATH.exists():
        try:
            previous = json.loads(OUT_PATH.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            previous = {}

    if previous.get("generated_date") == TODAY:
        previous_generated_at = previous.get("previous_generated_at")
    else:
        previous_generated_at = previous.get("generated_at")

    previous_agencies = {a["id"]: a for a in previous.get("agencies", [])}

    # 新着の基準日 = 直前の営業日。それより後に初めて見つけたものを新着とする。
    #
    # 「今日初めて見たか」だと、金曜日中に公表されたものは土曜の実行でしか
    # 新着にならず、月曜の朝には消えてしまう。前営業日を基準にすれば、
    # 月曜には金曜日中・土・日のぶんがまとめて出る。
    holidays = fetch_holidays()
    new_since = previous_business_day(TODAY, holidays)

    agencies_out = []
    all_errors = []

    for src in SOURCES:
        # この環境から取得できない機関は、取りに行かず直前の出力を引き継ぐ。
        # 取得できる環境が入れた内容を、空の結果で上書きしないため。
        if src["id"] in SKIP_SOURCES:
            carried = previous_agencies.get(src["id"])
            if carried is not None:
                # 引き継いだ項目の新着フラグも同じ基準で付け直す
                for it in carried.get("items", []):
                    it["is_new"] = (it.get("first_seen") or "") > new_since
                agencies_out.append(carried)
            else:
                agencies_out.append(
                    {
                        "id": src["id"],
                        "name": src["name"],
                        "seal": src["seal"],
                        "site": src["site"],
                        "method": src["method"],
                        "status": "manual",
                        "errors": [],
                        "items": [],
                    }
                )
            continue

        collected = []
        errors = []

        for feed in src["feeds"]:
            try:
                got = FETCHERS[feed["type"]](feed["url"])
                # keywords 指定がある系統は、一致する項目だけを採用する。
                # 絞り込んだ結果ゼロ件でも「関連する新着が無い」だけなので失敗にしない。
                keywords = feed.get("keywords")
                if keywords:
                    got = [
                        i
                        for i in got
                        if any(k in i["title"] for k in keywords)
                    ]
                collected.extend(got)
            except Exception as e:  # 1系統失敗しても他は続行
                msg = "{} -> {}".format(feed["url"], e)
                errors.append(msg)
                all_errors.append("{}: {}".format(src["name"], msg))

        # 全系統が落ちたのか、一部だけかを区別する
        if src["method"] == "manual":
            status = "manual"      # そもそも取得しない。失敗ではない
        elif not errors:
            status = "ok"
        elif len(errors) < len(src["feeds"]):
            status = "partial"
        else:
            status = "error"

        # 識別キーで重複排除
        dedup = {}
        for it in collected:
            dedup.setdefault(item_key(it), it)
        merged = list(dedup.values())

        # 初回検出日を記録し、is_new を付与
        for it in merged:
            key = item_key(it)
            first_seen = seen.get(key)
            if first_seen is None and it["date"]:
                # 以前は日付が取れず "URL|" で記録されていた項目がある。
                # 日付解析を直した結果キーが変わるので、旧キーを引き継いで
                # 既読のものが一斉に新着へ戻るのを防ぐ。
                first_seen = seen.pop(it["url"] + "|", None)
                if first_seen is not None:
                    seen[key] = first_seen
            if first_seen is None:
                seen[key] = TODAY
                first_seen = TODAY
            it["first_seen"] = first_seen
            it["is_new"] = first_seen > new_since

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

    # seen.json の肥大化防止: 180日より前に初回検出した項目は破棄
    cutoff = (datetime.now(JST) - timedelta(days=180)).strftime("%Y-%m-%d")
    seen = {k: d for k, d in seen.items() if d >= cutoff}

    out = {
        "generated_at": datetime.now(JST).strftime("%Y-%m-%d %H:%M"),
        "generated_date": TODAY,
        "previous_generated_at": previous_generated_at,
        # 新着の対象期間の起点（直前の営業日）
        "new_since": new_since,
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
    print("OK: {} items ({} new). errors={}".format(total, fresh, len(all_errors)))
    for a in agencies_out:
        mark = {
            "ok": "OK",
            "partial": "PARTIAL",
            "error": "FAILED",
            "manual": "MANUAL",
        }[a["status"]]
        n = len([i for i in a["items"] if i["is_new"]])
        print(
            "  [{}] {}: {} items, {} new".format(
                mark, a["name"], len(a["items"]), n
            )
        )
    for e in all_errors:
        print("WARN:", e, file=sys.stderr)


if __name__ == "__main__":
    main()
