# lixil-reform.net 加盟店情報スクレイパー

https://www.lixil-reform.net/ に掲載されている加盟店(リフォーム会社)の
「会社名・住所・電話番号・FAX番号」を収集するためのスクリプトです。

## 重要な前提

このスクリプトは、実行環境(Claude Codeのサンドボックス)から
`lixil-reform.net` への通信が組織ポリシーでブロックされているため、
**実際のサイト構造を確認せずに作成しています**。そのままでは加盟店詳細
ページの抽出がうまく動かない可能性が高いです。必ず以下の手順で
自分の手元環境から調整・検証してください。

## セットアップ

```bash
cd tools/lixil_reform_scraper
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## 使い方

### 1. まず1ページで抽出結果を確認する

加盟店の詳細ページURLを1つ指定して、抽出結果を確認します。

```bash
python3 scraper.py --debug-url "https://www.lixil-reform.net/shop/xxxxx/" dummy
```

`会社名`・`住所`・`電話番号`・`FAX番号` が正しく表示されない場合は、
対象ページのHTMLをブラウザの「検証」で確認し、`scraper.py` の
`extract_shop_info()` 内のラベル一覧(`FIELD_LABELS`)やセレクタを
実際のHTML(dt/dd, th/td, ラベルの文言など)に合わせて調整してください。

### 2. サイトマップからURLを収集する場合

多くのサイトは `/sitemap.xml` を公開しています。加盟店詳細ページの
URLパターン(例: `/shop/` を含む、など)が分かれば以下のように実行します。

```bash
python3 scraper.py "https://www.lixil-reform.net/" \
  --mode sitemap \
  --detail-url-pattern "/shop/" \
  --output lixil_shops.csv
```

`/sitemap.xml` が存在しない、または加盟店ページが含まれない場合は
`--mode listing` を使ってください。

### 3. 一覧ページを巡回する場合

都道府県別の検索結果一覧ページなど、一覧→詳細のリンク構造を持つ場合は
`listing` モードを使います。一覧ページ内の詳細リンクを絞り込む
`--item-link-selector` と `--detail-url-pattern`、次ページへの
`--next-page-selector` を実際のHTMLに合わせて指定してください。

```bash
python3 scraper.py "https://www.lixil-reform.net/search/?pref=13" \
  --mode listing \
  --detail-url-pattern "/shop/" \
  --item-link-selector "a.shop-list__link" \
  --next-page-selector "a.pager__next" \
  --output lixil_shops.csv
```

都道府県ごとに一覧の起点URLが異なる場合は、起点URLのリストを用意して
シェルスクリプト等で順番に実行してください(下記「注意点」の
アクセス頻度にも留意してください)。

## 出力

`会社名, 住所, 電話番号, FAX番号, 取得元URL` の列を持つCSV
(UTF-8 BOM付き、Excelでそのまま開ける)を出力します。

## 注意点(必ず確認してください)

- **robots.txt を必ず確認・尊重すること。** 本スクリプトは起動時に
  対象ドメインの `robots.txt` を取得し、`Disallow` されたパスへは
  アクセスしません(取得できない場合は安全側に倒してアクセスしません)。
- **サイトの利用規約を確認すること。** スクレイピングや情報の
  二次利用を制限する規約がある場合は、それに従ってください。
- **アクセス頻度を抑えること。** 既定では1リクエストにつき1.5秒の
  間隔を空けます。対象サーバーに負荷をかけないよう、間隔を詰めすぎない
  でください。
- **取得したデータの利用目的に応じた法令順守。** 収集した電話番号・
  FAX番号を用いて営業連絡を行う場合は、特定商取引法など関連法令、
  および各社が公表している連絡拒否の意思表示等を確認してください。
- User-Agent は既定でシンプルな識別子になっています。必要に応じて
  `--user-agent` で連絡先を含めるなど、対象サイト運営者が問い合わせ
  可能な形に変更することを推奨します。
