# reg-watch

金融庁・財務省・証券取引等監視委員会・公正取引委員会・個人情報保護委員会の新着情報を1日1回まとめて取得し、
GitHub Pages上の一覧ページに表示する個人用のメモ置き場。

## 仕組み

```
GitHub Actions (毎朝 JST 6:00頃)
  └─ scripts/fetch_news.py
       ├─ 金融庁・財務省・SESC: 公式RSSを取得
       ├─ 公取委・個情委: 新着一覧ページをHTML解析
       ├─ data/seen.json と突き合わせて新着を判定
       └─ docs/data/news.json を生成してコミット
GitHub Pages (docs/ フォルダを公開)
  └─ docs/index.html がnews.jsonを読んで表示
```

## 新着の判定

掲載日ではなく「`data/seen.json` にまだ無いURLを今回の実行で初めて見つけたか」で判定している。
毎朝1回走るので、これは実質「前回の取得以降に増えたぶん」＝前営業日分になる。

この方式にしている理由は2つある。

- 週明けの実行では金曜朝以降のぶんがまとめて拾われるので、**祝日カレンダーを持たなくても営業日の穴が空かない**
- 官庁ページには日付が取れない項目が混ざるが、掲載日でフィルタしないので取りこぼさない

Actionsが1日落ちても、次に成功した実行がその間の差分をまとめて拾うため欠落しない。
初回実行時だけは `seen.json` が空なので全件が新着扱いになる。

一覧ページは既定で「新着のみ」を表示する。過去分を見たいときは表示切り替えで「すべて」を選ぶ。

## 取得できたか / 新着が無いかの区別

官庁ごとに `status` を持たせている。RSSが無くHTML解析に頼っている公取委・個情委は
サイト改修やアクセス制限で壊れうるため、**「新着なし」と「取得失敗」が画面上で必ず区別される**ようにしてある。

| status | 意味 | 画面表示 |
|---|---|---|
| `ok` | 全系統の取得に成功 | 新着一覧、または「新着はありません」 |
| `partial` | 一部の系統だけ失敗（SESCのように複数フィードを持つ官庁） | 取得できた分＋一部失敗の注記 |
| `error` | 全系統が失敗 | 「取得に失敗しました。新着の有無は不明です。」＋エラー内容 |

`error` の官庁は件数がゼロでも「新着なし」とは表示されない。ヘッダー部分にも失敗した官庁名が出る。

## 取得方式

| 官庁 | 方式 | URL |
|---|---|---|
| 金融庁 | RSS | https://www.fsa.go.jp/fsaNewsListAll_rss2.xml |
| 財務省 | RSS | https://www.mof.go.jp/news.rss |
| SESC（報聓発表） | RSS | https://www.fsa.go.jp/sescReportList_rss2.xml |
| SESC（その他広報） | RSS | https://www.fsa.go.jp/sescOtherList_rss2.xml |
| 公正取引委員会 | HTML解析 | https://www.jftc.go.jp/ |
| 個人情報保護委員会 | HTML解析 | https://www.ppc.go.jp/information/ |

公取委・個情委は公式RSSを提供していないためページを解析している。
両サイトとも robots.txt 上で対象パスの取得は許可されている（JFTCは `Allow: /` のみでDisallow指定なし、
PPCの `/information/` も制限対象外）。取得はいずれも1日1回。

## メモ

- cronはUTC 21:00（JST 6:00）設定だが、GitHubの仕様で数分〜数十分遅れることがある
- HTTPヘッダは一般的なブラウザ相当のものを送っている。requests既定の `python-requests/x.y` だと
  WAFに弾かれて403になるサイトがあるため
- HTML解析が壊れたら `scripts/fetch_news.py` の `fetch_jftc` / `fetch_ppc` を調整する。
  項目がゼロ件だった場合は例外を投げて `status: error` になるので、画面とActionsのログの両方で気付ける
- 監視対象を増やすときは `SOURCES` にエントリを追加する（RSSがあるサイトなら `type: "rss"` にOK）
- `MAX_ITEMS_PER_AGENCY` で1官庁あたりの保持件数を制限しているが、新着は上限に関わらず必ず残す
