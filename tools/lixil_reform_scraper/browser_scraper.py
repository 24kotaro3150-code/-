#!/usr/bin/env python3
"""
lixil-reform.net の加盟店一覧ページをブラウザ操作(Playwright)で巡回し、
各店舗カードの「問い合わせする」ボタンをクリックして表示される
電話番号・FAX番号を取得するスクレイパー。

このサイトは一覧に会社名・住所が直接表示されているが、電話番号・
FAX番号は「問い合わせする」ボタンを押した時に開くポップアップ内に
しか表示されない(JavaScriptでのみ描画される)ため、requestsによる
静的HTML取得では取れない。そのため実ブラウザを操作するPlaywrightで
1件ずつボタンをクリックして値を読み取る方式にしている。

前提:
  - 実行環境(このサンドボックス)からは lixil-reform.net への通信が
    ブロックされているため、実サイトのDOM構造を直接確認できていない。
    スクリーンショットから読み取れる構造(会社名の見出し、「住所」ラベル、
    「詳細を見る」「問い合わせする」ボタン、ポップアップ内のTEL/FAX表示)
    を手がかりに、クラス名に依存しないテキスト/ロールベースの
    セレクタで実装している。ローカルのモックページ(同様のDOM構造)で
    抽出・ページ送り・ポップアップ開閉のロジックは動作確認済みだが、
    実サイトでの最終確認はユーザー側の環境で行うこと。
  - まず --debug-first-card で1件だけ処理し、値が正しく取れるか
    確認してから本走行することを強く推奨する。

使い方:
  pip install playwright
  # このサンドボックスと違い、通常の実行環境では以下が必要:
  playwright install chromium

  python3 browser_scraper.py "https://www.lixil-reform.net/list?pref=01" \
      --output lixil_shops.csv --debug-first-card

注意点(README.mdも参照):
  - アクセス頻度を抑えること(既定でカード間0.8秒、ページ間1.5秒待機)。
  - 対象サイトの利用規約・robots.txtの内容を確認し、それに従うこと。
  - 取得したデータを営業連絡等に使う場合は関連法令を確認すること。
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
import time
from dataclasses import dataclass

from playwright.sync_api import Locator, Page, TimeoutError as PlaywrightTimeoutError, sync_playwright

# 環境変数 PLAYWRIGHT_BROWSERS_PATH が正しく設定されていれば executable_path は
# 省略できる。このサンドボックスのように既定パスとPlaywrightのバージョンが
# ずれている場合に備えて、存在すれば明示的に使う。
FALLBACK_CHROMIUM_PATH = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"

PHONE_RE = re.compile(r"0\d{1,4}[-‐]\d{1,4}[-‐]\d{3,4}")


@dataclass
class ShopRecord:
    url: str
    company: str = ""
    address: str = ""
    phone: str = ""
    fax: str = ""


def launch_browser(p, headless: bool):
    import os

    kwargs = {"headless": headless}
    if os.path.exists(FALLBACK_CHROMIUM_PATH):
        kwargs["executable_path"] = FALLBACK_CHROMIUM_PATH
    return p.chromium.launch(**kwargs)


def find_card(button: Locator) -> Locator:
    """「問い合わせする」ボタンから、店舗カード(会社名・住所を含む祖先要素)を辿る。"""
    return button.locator(
        "xpath=ancestor::*[.//*[self::button or self::a]"
        "[contains(normalize-space(.), '詳細を見る')]][1]"
    )


def extract_card_info(card: Locator) -> tuple[str, str]:
    """カード内から会社名(見出し)と住所を取り出す。"""
    company = ""
    for tag in ("h1", "h2", "h3", "h4"):
        heading = card.locator(tag).first
        if heading.count() > 0:
            company = heading.inner_text().strip()
            break

    address = ""
    text = card.inner_text()
    m = re.search(r"住所[^\S\n]*[:：]?\s*\n?([^\n]+)", text)
    if m:
        address = m.group(1).strip()
    return company, address


def open_popup_and_read(page: Page, button: Locator, popup_wait_ms: int) -> tuple[str, str]:
    """「問い合わせする」ボタンをクリックし、開いたポップアップからTEL/FAXを読む。"""
    button.click()
    try:
        page.wait_for_timeout(popup_wait_ms)
        # ポップアップの本文全体からTEL/FAXっぽい番号を正規表現で拾う。
        # ラベルの正確な文言・マークアップに依存しないための方式。
        body_text = page.locator("body").inner_text()
    except PlaywrightTimeoutError:
        return "", ""

    phones = PHONE_RE.findall(body_text)
    phone = phones[0] if len(phones) >= 1 else ""
    fax = phones[1] if len(phones) >= 2 else ""
    return phone, fax


def close_popup(page: Page, close_selector: str) -> None:
    close_btn = page.locator(close_selector).first
    if close_btn.count() > 0:
        try:
            close_btn.click(timeout=2000)
            return
        except PlaywrightTimeoutError:
            pass
    page.keyboard.press("Escape")


def scrape_page(
    page: Page,
    contact_button_text: str,
    close_selector: str,
    delay_between_cards: float,
    popup_wait_ms: int,
    debug_first_card: bool,
) -> list[ShopRecord]:
    records: list[ShopRecord] = []
    buttons = page.get_by_role("button", name=contact_button_text).all()
    if not buttons:
        # role=button で取れない場合(<a>タグ実装など)への保険
        buttons = page.locator(f"text={contact_button_text}").all()

    for i, button in enumerate(buttons):
        card = find_card(button)
        company, address = extract_card_info(card if card.count() > 0 else button)
        phone, fax = open_popup_and_read(page, button, popup_wait_ms)
        close_popup(page, close_selector)

        record = ShopRecord(url=page.url, company=company, address=address, phone=phone, fax=fax)
        records.append(record)

        if debug_first_card:
            print("---- debug: 1件目の抽出結果 ----", file=sys.stderr)
            print(f"会社名  : {record.company or '(未検出)'}", file=sys.stderr)
            print(f"住所    : {record.address or '(未検出)'}", file=sys.stderr)
            print(f"電話番号: {record.phone or '(未検出)'}", file=sys.stderr)
            print(f"FAX番号 : {record.fax or '(未検出)'}", file=sys.stderr)
            print(
                "値が不正な場合は browser_scraper.py の find_card()/extract_card_info()/"
                "PHONE_RE を実際のDOMに合わせて調整してください。",
                file=sys.stderr,
            )
            return records

        time.sleep(delay_between_cards)

    return records


def go_to_next_page(page: Page, next_page_text: str, delay: float) -> bool:
    next_link = page.get_by_role("link", name=next_page_text).first
    if next_link.count() == 0:
        next_link = page.locator(f"text={next_page_text}").first
    if next_link.count() == 0:
        return False
    try:
        next_link.click(timeout=3000)
    except PlaywrightTimeoutError:
        return False
    page.wait_for_load_state("networkidle")
    time.sleep(delay)
    return True


def save_csv(records: list[ShopRecord], output_path: str) -> None:
    with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["会社名", "住所", "電話番号", "FAX番号", "取得元URL"])
        for r in records:
            writer.writerow([r.company, r.address, r.phone, r.fax, r.url])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("start_url", help="加盟店一覧ページのURL(都道府県等で絞り込み済みのもの)")
    parser.add_argument("-o", "--output", default="lixil_shops.csv", help="出力CSVファイルパス")
    parser.add_argument("--contact-button-text", default="問い合わせする", help="電話/FAXを表示させるボタンの文言")
    parser.add_argument("--close-selector", default="text=×", help="ポップアップを閉じるボタンのCSS/テキストセレクタ")
    parser.add_argument("--next-page-text", default="次へ", help="次ページへのリンク/ボタンの文言")
    parser.add_argument("--max-pages", type=int, default=0, help="巡回する一覧ページ数の上限。0で無制限")
    parser.add_argument("--delay-between-cards", type=float, default=0.8, help="カードごとの待機時間(秒)")
    parser.add_argument("--delay-between-pages", type=float, default=1.5, help="ページ送り後の待機時間(秒)")
    parser.add_argument("--popup-wait-ms", type=int, default=500, help="ポップアップ表示待ちのミリ秒")
    parser.add_argument("--headed", action="store_true", help="ブラウザ画面を表示して実行する(動作確認用)")
    parser.add_argument(
        "--debug-first-card",
        action="store_true",
        help="1ページ目の最初の1件だけ処理して抽出結果を表示し終了する(調整用)",
    )
    args = parser.parse_args()

    all_records: list[ShopRecord] = []

    with sync_playwright() as p:
        browser = launch_browser(p, headless=not args.headed)
        page = browser.new_page()
        page.goto(args.start_url, wait_until="networkidle")

        page_count = 0
        while True:
            page_count += 1
            print(f"[info] ページ {page_count} を処理中: {page.url}", file=sys.stderr)
            records = scrape_page(
                page,
                args.contact_button_text,
                args.close_selector,
                args.delay_between_cards,
                args.popup_wait_ms,
                args.debug_first_card,
            )
            all_records.extend(records)

            if args.debug_first_card:
                break
            if args.max_pages and page_count >= args.max_pages:
                break
            if not go_to_next_page(page, args.next_page_text, args.delay_between_pages):
                break

        browser.close()

    if args.debug_first_card:
        return

    save_csv(all_records, args.output)
    print(f"[done] {len(all_records)} 件を {args.output} に保存しました", file=sys.stderr)


if __name__ == "__main__":
    main()
