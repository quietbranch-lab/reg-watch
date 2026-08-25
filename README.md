# reg-watch

8つの官庁・機関の新着情報を1日1回まとめて取得し、GitHub Pages上の一覧ページに表示する個人用のメモ置き場。

## 仕組み

```
GitHub Actions (毎朝 JST 6:00頃)
  └─ scripts/fetch_news.py
       ├─ 公式RSSがある5機関: RSSを取得
       ├─ 無い3機関: 新着一覧ページをHTML解析
       ├─ data/seen.json と突き合わせて新着を判定
       └─ docs/data/news.json を生成してコミット
GitHub Pages (docs/ フォルダを公開)
  └─ docs/index.html がnews.jsonを読んで表示
```

## 取得方式

| 官庁・機関 | 方式 | URL |
|---|---|---|
| 金融庁 | RSS | https://www.fsa.go.jp/fsaNewsListAll_rss2.xml |
| 財務省 | RSS | https://www.mof.go.jp/news.rss |
| SESC（報道発表） | RSS | https://www.fsa.go.jp/sescReportList_rss2.xml |
| SESC（その他広報） | RSS | https://www.fsa.go.jp/sescOtherList_rss2.xml |
| 日本銀行 | RSS | https://www.boj.or.jp/rss/whatsnew.xml |
| 消費者庁 | RSS | https://www.caa.go.jp/news.rss |
| 公正取引委員会 | HTML解析 | https://www.jftc.go.jp/ |
| 個人情報保護委員会 | HTML解析 | https://www.ppc.go.jp/ |
| 警察庁 JAFIC | HTML解析 | https://www.npa.go.jp/sosikihanzai/jafic/index.htm |

公取委・個情委・JAFICは公式RSSを提供していないためページを解析している。
robots.txt 上で対象パスの取得は許可されている（JFTCは `Allow: /` のみでDisallow指定なし、
PPCの `/information/` も制限対象外）。取得はいずれも1日1回。

### HTML解析の方針

`fetch_dated_list` はページ全体のリンクを総当たりせず、**li / dd / tr のうち
「日付とリンクを持つ末端の項目」だけ**を拾う。総当たりだと案内リンクや数年前の記事を
大量に拾ってしまい、新着一覧として使い物にならないため。
`<time datetime="...">` があればそれを、無ければ本文中の令和表記を日付に使う。

個情委は**トップページ**を見る。`/information/` は年度別アーカイブの索引で、
ここを見ると最新の新着情報が取れない。

## 新着の判定

掲載日ではなく「`data/seen.json` にまだ無い項目を今回の実行で初めて見つけたか」で判定している。
毎朝1回走るので、これは実質「前回の取得以降に増えたぶん」＝前営業日分になる。

この方式にしている理由は2つある。

- 週明けの実行では金曜朝以降のぶんがまとめて拾われるので、**祝日カレンダーを持たなくても営業日の穴が空かない**
- 官庁ページには日付が取れない項目が混ざるが、掲載日でフィルタしないので取りこぼさない

Actionsが1日落ちても、次に成功した実行がその間の差分をまとめて拾うため欠落しない。

一覧ページは既定で「新着のみ」を表示する。過去分は表示切り替えで「すべて」を選ぶ。

### 識別キー

`seen.json` のキーは **URLと日付の組**（`item_key`）。URLだけにすると、JAFICのように
同じページ（`todoke/yousei.htm#p1`）を日付違いで繰り返し告知する機関の再掲を検出できず、
重複排除で1件に潰れてしまう。

## 取得できたか / 新着が無いかの区別

官庁ごとに `status` を持たせている。RSSが無くHTML解析に頼っている3機関は
サイト改修やアクセス制限で壊れうるため、**「新着なし」と「取得失敗」が画面上で必ず区別される**ようにしてある。

| status | 意味 | 画面表示 |
|---|---|---|
| `ok` | 全系統の取得に成功 | 新着一覧、または「新着はありません」 |
| `partial` | 一部の系統だけ失敗（SESCのように複数フィードを持つ場合） | 取得できた分＋一部失敗の注記 |
| `error` | 全系統が失敗 | 「取得に失敗しました。新着の有無は不明です。」＋エラー内容 |

`error` の官庁は件数がゼロでも「新着なし」とは表示されない。ヘッダー部分にも失敗した官庁名が出る。

## メモ

- cronはUTC 21:00（JST 6:00）設定だが、GitHubの仕様で数分〜数十分遅れることがある
- **公取委は現在403で取得できていない。** robots.txtは取得を許可しており、
  ブラウザ相当のUAでも変わらないことから、GitHub ActionsのIPレンジがWAFで
  遮断されていると見られる。回避はしない。画面上は `error` として明示される
- HTML解析が壊れたら `fetch_dated_list` を調整する。項目がゼロ件だった場合は
  例外を投げて `status: error` になるので、画面とActionsのログの両方で気付ける
- 監視対象を増やすときは `SOURCES` にエントリを追加する（RSSがあるサイトなら `type: "rss"`、
  無ければ `type: "dated_list"`）
- `MAX_ITEMS_PER_AGENCY` で1機関あたりの保持件数を制限しているが、新着は上限に関わらず必ず残す
