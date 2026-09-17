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
import glob
import math
import os
import re
import sys
import time
from dataclasses import dataclass
from urllib.parse import urljoin

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

# 都道府県トップページ(https://www.lixil-reform.net/shop_search 等)から
# 各都道府県の一覧ページへ飛ぶために使う、標準の47都道府県名。
PREFECTURES = [
    "北海道", "青森県", "岩手県", "宮城県", "秋田県", "山形県", "福島県",
    "茨城県", "栃木県", "群馬県", "埼玉県", "千葉県", "東京都", "神奈川県",
    "新潟県", "富山県", "石川県", "福井県", "山梨県", "長野県",
    "岐阜県", "静岡県", "愛知県", "三重県",
    "滋賀県", "京都府", "大阪府", "兵庫県", "奈良県", "和歌山県",
    "鳥取県", "島根県", "岡山県", "広島県", "山口県",
    "徳島県", "香川県", "愛媛県", "高知県",
    "福岡県", "佐賀県", "長崎県", "熊本県", "大分県", "宮崎県", "鹿児島県", "沖縄県",
]


@dataclass
class ShopRecord:
    url: str
    company: str = ""
    address: str = ""
    phone: str = ""
    fax: str = ""


def launch_browser(p, headless: bool):
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


# 実サイトのDOM確認で判明した、ポップアップ本体のクラス名パターン。
# トリガー側は "tooltip-contact-trigger"、ポップアップ本体は "tooltip-contact ..." で
# "trigger" を含まないため、両者をこの条件で区別できる。
_OPEN_TOOLTIP_ELEMENT_JS = """
() => {
    const candidates = Array.from(document.querySelectorAll('[class*="tooltip-contact"]'))
        .filter(el => !el.className.includes('trigger'));
    for (const el of candidates) {
        const style = window.getComputedStyle(el);
        if (style.display !== 'none' && style.visibility !== 'hidden' && el.offsetParent !== null) {
            return el;
        }
    }
    return null;
}
"""

_ANY_TOOLTIP_OPEN_JS = """
() => {
    const candidates = Array.from(document.querySelectorAll('[class*="tooltip-contact"]'))
        .filter(el => !el.className.includes('trigger'));
    return candidates.some(el => {
        const style = window.getComputedStyle(el);
        return style.display !== 'none' && style.visibility !== 'hidden' && el.offsetParent !== null;
    });
}
"""


def _extract_number_near_label(text: str, labels: list[str]) -> str:
    for label in labels:
        m = re.search(rf"{re.escape(label)}[^0-9]{{0,10}}({PHONE_RE.pattern})", text)
        if m:
            return m.group(1)
    return ""


def open_popup_and_read(page: Page, button: Locator, popup_wait_ms: int) -> tuple[str, str]:
    """「問い合わせする」ボタンをクリックし、開いたポップアップからTEL/FAXを読む。

    実サイトのポップアップは、フリーダイヤル(0120等)が別途表示される店舗もある
    ("TEL"欄とFAXの間にもう1つ電話番号らしき行が挟まる)。そのため単純に
    「最初の番号=TEL、2番目の番号=FAX」と位置で決め打ちすると、フリーダイヤルを
    FAXと誤認識してしまう。実DOMで確認済みの、TEL行が持つ "tell" クラス、FAX行が
    持つ "fax" クラスを使って直接該当要素から読み取ることで、間に何個フリー
    ダイヤルが挟まっていても正しくTEL/FAXを区別する。
    """
    button.click()
    try:
        page.wait_for_timeout(popup_wait_ms)
        tooltip = page.evaluate_handle(_OPEN_TOOLTIP_ELEMENT_JS).as_element()
    except PlaywrightTimeoutError:
        return "", ""

    phone = ""
    fax = ""

    if tooltip is not None:
        tel_el = tooltip.query_selector('[class*="tell"], [class*="tel__"], [class*="tel-"]')
        fax_el = tooltip.query_selector('[class*="fax"]')
        if tel_el:
            m = PHONE_RE.search(tel_el.inner_text())
            phone = m.group(0) if m else ""
        if fax_el:
            m = PHONE_RE.search(fax_el.inner_text())
            fax = m.group(0) if m else ""

    if phone and fax:
        return phone, fax

    # クラス名でTEL/FAXの要素を特定できなかった場合のフォールバック。
    # 位置(何番目の番号か)ではなく、"TEL"/"FAX"ラベル直後の番号を拾う方式にして、
    # 間にフリーダイヤル等が挟まっていても誤認識しにくくする。
    source_text = tooltip.inner_text() if tooltip is not None else page.locator("body").inner_text()
    if not phone:
        phone = _extract_number_near_label(source_text, ["TEL", "Tel", "電話"])
    if not fax:
        fax = _extract_number_near_label(source_text, ["FAX", "Fax"])

    if phone and fax:
        return phone, fax

    # それでも取れない場合の最終フォールバック(従来通り、最初の2件を使う)。
    phones = PHONE_RE.findall(source_text)
    if not phone:
        phone = phones[0] if len(phones) >= 1 else ""
    if not fax:
        fax = next((p for p in phones if p != phone), "")
    return phone, fax


