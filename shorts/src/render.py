import json, subprocess, sys, os
from PIL import Image, ImageDraw, ImageFont

SRC = "/root/.claude/uploads/d74b9b63-dcea-554d-9e65-dea67768f92e/9bb089b2-MEO__AI________________.mp4"
FD = os.path.abspath("fonts")
BLACK, BOLD = f"{FD}/NotoSansJP-Black.otf", f"{FD}/NotoSansJP-Bold.otf"
W, H = 1080, 1920
NAVY, NAVY2, YEL = (24, 36, 104), (14, 22, 70), (255, 204, 0)
VY, VH = 520, 608          # video slot
CAP_Y = 1218               # caption center
OUT = sys.argv[1] if len(sys.argv) > 1 else "out"
os.makedirs(OUT, exist_ok=True)

def fit(draw, text, path, size, maxw):
    while True:
        f = ImageFont.truetype(path, size)
        if draw.textlength(text, font=f) <= maxw or size < 30:
            return f
        size -= 2

def ctext(d, y, text, path, size, fill, maxw=1000, stroke=0, sfill=None):
    f = fit(d, text, path, size, maxw)
    d.text((W // 2, y), text, font=f, fill=fill, anchor="mm", stroke_width=stroke, stroke_fill=sfill)
    return f

def overlay(c, path):
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    # top title panel
    for y in range(VY - 20):
        k = y / (VY - 20)
        d.line([(0, y), (W, y)], fill=tuple(int(NAVY2[i] + (NAVY[i] - NAVY2[i]) * k) for i in range(3)) + (240,))
    ctext(d, 255, c["t1"], BLACK, 104, "white", 1010, 6, NAVY2)
    ctext(d, 395, c["t2"], BLACK, 116, YEL, 1010, 6, NAVY2)
    # thin frame around video
    d.rectangle([0, VY - 6, W, VY - 1], fill=YEL)
    d.rectangle([0, VY + VH, W, VY + VH + 5], fill=YEL)
    # caption zone (darkened)
    d.rectangle([0, VY + VH + 6, W, 1318], fill=(0, 0, 0, 120))
    # yellow name band
    d.rectangle([0, 1318, W, 1418], fill=YEL)
    ctext(d, 1366, "18歳からMEO業界｜西 幸太郎", BLACK, 66, (17, 17, 17), 1020)
    # bottom panel with search CTA
    d.rectangle([0, 1418, W, H], fill=NAVY + (245,))
    ctext(d, 1478, "本編が気になる方は", BOLD, 46, "white")
    bx0, by0, bx1, by1 = 70, 1522, 1010, 1622
    d.rounded_rectangle([bx0, by0, bx1, by1], radius=50, fill="white", outline=YEL, width=5)
    # magnifier icon
    cx, cy = bx0 + 62, (by0 + by1) // 2 - 6
    d.ellipse([cx - 20, cy - 20, cx + 20, cy + 20], outline=NAVY, width=7)
    d.line([(cx + 14, cy + 14), (cx + 32, cy + 32)], fill=NAVY, width=9)
    f = fit(d, "MEO・AIO専門家 西 幸太郎", BLACK, 52, bx1 - bx0 - 150)
    d.text(((bx0 + 110 + bx1) // 2, (by0 + by1) // 2), "MEO・AIO専門家 西 幸太郎", font=f, fill=NAVY, anchor="mm")
    ctext(d, 1668, "で検索！", BOLD, 46, "white")
    im.save(path)

OVR = {'というテーマでお話ししていきます': 'というテーマで|お話ししていきます', '簡単な内容からお話ししたいんですけど': '簡単な内容から|お話ししたいんですけど', 'ご来店 お見積もりにつながる流れを': 'ご来店 お見積もりに|つながる流れを', 'いや でもこっちの方が安そうだしな': 'いや でもこっちの方が|安そうだしな', 'ここにお問い合わせしてみようかな': 'ここに|お問い合わせしてみようかな', '集客や売り上げにつながらないじゃないですか': '集客や売り上げに|つながらないじゃないですか', 'まとめサイトみたいなのにお金を払って': 'まとめサイトみたいなのに|お金を払って', 'ただ 今どう変わってるかっていうと': 'ただ 今どう|変わってるかっていうと', '勝手に読み取ってくれたりしてるので': '勝手に読み取って|くれたりしてるので', 'AIモードっていうのが搭載されたんですね': 'AIモードっていうのが|搭載されたんですね', '世の中に普及していってるわけじゃないですか': '世の中に普及していってる|わけじゃないですか', '増えていくんじゃないかっていうのはあるので': '増えていくんじゃないか|っていうのはあるので', 'Googleマップでどんな感じで': 'Googleマップで|どんな感じで', 'お客様が見つけているのかってのも': 'お客様が見つけているのか|ってのも', 'で 見たうえでやっぱ気になるのって': 'で 見たうえで|やっぱ気になるのって', 'さっきの伏線回収にもなるんですけど': 'さっきの伏線回収にも|なるんですけど', 'みたいなざっくり解説したじゃないですか': 'みたいなざっくり解説した|じゃないですか', 'AIとかGoogleマップとかSNSとか': 'AIとかGoogleマップとか|SNSとか', '経営者 忙しいと思うので多いと思うんですけど': '経営者 忙しいと思うので|多いと思うんですけど', 'AIにもおすすめされやすくなるんですね': 'AIにも|おすすめされやすくなるんですね', 'いわゆるゼロクリック検索の時代とも': 'いわゆる|ゼロクリック検索の時代とも', '僕もめっちゃ使ってるので分かるんですけど': '僕もめっちゃ使ってるので|分かるんですけど', 'いくつか会社出てきてるじゃないですか': 'いくつか会社|出てきてるじゃないですか', 'さっきの内容にもひもづくんですけれども': 'さっきの内容にも|ひもづくんですけれども', '知ってもらいたいわけじゃないですか': '知ってもらいたい|わけじゃないですか', 'いわゆるビジネスプロフィールがあるんです': 'いわゆる|ビジネスプロフィールがあるんです', '単純に MEOみたいな観点でいうと': '単純に MEOみたいな|観点でいうと', 'また ある一定の割合で「興味・関心」': 'また ある一定の割合で|「興味・関心」', 'もちろん完全比例というわけではないんですけど': 'もちろん完全比例という|わけではないんですけど'}

def wrap(text, n=15):
    if text in OVR:
        return OVR[text].replace('|', '\\N')
    flat = text.replace(" ", "")
    if len(flat) <= n:
        return text if len(flat) + text.count(" ") <= n else flat
    parts = text.split(" ")
    if len(parts) > 1:  # break at the space closest to the middle
        best = min(range(1, len(parts)), key=lambda i: abs(len("".join(parts[:i])) - len(flat) / 2))
        a, b = "".join(parts[:best]), "".join(parts[best:])
        if max(len(a), len(b)) <= n + 1 and min(len(a), len(b)) >= 4:
            return a + "\\N" + b
    L = len(flat)
    def cost(i):
        c = abs(i - L / 2)
        a, b = flat[i - 1], flat[i]
        if a.isascii() and a.isalnum() and b.isascii() and b.isalnum():
            c += 100  # never split an English word / number
        if b in "ぁぃぅぇぉっゃゅょゎァィゥェォッャュョーんン、。」）・":
            c += 50
        if a in "はがをにでのともや、」" and not ("ぁ" <= b <= "ゖ" and a in "でと"):
            c -= 4
        kata = lambda x: "ァ" <= x <= "ヺ" or x == "ー"
        hira = lambda x: "ぁ" <= x <= "ゖ"
        kanji = lambda x: "一" <= x <= "鿿"
        if (kata(a) or a.isascii()) and kata(b):
            c += 30  # inside a katakana word (incl. Googleマップ)
        if kanji(a) and kanji(b):
            c += 6
        if hira(b) and (hira(a) or kanji(a)) and a not in "はがをにでのともや":
            c += 3  # mid-verb / okurigana
        if b in "「（" or (b.isascii() and not a.isascii()):
            c -= 2
        return c
    m = min(range(max(1, L // 4), L - max(1, L // 4) + 1), key=cost)
    return flat[:m] + "\\N" + flat[m:]

def ts(t):
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}"

def ass(c, path):
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,Noto Sans JP Black,62,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,5,2,5,20,20,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    ev = [f"Dialogue: 0,{ts(a)},{ts(b)},Cap,,0,0,0,,{{\\pos(540,{CAP_Y})}}{wrap(t)}" for a, b, t in c["caps"]]
    open(path, "w").write(head + "\n".join(ev) + "\n")

def render(c):
    base = f"{OUT}/short{c['id']}"
    ov, sub = f"{OUT}/.ov{c['id']}.png", f"{OUT}/.sub{c['id']}.ass"
    overlay(c, ov); ass(c, sub)
    fc, n = [], len(c["segs"])
    for i, (s, e) in enumerate(c["segs"]):
        fc.append(f"[0:v]trim={s}:{e},setpts=PTS-STARTPTS,fps=30[v{i}]")
        fc.append(f"[0:a]atrim={s}:{e},asetpts=PTS-STARTPTS,afade=t=in:d=0.04,afade=t=out:st={e - s - 0.06:.3f}:d=0.06[a{i}]")
    fc.append("".join(f"[v{i}][a{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=1[vc][ac]")
    fc.append("[vc]split[va][vb]")
    fc.append(f"[va]scale=-2:{H},crop={W}:{H},gblur=sigma=28,eq=brightness=-0.12[bg]")
    fc.append(f"[vb]scale={W}:{VH}[fg]")
    fc.append(f"[bg][fg]overlay=0:{VY}[m]")
    fc.append(f"[m][1:v]overlay=0:0,subtitles={sub}:fontsdir={FD},format=yuv420p[vo]")
    fc.append("[ac]loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000[ao]")
    cmd = ["ffmpeg", "-v", "error", "-y", "-i", SRC, "-i", ov, "-filter_complex", ";".join(fc),
           "-map", "[vo]", "-map", "[ao]", "-c:v", "libx264", "-preset", "medium", "-crf", "20",
           "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", base + ".mp4"]
    subprocess.run(cmd, check=True)
    print("done", base)

plan = json.load(open("plan.json"))
only = sys.argv[2:]
for c in plan:
    if not only or c["id"] in only:
        render(c)
