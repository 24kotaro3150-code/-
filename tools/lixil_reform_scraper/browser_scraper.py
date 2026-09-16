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
import math
import re
import sys
import time
from dataclasses import dataclass

from playwright.sync_api import (
    ElementHandle,
    Locator,
    Page,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)

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


_FIND_CARD_BY_ITEM_CLASS_JS = """
el => {
    // 実サイトのDOM確認で判明した、1店舗=1個の<li class="p-caseListItems__item">
    // という構造を最優先の手がかりにする。クラス名が変わった場合に備えて、
    // BEM命名の "__item" で終わるクラスも同様に「1件分のアイテム」とみなす。
    let cur = el;
    for (let i = 0; i < 20 && cur.parentElement; i++) {
        cur = cur.parentElement;
        if (!cur.classList) continue;
        if (cur.classList.contains('p-caseListItems__item')) return cur;
        for (const cls of cur.classList) {
            if (cls.endsWith('__item')) return cur;
        }
    }
    return null;
}
"""

_FIND_CARD_BOUNDARY_JS = """
(el, contactText) => {
    // ボタンが <button>/<a>/role=button とは限らない(例: 単なる<div class="m-btn">)
    // ため、タグ/role種別は問わず「この文言を含み、かつ子要素の誰もこの文言を
    // 含まない(=文言を含む最も内側の要素)」ものを1個の"ボタン相当"としてカウントする。
    function isTightMatch(node) {
        if (!(node.textContent || '').includes(contactText)) return false;
        for (const child of node.children) {
            if ((child.textContent || '').includes(contactText)) return false;
        }
        return true;
    }
    function matchCount(root) {
        let count = 0;
        for (const node of root.querySelectorAll('*')) {
            if (isTightMatch(node)) count += 1;
        }
        return count;
    }
    let cur = el;
    let lastGood = null;
    for (let i = 0; i < 20 && cur.parentElement; i++) {
        cur = cur.parentElement;
        const count = matchCount(cur);
        if (count === 1) {
            lastGood = cur;
        } else if (count > 1) {
            // 兄弟カード(他店舗)のボタンまで含んでしまう手前で止める。
            break;
        }
    }
    return lastGood;
}
"""


def find_card(button: Locator, contact_button_text: str) -> ElementHandle | None:
    """「問い合わせする」ボタンから店舗カードの境界要素を特定する。

    1. まず、実サイトで確認済みの "__item" 系クラス(p-caseListItems__item等)
       を持つ祖先を優先的に探す。PC/SP切り替えなどで「問い合わせする」ボタンが
       カード内に複数存在するケースでも正しくカード全体を取れる。
    2. 見つからない場合のみ、タグ名やclass名に依存しない汎用ヒューリスティック
       (「他の店舗のボタンを巻き込む直前の、最も外側の祖先」)にフォールバックする。
    """
    handle = button.element_handle()
    if handle is None:
        return None

    by_class = handle.evaluate_handle(_FIND_CARD_BY_ITEM_CLASS_JS)
    card = by_class.as_element()
    if card is not None:
        return card

    result = handle.evaluate_handle(_FIND_CARD_BOUNDARY_JS, contact_button_text)
    return result.as_element()


def extract_card_info(card: ElementHandle) -> tuple[str, str]:
    """カード内から会社名(見出し、無ければ先頭行)と住所を取り出す。"""
    company = ""
    for tag in ("h1", "h2", "h3", "h4", "h5"):
        heading = card.query_selector(tag)
        if heading:
            company = heading.inner_text().strip()
            break

    text = card.inner_text()
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]

    if not company and lines:
        company = lines[0]

    address = ""
    m = re.search(r"住所[^\S\n]*[:：]?\s*\n?([^\n]+)", text)
    if m:
        address = m.group(1).strip()
    return company, address