def _tooltip_is_open(page: Page) -> bool:
    try:
        return bool(page.evaluate(_ANY_TOOLTIP_OPEN_JS))
    except PlaywrightTimeoutError:
        return False


def close_popup(page: Page, close_selector: str) -> None:
    """ポップアップを閉じ、実際に閉じたことまで確認する。

    実サイトの閉じるボタンは <div class="close_btn"><img alt="×ボタン"></div> という
    構造で、"×" という文字そのものは持たない(imgのalt属性のみ)。そのため単純な
    text=× セレクタでは一致しないことがあり、閉じ損ねると次のカードのクリックが
    「開いたまま」扱いで無視され、前の値を読み続けてしまう。これを避けるため、
    実際に確認済みの .close_btn を優先しつつ複数の候補を試し、最後に
    「ポップアップが本当に非表示になったか」をJS側で検証する。
    """
    candidates = [close_selector, ".close_btn", 'img[alt*="×"]', 'img[alt*="閉じる"]', "text=×"]
    seen: set[str] = set()
    for sel in candidates:
        if not sel or sel in seen:
            continue
        seen.add(sel)
        try:
            loc = page.locator(sel).first
            if loc.count() > 0 and loc.is_visible():
                loc.click(timeout=2000)
        except PlaywrightTimeoutError:
            continue

        page.wait_for_timeout(200)
        if not _tooltip_is_open(page):
            return

    # ここまでで閉じたと確認できなかった場合の最終手段。
    page.keyboard.press("Escape")
    try:
        page.mouse.click(2, 2)
    except PlaywrightTimeoutError:
        pass
    page.wait_for_timeout(200)

    if _tooltip_is_open(page):
        print(
            "[warn] ポップアップが閉じたことを確認できませんでした。"
            "次の店舗の電話番号/FAXが誤って前の値のままになっている可能性があります。",
            file=sys.stderr,
        )


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
    except Exception:
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
    # このサイトはページ送りが(SPAではなく)実際のページ遷移(?P=2等)で行われる。
    # そのため遷移中は document/実行コンテキストが一瞬失われるタイミングがあり、
    # 通常のPlaywrightTimeoutErrorとは別種の例外(実行コンテキスト破棄など)が
    # 起こり得る。1ページの遷移タイミングのズレで都道府県全体の処理を失う
    # ことがないよう、ここでは広めにExceptionを捕まえて処理を続行する。
    before_text = ""
    try:
        before_text = page.locator("body").inner_text()[:2000]
    except Exception:
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
        except Exception:
            continue

    if not clicked:
        for sel in [f"text={next_page_text}", *_NEXT_ARROW_SELECTOR_CANDIDATES]:
            try:
                loc = page.locator(sel).first
                if loc.count() > 0 and loc.is_visible():
                    loc.click(timeout=3000)
                    clicked = True
                    break
            except Exception:
                continue

    if not clicked:
        return False

    try:
        page.wait_for_load_state("networkidle")
    except Exception:
        pass

    if before_text:
        try:
            page.wait_for_function(
                "(prev) => !!document.body && document.body.innerText.slice(0, 2000) !== prev",
                arg=before_text,
                timeout=8000,
            )
        except Exception:
            # SPA遷移でない、比較範囲がたまたま変化しない、遷移タイミングで
            # 実行コンテキストが一時的に失われた等、いずれの場合も失敗とは
            # 扱わず、下の待機時間に委ねる。
            pass
    time.sleep(delay)
    return True


def save_csv(records: list[ShopRecord], output_path: str) -> None:
    with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["会社名", "住所", "電話番号", "FAX番号", "取得元URL"])
        for r in records:
            writer.writerow([r.company, r.address, r.phone, r.fax, r.url])


