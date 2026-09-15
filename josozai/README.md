# 除草剤 5種比較ページ

親戚にURLを共有して、スマホから5商品を比較してもらうための静的ページ。

```
josozai/
├─ index.html      … ページ本体（HTML/CSSのみ。JSはimgのフォールバックのみ）
└─ assets/         … 商品画像（5ファイル。assets/README.md 参照）
```

## Vercelで公開する手順

このディレクトリはビルド不要の静的サイトです。

### Vercel CLI の場合

```bash
npm i -g vercel
cd josozai
vercel login      # ブラウザ認証が必要
vercel --prod
```

Framework Preset は **Other**、Build Command は空、
Output Directory は `.` （カレント）を指定します。

### GitHubリポジトリ連携の場合

Vercel の New Project でこのリポジトリを選び、

- **Root Directory**: `josozai`
- **Framework Preset**: Other
- **Build Command**: （空欄）
- **Output Directory**: `.`

を設定します。

## 掲載データの出典

内容量・持続期間・散布面積はメーカー公式および商品パッケージの表記に基づく。
「効き目」の★は5製品内での相対的な目安であり、公式の指標ではない。
価格は2026年9月時点の参考価格。