def debug_dump_dom(button: Locator, card: ElementHandle | None) -> None:
    """会社名・住所が取れない原因を調べるため、実際のDOM構造を出力する。"""
    print("---- DOM debug ----", file=sys.stderr)
    try:
        btn_html = button.evaluate("el => el.outerHTML")
    except Exception as exc:
        btn_html = f"(取得失敗: {exc})"
    print(f"[問い合わせするボタン outerHTML]\n{btn_html[:800]}\n", file=sys.stderr)

    if card is not None:
        try:
            card_html = card.evaluate("el => el.outerHTML")
        except Exception as exc:
            card_html = f"(取得失敗: {exc})"
        print(f"[find_card()が特定したカード outerHTML (先頭3000文字)]\n{card_html[:3000]}\n", file=sys.stderr)
    else:
        print(
            "[find_card() は該当要素を見つけられませんでした"
            "(「問い合わせする」ボタンを1個だけ含む祖先要素が12階層以内に無かった)。"
            "ボタンの祖先要素チェーンを表示します]",
            file=sys.stderr,
        )
        try:
            chain = button.evaluate(
                """
                el => {
                    let chain = [];
                    let cur = el.parentElement;
                    for (let i = 0; i < 12 && cur; i++) {
                        const cls = cur.className ? '.' + String(cur.className).replace(/\\s+/g, '.') : '';
                        chain.push(cur.tagName.toLowerCase() + cls);
                        cur = cur.parentElement;
                    }
                    return chain.join(' > ');
                }
                """
            )
        except Exception as exc:
            chain = f"(取得失敗: {exc})"
        print(f"祖先要素チェーン(近い順): {chain}\n", file=sys.stderr)

        try:
            grandparent_html = button.evaluate(
                "el => el.parentElement && el.parentElement.parentElement "
                "? el.parentElement.parentElement.outerHTML : ''"
            )
        except Exception as exc:
            grandparent_html = f"(取得失敗: {exc})"
        print(f"[祖父要素 outerHTML (先頭2000文字)]\n{grandparent_html[:2000]}\n", file=sys.stderr)

    print(
        "この出力をそのまま貼り付けてもらえれば、find_card()/extract_card_info() を"
        "実際の構造に合わせて修正します。",
        file=sys.stderr,
    )


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
    max_items: int = 0,
) -> list[ShopRecord]:
    records: list[ShopRecord] = []
    buttons = page.get_by_role("button", name=contact_button_text).all()
    if not buttons:
        # role=button で取れない場合(<a>タグ・role無しdiv実装など)への保険
        buttons = page.locator(f"text={contact_button_text}").all()

    # PC/SP切り替えUIでは同じ文言のボタンがDOM上に複数(非表示分も)存在することが
    # あるため、実際に見えているものだけを対象にする。非表示要素をクリックしようと
    # するとPlaywrightが操作可能になるまで待ち続け、スクリプトがハングするため。
    buttons = [b for b in buttons if b.is_visible()]
    if max_items:
        buttons = buttons[:max_items]

    for i, button in enumerate(buttons):
        card = find_card(button, contact_button_text)
        if card is not None:
            company, address = extract_card_info(card)
        else:
            company, address = "", ""
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
            if not record.company or not record.address:
                debug_dump_dom(button, card)
            return records

        time.sleep(delay_between_cards)

    return records


TOTAL_COUNT_RE = re.compile(r"(\d+)\s*件中")

# ページ番号のリンクが見つからない場合(番号ウィンドウの外にいる等)に備えた、
# 「次へ」を意味しそうな矢印/ボタンの候補セレクタ。上から順に試す。
_NEXT_ARROW_SELECTOR_CANDIDATES = [
    'a[aria-label="次へ"]',
    'button[aria-label="次へ"]',
    'a[aria-label="Next"]',
    'button[aria-label="Next"]',
    'a[rel="next"]',
    'button[rel="next"]',
    '[class*="pagination"] [class*="next"]',
    '[class*="pager"] [class*="next"]',
    "text=›",
    "text=»",
    "text=＞",
    "text=>",
]


def extract_total_items(page: Page) -> int | None:
    """「368件中 1〜10件を表示」のような表記から総件数を読み取る。"""
    try:
        text = page.locator("body").inner_text()
    except PlaywrightTimeoutError:
        return None
    m = TOTAL_COUNT_RE.search(text)
    return int(m.group(1)) if m else None