def scrape_list_pages(page: Page, start_url: str, args: argparse.Namespace) -> list[ShopRecord]:
    """1つの一覧ページ(都道府県で絞り込み済み等)を、ページ送りしながら最後まで巡回する。"""
    page.goto(start_url, wait_until="networkidle")
    if page.url.rstrip("/") != start_url.rstrip("/"):
        # 直前の都道府県での遷移トラブルの余波で別ページに流れた、あるいは
        # サイト側の何らかのリダイレクトで想定外のページに着地した可能性がある。
        # 0件になった場合に「本当に該当店舗が無い」のか「取得に失敗した」のか
        # 見分けられるよう、ここで明示的に警告しておく。
        print(
            f"[warn] 遷移先が指定URLと異なります(指定: {start_url} / 実際: {page.url})。"
            "想定外のページに遷移した可能性があります。",
            file=sys.stderr,
        )

    all_records: list[ShopRecord] = []
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

    return all_records


def discover_prefecture_links(page: Page, search_url: str) -> list[tuple[str, str]]:
    """都道府県トップページ(shop_search等)から、各都道府県一覧ページへのURLを集める。

    「北海道」「青森県」...という確定済みの47都道府県名をそのまま手がかりに
    リンクを探すため、都道府県ごとのURLパターン(スラッグ)を推測する必要がない。
    """
    page.goto(search_url, wait_until="networkidle")

    found: list[tuple[str, str]] = []
    for name in PREFECTURES:
        loc = None
        for getter in (
            lambda: page.get_by_role("link", name=name, exact=True),
            lambda: page.get_by_role("link", name=name),
            lambda: page.get_by_text(name, exact=True),
        ):
            candidate = getter().first
            if candidate.count() > 0:
                loc = candidate
                break

        if loc is None:
            print(f"[warn] 「{name}」へのリンクが見つかりませんでした。スキップします。", file=sys.stderr)
            continue

        href = loc.get_attribute("href")
        if not href:
            print(f"[warn] 「{name}」のリンクに href がありませんでした。スキップします。", file=sys.stderr)
            continue

        found.append((name, urljoin(search_url, href)))

    return found


def merge_csvs(output_dir: str, merged_path: str) -> int:
    """output_dir 内の都道府県ごとのCSVをすべて連結し、1つのCSVにまとめる。

    処理済みの都道府県が増えるたびに呼び出すことで、実行の途中経過や
    中断からの再開時点でも常に最新の全国合計CSVを参照できるようにする。
    """
    all_rows: list[list[str]] = []
    for path in sorted(glob.glob(os.path.join(output_dir, "*.csv"))):
        with open(path, encoding="utf-8-sig", newline="") as f:
            reader = csv.reader(f)
            next(reader, None)  # ヘッダ行を読み飛ばす
            all_rows.extend(reader)

    with open(merged_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["会社名", "住所", "電話番号", "FAX番号", "取得元URL"])
        writer.writerows(all_rows)

    return len(all_rows)


