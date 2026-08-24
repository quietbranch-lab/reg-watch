# 規制当局ウォッチャー（reg-watch）

金融庁・財務省・証券取引等監視委員会・公正取引委員会・個人情報保護委員会の新着情報を毎朝自動取得し、GitHub Pages上のダッシュボードに表示する個人用ツール。

## 仕組み

```
GitHub Actions (毎朝 JST 6:00頃)
  └─ scripts/fetch_news.py
       ├─ 金融庁・財務省・SESC: 公式RSSを取得
       ├─ 公取委・個情委: 新着一覧ページをHTML解析
       ├─ data/seen.json と突き合わせて「本日の新着」を判定
       └─ docs/data/news.json を生成してコミット
GitHub Pages (docs/ フォルダを公開)
  └─ docs/index.html がnews.jsonを読んで表示
```

閲覧は `https://<ユーザー名>.github.io/<リポジトリ名>/`。
「本日分をコピー」ボタンで、生成AI用の分類指示プロンプト付きテキストがクリップボードに入る。

## セットアップ手順（個人のスマホ/PCから）

1. GitHubで新しいリポジトリを作成（**Public**にすること。無料プランのPagesはPublicのみ）
2. このフォルダの中身を丸ごとアップロード
3. リポジトリの **Settings → Pages** で
   - Source: `Deploy from a branch`
   - Branch: `main` / フォルダ: `/docs`
   を選択して保存
4. **Actionsタブ** → `Update regulatory news` → `Run workflow` で初回を手動実行
5. 実行完了後、数分待ってPagesのURLを開く。表示されたら仕事用PCでもそのURLをブックマーク

## 運用メモ

- cronはUTC 21:00（JST 6:00）設定だが、GitHubの仕様で数分〜数十分遅れることがある
- 「本日の新着」は `data/seen.json`（URL→初回検出日）との差分で判定。初回実行時は全件が新着扱いになる
- 公取委・個情委はHTML解析のため、サイト改修で取れなくなることがある。その場合は
  `scripts/fetch_news.py` の `fetch_jftc` / `fetch_ppc` を調整する（Actionsのログにエラーが出る）
- 監視対象を増やすときは `SOURCES` にエントリを追加するだけ（RSSがあるサイトなら `type: "rss"` でOK）

## 情報源

| 官庁 | 方式 | URL |
|---|---|---|
| 金融庁 | RSS | https://www.fsa.go.jp/fsaNewsListAll_rss2.xml |
| 財務省 | RSS | https://www.mof.go.jp/news.rss |
| SESC（報道発表） | RSS | https://www.fsa.go.jp/sescReportList_rss2.xml |
| SESC（その他広報） | RSS | https://www.fsa.go.jp/sescOtherList_rss2.xml |
| 公正取引委員会 | HTML解析 | https://www.jftc.go.jp/ |
| 個人情報保護委員会 | HTML解析 | https://www.ppc.go.jp/information/ |

※ 取得対象はすべて官公庁の公開情報。取得頻度は1日1回であり、各サイトへの負荷は実質ゼロ。
# reg-watch