def go_to_page(page: Page, target_page: int, next_page_text: str, delay: float) -> bool:
    """指定したページ番号に進む。

    1. まずページ番号そのもの(例: "2")をクリックする。誤クリックを避けるため
       role=link/button で名前が完全一致するものを優先し、それでも見つからない
       場合のみテキスト完全一致で探す。
    2. ページ番号がウィンドウ外で見当たらない場合は、「次へ」相当の矢印/ボタンを
       候補セレクタから順に試してクリックする。
    """
    before_text = ""
    try:
        before_text = page.locator("body").inner_text()[:2000]
    except PlaywrightTimeoutError:
        pass

    clicked = False
    target_str = str(target_page)
    for getter in (
        lambda: page.get_by_role("link", name=target_str, exact=True),
        lambda: page.get_by_role("button", name=target_str, exact=True),
        lambda: page.get_by_text(target_str, exact=True),
    ):
        try:
            loc = getter().first
            if loc.count() > 0 and loc.is_visible():
                loc.click(timeout=3000)
                clicked = True
                break
        except PlaywrightTimeoutError:
            continue

    if not clicked:
        for sel in [f"text={next_page_text}", *_NEXT_ARROW_SELECTOR_CANDIDATES]:
            try:
                loc = page.locator(sel).first
                if loc.count() > 0 and loc.is_visible():
                    loc.click(timeout=3000)
                    clicked = True
                    break
            except PlaywrightTimeoutError:
                continue

    if not clicked:
        return False

    page.wait_for_load_state("networkidle")
    if before_text:
        try:
            page.wait_for_function(
                "(prev) => document.body.innerText.slice(0, 2000) !== prev",
                arg=before_text,
                timeout=8000,
            )
        except PlaywrightTimeoutError:
            # SPA遷移でない、または比較範囲がたまたま変化しない場合もあるので、
            # ここでは失敗として扱わず、下の待機時間に委ねる。
            pass
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
    parser.add_argument(
        "--next-page-text",
        default="次へ",
        help="ページ番号リンクが見つからない場合に使う「次へ」相当リンク/ボタンの文言",
    )
    parser.add_argument("--max-pages", type=int, default=0, help="巡回する一覧ページ数の上限。0で無制限")
    parser.add_argument(
        "--max-items",
        type=int,
        default=0,
        help="取得する店舗件数の上限。0で無制限(達した時点でページの途中でも打ち切る。動作確認用)",
    )
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
        total_pages: int | None = None
        while True:
            page_count += 1
            print(f"[info] ページ {page_count} を処理中: {page.url}", file=sys.stderr)
            remaining = args.max_items - len(all_records) if args.max_items else 0
            records = scrape_page(
                page,
                args.contact_button_text,
                args.close_selector,
                args.delay_between_cards,
                args.popup_wait_ms,
                args.debug_first_card,
                max_items=remaining,
            )
            all_records.extend(records)
            print(f"[info] このページで {len(records)} 件取得(累計 {len(all_records)} 件)", file=sys.stderr)

            if args.max_items and len(all_records) >= args.max_items:
                print(f"[info] --max-items の上限({args.max_items})に到達したため終了します", file=sys.stderr)
                break

            if page_count == 1 and records:
                total_items = extract_total_items(page)
                page_size = len(records)
                if total_items:
                    total_pages = math.ceil(total_items / page_size)
                    print(
                        f"[info] 総件数 {total_items} 件 / 1ページ {page_size} 件 "
                        f"→ 全 {total_pages} ページと推定",
                        file=sys.stderr,
                    )

            if args.debug_first_card:
                break
            if args.max_pages and page_count >= args.max_pages:
                print(f"[info] --max-pages の上限({args.max_pages})に到達したため終了します", file=sys.stderr)
                break
            if total_pages and page_count >= total_pages:
                print("[info] 推定ページ数に到達したため終了します", file=sys.stderr)
                break
            if not go_to_page(page, page_count + 1, args.next_page_text, args.delay_between_pages):
                print("[info] 次ページへのリンクが見つからなかったため終了します", file=sys.stderr)
                break

        browser.close()

    if args.debug_first_card:
        return

    save_csv(all_records, args.output)
    print(f"[done] {len(all_records)} 件を {args.output} に保存しました", file=sys.stderr)


if __name__ == "__main__":
    main()
