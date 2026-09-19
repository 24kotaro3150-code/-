#!/usr/bin/env python3
"""
Google マップ検索(例: 「世田谷区 リフォーム」)に相当する店舗一覧を、
Google公式の Places API (New) を使って取得し、直近一定期間内に新しい
口コミが付いている店舗だけを絞り込んで CSV に出力するツール。

Googleマップの検索結果ページ自体を直接スクレイピングするのは
Googleの利用規約に反し、CAPTCHA等ですぐ止められる可能性が高いため、
公式APIであるPlaces API (New) を使う方式にしている。

処理の流れ:
  1. Text Search (New) で検索クエリに一致する店舗を検索し、店舗ID一覧を取得する
     (料金を抑えるため、この段階では id と表示名だけを取得)。
  2. 各店舗について Place Details (New) を呼び出し、会社名・住所・電話番号・
     評価・口コミ件数・口コミ(最大5件、投稿日付き)を取得する。
  3. 取得した口コミの中に、指定した期間(既定12か月)以内に投稿されたものが
     1件でもあれば「新着口コミあり」として結果に含める。
     ※ Places API で取得できる口コミは最大5件までのため、6件目以降に
        新しい口コミがあってもこの判定では見えない点に注意。

事前準備:
  - Google Cloud で課金を有効にしたプロジェクトを作成し、
    「Places API (New)」を有効化する(無料枠内でもプロジェクトへの
    課金設定自体は必要)。
  - APIキーを発行し、Places API (New) のみに使用制限をかけることを推奨。
  - 環境変数 GOOGLE_MAPS_API_KEY にAPIキーを設定するか、--api-key で渡す。

使い方の例:
  export GOOGLE_MAPS_API_KEY="xxxxxxxx"
  python3 place_reviews.py --query "世田谷区 リフォーム" \
      --max-results 100 --months 12 --output result.csv

  複数エリア/キーワードをまとめて集計する場合は --query を複数指定できる:
  python3 place_reviews.py \
      --query "世田谷区 リフォーム" --query "世田谷区 外壁塗装" \
      --max-results 100 --output result.csv

注意点:
  - Places API は従量課金。無料枠(Enterprise系SKUは月1,000件程度)を
    超えると課金される。100件程度のテストであれば通常は無料枠内に
    収まる想定だが、実際の料金は必ずGoogle Cloud側の請求状況で確認する
    こと。
  - 取得したデータを営業連絡に使う場合は、特定商取引法など関連法令を
    確認すること。
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import requests

TEXT_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
DETAILS_URL = "https://places.googleapis.com/v1/places/{place_id}"

# 検索段階では候補の絞り込みに使う最小限のフィールドだけを取得し、
# 料金の高いフィールド(電話番号・評価・口コミ等)は Place Details 側で
# まとめて取得する。
SEARCH_FIELD_MASK = "places.id,places.displayName"

DETAILS_FIELD_MASK = (
    "id,displayName,formattedAddress,nationalPhoneNumber,"
    "internationalPhoneNumber,rating,userRatingCount,reviews,googleMapsUri"
)


@dataclass
class ShopRecord:
    place_id: str
    company: str = ""
    phone: str = ""
    address: str = ""
    review_count: int = 0
    rating: float | None = None
    latest_review_at: str = ""
    maps_url: str = ""


def search_text_page(
    session: requests.Session, api_key: str, query: str, page_token: str | None
) -> dict:
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": SEARCH_FIELD_MASK + ",nextPageToken",
    }
    body: dict = {"textQuery": query, "languageCode": "ja", "regionCode": "JP"}
    if page_token:
        body["pageToken"] = page_token

    resp = session.post(TEXT_SEARCH_URL, json=body, headers=headers, timeout=20)
    if resp.status_code != 200:
        raise RuntimeError(f"Text Search 失敗 ({resp.status_code}): {resp.text[:500]}")
    return resp.json()


def iter_search_results(session: requests.Session, api_key: str, query: str, max_results: int):
    """1つの検索クエリについて、ページングしながら候補店舗IDを順に返す。"""
    page_token = None
    seen = 0
    while True:
        data = search_text_page(session, api_key, query, page_token)
        places = data.get("places", [])
        for place in places:
            if seen >= max_results:
                return
            place_id = place.get("id")
            if place_id:
                yield place_id
                seen += 1

        page_token = data.get("nextPageToken")
        if not page_token or seen >= max_results:
            return
        # 新しいページトークンはごく短時間は無効なことがあるため、
        # 少し待ってから使う(Google公式の注意事項に準拠)。
        time.sleep(2)


def get_place_details(session: requests.Session, api_key: str, place_id: str) -> dict:
    headers = {
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": DETAILS_FIELD_MASK,
    }
    url = DETAILS_URL.format(place_id=place_id)
    # languageCode を指定しないと店舗によって英語表記(ローマ字)の住所が
    # 返ってくることがあるため、明示的に日本語を指定する。
    params = {"languageCode": "ja", "regionCode": "JP"}
    resp = session.get(url, headers=headers, params=params, timeout=20)
    if resp.status_code != 200:
        raise RuntimeError(f"Place Details 失敗 ({resp.status_code}): {resp.text[:500]}")
    return resp.json()


def parse_publish_time(value: str) -> datetime | None:
    if not value:
        return None
    try:
        # Places API (New) は RFC3339 (例: "2025-11-02T03:15:21Z") で返す。
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def latest_review_within(reviews: list[dict], cutoff: datetime) -> datetime | None:
    """指定したcutoff以降に投稿された口コミがあれば、その中で最新の投稿日時を返す。"""
    latest: datetime | None = None
    for review in reviews:
        published = parse_publish_time(review.get("publishTime", ""))
        if published is None:
            continue
        if published >= cutoff and (latest is None or published > latest):
            latest = published
    return latest


def details_to_record(place_id: str, data: dict) -> ShopRecord:
    display_name = data.get("displayName", {})
    company = display_name.get("text", "") if isinstance(display_name, dict) else ""
    phone = data.get("nationalPhoneNumber") or data.get("internationalPhoneNumber") or ""
    return ShopRecord(
        place_id=place_id,
        company=company,
        phone=phone,
        address=data.get("formattedAddress", ""),
        review_count=data.get("userRatingCount", 0) or 0,
        rating=data.get("rating"),
        maps_url=data.get("googleMapsUri", ""),
    )


def save_csv(records: list[ShopRecord], output_path: str) -> None:
    with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["会社名", "電話番号", "住所", "口コミ件数", "平均評価", "直近1年以内の最新口コミ日", "Google マップURL"])
        for r in records:
            writer.writerow(
                [
                    r.company,
                    r.phone,
                    r.address,
                    r.review_count,
                    r.rating if r.rating is not None else "",
                    r.latest_review_at,
                    r.maps_url,
                ]
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--query",
        action="append",
        required=True,
        help="検索クエリ(例: '世田谷区 リフォーム')。複数回指定すると結果を合算する",
    )
    parser.add_argument("--api-key", default=os.environ.get("GOOGLE_MAPS_API_KEY", ""), help="Places APIキー")
    parser.add_argument("--max-results", type=int, default=100, help="クエリ全体で取得する最大件数")
    parser.add_argument("--months", type=int, default=12, help="この月数以内に口コミがある店舗だけを残す")
    parser.add_argument("--output", "-o", default="place_reviews.csv", help="出力CSVファイルパス")
    parser.add_argument("--delay", type=float, default=0.2, help="Place Details呼び出し間の待機時間(秒)")
    args = parser.parse_args()

    if not args.api_key:
        print(
            "[error] APIキーが指定されていません。--api-key か環境変数 GOOGLE_MAPS_API_KEY を設定してください。",
            file=sys.stderr,
        )
        sys.exit(1)

    cutoff = datetime.now(timezone.utc) - timedelta(days=int(365 * args.months / 12))

    session = requests.Session()

    seen_place_ids: set[str] = set()
    place_ids: list[str] = []
    for query in args.query:
        remaining = args.max_results - len(place_ids)
        if remaining <= 0:
            break
        print(f"[info] 検索中: {query!r}", file=sys.stderr)
        for pid in iter_search_results(session, args.api_key, query, remaining):
            if pid in seen_place_ids:
                continue
            seen_place_ids.add(pid)
            place_ids.append(pid)
            if len(place_ids) >= args.max_results:
                break
        print(f"[info] 現在の候補件数: {len(place_ids)}", file=sys.stderr)

    print(f"[info] 候補 {len(place_ids)} 件について詳細・口コミを取得します", file=sys.stderr)

    results: list[ShopRecord] = []
    for i, place_id in enumerate(place_ids, 1):
        try:
            data = get_place_details(session, args.api_key, place_id)
        except RuntimeError as exc:
            print(f"[warn] ({i}/{len(place_ids)}) {place_id}: 取得失敗 ({exc})", file=sys.stderr)
            time.sleep(args.delay)
            continue

        record = details_to_record(place_id, data)
        reviews = data.get("reviews", [])
        latest = latest_review_within(reviews, cutoff)

        if latest is not None:
            record.latest_review_at = latest.strftime("%Y-%m-%d")
            results.append(record)
            print(f"[info] ({i}/{len(place_ids)}) {record.company}: 対象(最新口コミ {record.latest_review_at})", file=sys.stderr)
        else:
            print(f"[info] ({i}/{len(place_ids)}) {record.company}: 対象外(期間内の口コミなし)", file=sys.stderr)

        time.sleep(args.delay)

    save_csv(results, args.output)
    print(f"[done] 条件に合致した {len(results)}/{len(place_ids)} 件を {args.output} に保存しました", file=sys.stderr)


if __name__ == "__main__":
    main()