def run_all_prefectures(browser, args: argparse.Namespace) -> None:
    """start_url を都道府県トップページとして扱い、全都道府県を順番に取得する。

    都道府県ごとに output_dir/<都道府県名>.csv として個別保存するため、
    - 実行を中断しても、既に終わった都道府県の結果は失われない
    - --force を付けなければ、既にファイルがある都道府県は再取得せず飛ばす
      (中断後の再実行がそのまま「続きから」になる)
    - 1都道府県の取得中にエラーが起きても、その都道府県だけスキップして
      残りの都道府県は継続する
    という形で長時間の全国走行に対応する。

    都道府県ごとに新しいブラウザページ(Page)を作り直して処理する。1つの
    都道府県でページ遷移がらみのエラーが起きた場合でも、そのブラウザ
    タブの状態(中途半端に開いたポップアップや遷移途中のDOM等)を次の
    都道府県に持ち越さないようにするため。
    """
    os.makedirs(args.output_dir, exist_ok=True)

    discovery_page = browser.new_page()
    try:
        prefectures = discover_prefecture_links(discovery_page, args.start_url)
    finally:
        discovery_page.close()

    print(f"[info] {len(prefectures)}/{len(PREFECTURES)} 都道府県のリンクを検出しました", file=sys.stderr)
    if len(prefectures) < len(PREFECTURES):
        missing = [name for name in PREFECTURES if name not in {n for n, _ in prefectures}]
        print(f"[warn] リンクが見つからなかった都道府県: {', '.join(missing)}", file=sys.stderr)

    if args.prefectures:
        wanted = [p.strip() for p in args.prefectures.split(",") if p.strip()]
        unknown = [p for p in wanted if p not in PREFECTURES]
        if unknown:
            print(
                f"[warn] --prefectures に含まれる次の名前は標準の都道府県名と一致しません: {', '.join(unknown)}",
                file=sys.stderr,
            )
        wanted_set = set(wanted)
        prefectures = [(name, url) for name, url in prefectures if name in wanted_set]
        not_found = wanted_set - {name for name, _ in prefectures}
        if not_found:
            print(
                f"[warn] --prefectures で指定されたが、一覧ページ上でリンクが見つからなかった都道府県: "
                f"{', '.join(sorted(not_found, key=PREFECTURES.index))}",
                file=sys.stderr,
            )
        print(f"[info] --prefectures により {len(prefectures)} 都道府県のみ処理します", file=sys.stderr)

    if args.max_prefectures:
        prefectures = prefectures[: args.max_prefectures]
        print(f"[info] --max-prefectures により先頭 {len(prefectures)} 都道府県のみ処理します", file=sys.stderr)

    for i, (name, url) in enumerate(prefectures, 1):
        pref_csv = os.path.join(args.output_dir, f"{name}.csv")
        if os.path.exists(pref_csv) and not args.force:
            print(
                f"[info] ({i}/{len(prefectures)}) {name}: 既存ファイルがあるためスキップします ({pref_csv})",
                file=sys.stderr,
            )
            continue

        print(f"[info] ({i}/{len(prefectures)}) {name} の取得を開始します: {url}", file=sys.stderr)
        page = browser.new_page()
        try:
            records = scrape_list_pages(page, url, args)
            save_csv(records, pref_csv)
            print(f"[info] {name}: {len(records)} 件を {pref_csv} に保存しました", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001 - 1都道府県の失敗で全体を止めないための意図的な広い捕捉
            print(f"[error] {name} の取得中にエラーが発生しました: {exc}", file=sys.stderr)
            print(f"[error] {name} をスキップして次の都道府県に進みます", file=sys.stderr)
        finally:
            page.close()

        total = merge_csvs(args.output_dir, args.output)
        print(f"[info] ここまでの全国合計を {args.output} に反映しました(現在 {total} 件)", file=sys.stderr)

        if i < len(prefectures):
            time.sleep(args.delay_between_prefectures)

    print("[done] 全都道府県の処理が完了しました", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "start_url",
        help="加盟店一覧ページのURL(都道府県等で絞り込み済みのもの)。"
        "--all-prefectures 指定時は都道府県トップページ(例: https://www.lixil-reform.net/shop_search)",
    )
    parser.add_argument("-o", "--output", default="lixil_shops.csv", help="出力CSVファイルパス")
    parser.add_argument("--contact-button-text", default="問い合わせする", help="電話/FAXを表示させるボタンの文言")
    parser.add_argument(
        "--close-selector",
        default="",
        help="ポップアップを閉じるボタンのCSS/テキストセレクタ(最優先で試す)。"
        "省略時は実サイトで確認済みの .close_btn 等を自動で試す",
    )
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
    parser.add_argument(
        "--all-prefectures",
        action="store_true",
        help="start_url を都道府県トップページ(例: https://www.lixil-reform.net/shop_search)として扱い、"
        "47都道府県すべてを順番に取得する",
    )
    parser.add_argument(
        "--output-dir",
        default="lixil_shops_by_pref",
        help="(--all-prefectures時) 都道府県ごとのCSVを保存するディレクトリ",
    )
    parser.add_argument(
        "--delay-between-prefectures",
        type=float,
        default=5.0,
        help="(--all-prefectures時) 都道府県間の待機時間(秒)",
    )
    parser.add_argument(
        "--max-prefectures",
        type=int,
        default=0,
        help="(--all-prefectures時) 処理する都道府県数の上限(先頭から)。0で無制限。動作確認用",
    )
    parser.add_argument(
        "--prefectures",
        default="",
        help="(--all-prefectures時) カンマ区切りで指定した都道府県だけを処理する"
        "(例: '佐賀県,長崎県,宮崎県,熊本県,鹿児島県,沖縄県')。省略時は全都道府県が対象",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="(--all-prefectures時) 既に output-dir にCSVがある都道府県も再取得する(既定はスキップして再開)",
    )
    args = parser.parse_args()

    with sync_playwright() as p:
        browser = launch_browser(p, headless=not args.headed)

        if args.all_prefectures:
            run_all_prefectures(browser, args)
            browser.close()
            return

        page = browser.new_page()
        records = scrape_list_pages(page, args.start_url, args)
        browser.close()

    if args.debug_first_card:
        return

    save_csv(records, args.output)
    print(f"[done] {len(records)} 件を {args.output} に保存しました", file=sys.stderr)


if __name__ == "__main__":
    main()
