// Google Places API (Place Details) から実際の口コミを取得して「お客様の声」に表示します。
// 差し替えが必要な項目は下の2つだけです。
//   1) GOOGLE_PLACE_ID … Google Place ID Finder (https://developers.google.com/maps/documentation/places/web-service/place-id-finder) で取得
//   2) index.html 末尾の Maps JavaScript API の script タグの key= 部分 … Google Cloud で発行したAPIキー（Places API有効・HTTPリファラー制限推奨）
// 仕様上、Places APIで取得できるのは最新・関連度の高い口コミ最大5件までです（Google公式の上限）。
// 全件を見たい場合は「Googleの口コミをすべて見る」ボタンからGoogleマップへ遷移します。
const GOOGLE_PLACE_ID = "YOUR_PLACE_ID";

function initGoogleReviews() {
  if (!window.google || !google.maps || !google.maps.places) {
    showVoiceFallback();
    return;
  }
  const service = new google.maps.places.PlacesService(document.createElement("div"));
  service.getDetails(
    { placeId: GOOGLE_PLACE_ID, fields: ["rating", "user_ratings_total", "reviews"] },
    (place, status) => {
      if (status !== google.maps.places.PlacesServiceStatus.OK || !place || !place.reviews || !place.reviews.length) {
        showVoiceFallback();
        return;
      }
      renderVoiceSummary(place);
      renderVoiceCards(place.reviews);
    }
  );
}

function renderVoiceSummary(place) {
  const summaryEl = document.getElementById("voiceSummary");
  if (!summaryEl) return;
  const rating = place.rating ? place.rating.toFixed(1) : "-";
  const rounded = Math.round(place.rating || 0);
  const total = place.user_ratings_total || 0;
  summaryEl.innerHTML =
    '<span class="voice-rating">' + rating + '</span>' +
    '<span class="voice-stars">' + "★".repeat(rounded) + "☆".repeat(5 - rounded) + '</span>' +
    '<span class="voice-total">Googleの口コミ ' + total + '件</span>';
}

function renderVoiceCards(reviews) {
  const target = document.getElementById("voiceScroll");
  if (!target) return;
  target.innerHTML = "";
  reviews.forEach((r) => {
    const card = document.createElement("div");
    card.className = "voice-card";
    const initial = (r.author_name || "?").charAt(0);
    const stars = "★".repeat(r.rating || 0) + "☆".repeat(5 - (r.rating || 0));
    const name = document.createElement("p");
    name.className = "voice-name";
    name.textContent = r.author_name || "Google ユーザー";
    const time = document.createElement("p");
    time.className = "voice-time";
    time.textContent = r.relative_time_description || "";
    const starsEl = document.createElement("p");
    starsEl.className = "voice-stars";
    starsEl.textContent = stars;
    const text = document.createElement("p");
    text.className = "voice-text";
    text.textContent = r.text || "";

    const head = document.createElement("div");
    head.className = "voice-card-head";
    const avatar = document.createElement("div");
    avatar.className = "voice-avatar";
    avatar.textContent = initial;
    const nameWrap = document.createElement("div");
    nameWrap.appendChild(name);
    nameWrap.appendChild(time);
    head.appendChild(avatar);
    head.appendChild(nameWrap);

    card.appendChild(head);
    card.appendChild(starsEl);
    card.appendChild(text);
    target.appendChild(card);
  });
}

function showVoiceFallback() {
  const target = document.getElementById("voiceScroll");
  const summaryEl = document.getElementById("voiceSummary");
  if (summaryEl) summaryEl.innerHTML = "";
  if (target) {
    target.innerHTML = '<p class="voice-fallback">口コミを読み込めませんでした。下のボタンからGoogleマップでご確認ください。</p>';
  }
}

window.gm_authFailure = showVoiceFallback;

document.addEventListener("DOMContentLoaded", () => {
  setTimeout(() => {
    const loading = document.getElementById("voiceLoading");
    if (loading && loading.isConnected) showVoiceFallback();
  }, 6000);
});
