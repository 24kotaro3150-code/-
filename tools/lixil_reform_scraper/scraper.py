#!/usr/bin/env python3
"""
lixil-reform.net (LIXILリフォームネット) の加盟店一覧ページから
会社名・住所・電話番号・FAX番号を収集する汎用スクレイパー。

サイトの正確なHTML構造が未確認のため、次の2段階で動作する:
  1. URL収集: サイトマップ(sitemap.xml)または一覧ページの巡回から、
     加盟店詳細ページと思われるURLを集める。
  2. 情報抽出: 各詳細ページを、ラベル文字列(会社名/住所/TEL/FAX等)を
     手がかりに解析し、隣接する値を取り出す。dt/dd, th/td, 通常テキスト
     の3パターンに対応。

実際のHTML構造に合わせて --detail-url-pattern や --field-labels 等を
調整すること。まずは --debug-url で1ページ取得し、抽出結果を確認してから
本走行することを推奨する。

利用にあたっての注意:
  - 本スクリプトは既定で robots.txt を尊重し、Disallow されたパスは
    取得しない。
  - 既定のリクエスト間隔は 1.5 秒。対象サーバーに過度な負荷をかけない
    よう、間隔を詰めすぎないこと。
  - 取得したデータの利用(営業連絡・FAX送信等)は、特定商取引法など
    関連法令および対象サイトの利用規約に従うこと。判断は利用者の責任で
    行うこと。
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
import time
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser
from xml.etree import ElementTree

import requests
from bs4 import BeautifulSoup

DEFAULT_USER_AGENT = "Mozilla/5.0 (compatible; ResearchBot/1.0)"

FIELD_LABELS = {
    "company": ["会社名", "商号", "店舗名", "社名", "屋号"],
    "address": ["住所", "所在地"],
    "phone": ["電話番号", "電話", "TEL", "Tel", "tel"],
    "fax": ["FAX番号", "FAX", "Fax", "fax"],
}


@dataclass
class ScraperConfig:
    start_url: str
    output: str
    delay: float = 1.5
    max_pages: int = 0  # 0 = unlimited
    user_agent: str = DEFAULT_USER_AGENT
    detail_url_pattern: str = r".+"
    next_page_selector: str = 'a[rel="next"]'
    item_link_selector: str = "a"
    timeout: int = 20
    max_retries: int = 3


@dataclass
class ShopRecord:
    url: str
    company: str = ""
    address: str = ""
    phone: str = ""
    fax: str = ""


class PoliteFetcher:
    """robots.txt を尊重し、リクエスト間隔を空けながら取得するクライアント。"""

    def __init__(self, user_agent: str, delay: float, timeout: int, max_retries: int):
        self.user_agent = user_agent
        self.delay = delay
        self.timeout = timeout
        self.max_retries = max_retries
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent})
        self._robots_cache: dict[str, RobotFileParser] = {}
        self._last_request_ts = 0.0

    def _robots_for(self, url: str) -> RobotFileParser:
        origin = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
        if origin not in self._robots_cache:
            rp = RobotFileParser()
            rp.set_url(urljoin(origin, "/robots.txt"))
            try:
                rp.read()
            except Exception:
                # robots.txt が読めない場合は安全側に倒し、取得を許可しない。
                rp.disallow_all = True
            self._robots_cache[origin] = rp
        return self._robots_cache[origin]

    def allowed(self, url: str) -> bool:
        rp = self._robots_for(url)
        try:
            return rp.can_fetch(self.user_agent, url)
        except Exception:
            return False

    def get(self, url: str) -> requests.Response | None:
        if not self.allowed(url):
            print(f"[skip] robots.txt により禁止: {url}", file=sys.stderr)
            return None

        wait = self.delay - (time.monotonic() - self._last_request_ts)
        if wait > 0:
            time.sleep(wait)

        last_exc = None
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = self.session.get(url, timeout=self.timeout)
                self._last_request_ts = time.monotonic()
                if resp.status_code == 200:
                    return resp
                if resp.status_code in (429, 503):
                    backoff = self.delay * (2**attempt)
                    print(
                        f"[retry] {resp.status_code} {url} -> {backoff:.1f}s待機",
                        file=sys.stderr,
                    )
                    time.sleep(backoff)
                    continue
                print(f"[warn] HTTP {resp.status_code}: {url}", file=sys.stderr)
                return None
            except requests.RequestException as exc:
                last_exc = exc
                backoff = self.delay * (2**attempt)
                print(f"[retry] {exc} -> {backoff:.1f}s待機", file=sys.stderr)
                time.sleep(backoff)
        print(f"[error] 取得失敗: {url} ({last_exc})", file=sys.stderr)
        return None


def discover_from_sitemap(fetcher: PoliteFetcher, base_url: str, url_pattern: str) -> list[str]:
    """sitemap.xml (サイトマップインデックス含む) からURLを収集する。"""
    origin = f"{urlparse(base_url).scheme}://{urlparse(base_url).netloc}"
    to_visit = [urljoin(origin, "/sitemap.xml")]
    seen_sitemaps: set[str] = set()
    pattern = re.compile(url_pattern)
    found: list[str] = []

    while to_visit:
        sm_url = to_visit.pop()
        if sm_url in seen_sitemaps:
            continue
        seen_sitemaps.add(sm_url)

        resp = fetcher.get(sm_url)
        if resp is None:
            continue
        try:
            root = ElementTree.fromstring(resp.content)
        except ElementTree.ParseError:
            continue

        ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        # サイトマップインデックス
        for sitemap in root.findall("sm:sitemap/sm:loc", ns):
            if sitemap.text:
                to_visit.append(sitemap.text.strip())
        # 通常のURLエントリ
        for loc in root.findall("sm:url/sm:loc", ns):
            if loc.text and pattern.search(loc.text):
                found.append(loc.text.strip())

    return sorted(set(found))


def discover_from_listing(fetcher: PoliteFetcher, cfg: ScraperConfig) -> list[str]:
    """一覧ページをページネーションに沿って巡回し、詳細ページURLを収集する。"""
    pattern = re.compile(cfg.detail_url_pattern)
    found: set[str] = set()
    url = cfg.start_url
    visited_pages = 0

    while url:
        if cfg.max_pages and visited_pages >= cfg.max_pages:
            break
        resp = fetcher.get(url)
        visited_pages += 1
        if resp is None:
            break
        soup = BeautifulSoup(resp.text, "html.parser")

        for a in soup.select(cfg.item_link_selector):
            href = a.get("href")
            if not href:
                continue
            abs_url = urljoin(url, href)
            if pattern.search(abs_url):
                found.add(abs_url)

        next_link = soup.select_one(cfg.next_page_selector)
        url = urljoin(url, next_link["href"]) if next_link and next_link.get("href") else None

    return sorted(found)


def _extract_by_dt_dd(soup: BeautifulSoup, labels: list[str]) -> str:
    for dt in soup.find_all("dt"):
        text = dt.get_text(strip=True)
        if any(label in text for label in labels):
            dd = dt.find_next_sibling("dd")
            if dd:
                return dd.get_text(" ", strip=True)
    return ""


def _extract_by_th_td(soup: BeautifulSoup, labels: list[str]) -> str:
    for th in soup.find_all("th"):
        text = th.get_text(strip=True)
        if any(label in text for label in labels):
            td = th.find_next_sibling("td")
            if td:
                return td.get_text(" ", strip=True)
    return ""


def _extract_by_regex(page_text: str, labels: list[str]) -> str:
    for label in labels:
        m = re.search(rf"{re.escape(label)}[\s:：]*\s*([^\n]+)", page_text)
        if m:
            return m.group(1).strip()
    return ""


def extract_shop_info(url: str, html: bytes | str) -> ShopRecord:
    # bytesを渡すことで、HTTPヘッダにcharsetが無くても<meta charset>から
    # bs4が文字コードを検出できるようにする(日本語サイトでの文字化け対策)。
    soup = BeautifulSoup(html, "html.parser")
    page_text = soup.get_text("\n", strip=True)
    record = ShopRecord(url=url)

    for field_name, labels in FIELD_LABELS.items():
        value = (
            _extract_by_dt_dd(soup, labels)
            or _extract_by_th_td(soup, labels)
            or _extract_by_regex(page_text, labels)
        )
        setattr(record, field_name, value)

    return record


def save_csv(records: list[ShopRecord], output_path: str) -> None:
    with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["会社名", "住所", "電話番号", "FAX番号", "取得元URL"])
        for r in records:
            writer.writerow([r.company, r.address, r.phone, r.fax, r.url])


def run_debug(fetcher: PoliteFetcher, url: str) -> None:
    resp = fetcher.get(url)
    if resp is None:
        print("取得に失敗しました。", file=sys.stderr)
        return
    record = extract_shop_info(url, resp.content)
    print(f"URL     : {record.url}")
    print(f"会社名  : {record.company or '(未検出)'}")
    print(f"住所    : {record.address or '(未検出)'}")
    print(f"電話番号: {record.phone or '(未検出)'}")
    print(f"FAX番号 : {record.fax or '(未検出)'}")
    print(
        "\n値が正しく取れない場合は、対象ページのHTMLを確認し "
        "extract_shop_info() の抽出ロジックを調整してください。"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("start_url", help="巡回を開始するURL(一覧ページ、またはトップページ)")
    parser.add_argument("-o", "--output", default="lixil_shops.csv", help="出力CSVファイルパス")
    parser.add_argument("--mode", choices=["sitemap", "listing"], default="sitemap", help="URL収集方式")
    parser.add_argument("--detail-url-pattern", default=r".+", help="加盟店詳細ページと判定する正規表現")
    parser.add_argument("--item-link-selector", default="a", help="(listingモード) 一覧ページ内のリンクCSSセレクタ")
    parser.add_argument("--next-page-selector", default='a[rel="next"]', help="(listingモード) 次ページリンクのCSSセレクタ")
    parser.add_argument("--delay", type=float, default=1.5, help="リクエスト間隔(秒)")
    parser.add_argument("--max-pages", type=int, default=0, help="(listingモード) 巡回する一覧ページ数の上限。0で無制限")
    parser.add_argument("--user-agent", default=DEFAULT_USER_AGENT, help="送信するUser-Agent")
    parser.add_argument("--debug-url", help="このURLを1件だけ取得し、抽出結果を表示して終了する")
    args = parser.parse_args()

    cfg = ScraperConfig(
        start_url=args.start_url,
        output=args.output,
        delay=args.delay,
        max_pages=args.max_pages,
        user_agent=args.user_agent,
        detail_url_pattern=args.detail_url_pattern,
        next_page_selector=args.next_page_selector,
        item_link_selector=args.item_link_selector,
    )
    fetcher = PoliteFetcher(cfg.user_agent, cfg.delay, cfg.timeout, cfg.max_retries)

    if args.debug_url:
        run_debug(fetcher, args.debug_url)
        return

    if args.mode == "sitemap":
        urls = discover_from_sitemap(fetcher, cfg.start_url, cfg.detail_url_pattern)
    else:
        urls = discover_from_listing(fetcher, cfg)

    print(f"[info] 詳細ページ候補 {len(urls)} 件を検出", file=sys.stderr)

    records: list[ShopRecord] = []
    for i, url in enumerate(urls, 1):
        resp = fetcher.get(url)
        if resp is None:
            continue
        records.append(extract_shop_info(url, resp.content))
        if i % 20 == 0:
            print(f"[info] {i}/{len(urls)} 件処理済み", file=sys.stderr)

    save_csv(records, cfg.output)
    print(f"[done] {len(records)} 件を {cfg.output} に保存しました", file=sys.stderr)


if __name__ == "__main__":
    main()
