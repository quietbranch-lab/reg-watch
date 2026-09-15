# 商品画像

`index.html` から相対パスで参照している商品画像。

| ファイル名 | 商品 |
|---|---|
| `sunfulon.webp` | サンフーロン 500mL |
| `roundup.webp` | ラウンドアップ マックスロード 1L |
| `kusanon.webp` | クサノンGT粒剤 800g |
| `nekosogi-top.webp` | ネコソギトップF粒剤 800g |
| `nekosogi-block.webp` | ネコソギブロックV粒剤 600g |

いずれも実物の商品画像。長辺600pxのWebPに変換して収録（合計約180KB）。
変換時にEXIF等のメタデータは除去済み。

差し替える場合は同じファイル名で上書きすれば表示に反映される。
拡張子を変える場合は `index.html` の `<img src="assets/...">` も合わせて変更すること。
