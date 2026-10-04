import json, re, difflib

def stream(path):
    chars = []  # (char, start, end)
    for s in json.load(open(path)):
        toks, ts = s["tokens"], s["ts"]
        for i, (t, a) in enumerate(zip(toks, ts)):
            nxt = ts[i + 1] if i + 1 < len(ts) else min(a + 0.5, s["end"])
            nxt = min(nxt, a + 0.6)
            t = t.replace(" ", "").replace("▁", "")
            for j, ch in enumerate(t):
                chars.append((ch, a + (nxt - a) * j / len(t), a + (nxt - a) * (j + 1) / len(t)))
    return chars

STREAMS = [stream("transcript.json"), stream("transcript_vad.json")]
NORM = re.compile(r"[\s　「」・、。？！!?〜]")

def align_part(t, lines):
    texts = [NORM.sub("", l) for l in lines]
    mine = "".join(texts)
    est = len(mine) / 5 + 15
    best = None
    for st in STREAMS:
        win = [c for c in st if t - 2 <= c[1] <= t + est]
        sm = difflib.SequenceMatcher(None, mine, "".join(c[0] for c in win), autojunk=False)
        blocks = [b for b in sm.get_matching_blocks() if b.size >= 2]
        score = sum(b.size for b in blocks)
        if best is None or score > best[0]:
            m = {}
            for b in blocks:
                for k in range(b.size):
                    m[b.a + k] = win[b.b + k]
            best = (score, m)
    score, m = best
    out, pos = [], 0
    for l, tx in zip(lines, texts):
        hits = [m[i] for i in range(pos, pos + len(tx)) if i in m]
        out.append([l, hits[0][1] if hits else None, hits[-1][2] if hits else None])
        pos += len(tx)
    # fill gaps by interpolation, enforce monotonic
    for i, o in enumerate(out):
        if o[1] is None:
            prev = out[i - 1][2] if i else t
            nx = next((x[1] for x in out[i + 1:] if x[1] is not None), prev + 1.5)
            o[1], o[2] = prev, max(prev + 0.6, nx)
    for i in range(1, len(out)):
        if out[i][1] < out[i - 1][1]:
            out[i][1] = out[i - 1][2]
            out[i][2] = max(out[i][2], out[i][1] + 0.5)
    return out, score / max(1, len(mine))

clips = json.load(open("clips.json"))
for c in clips:
    segs, caps, T = [], [], 0.0
    for p in c["parts"]:
        al, q = align_part(p["t"], p["lines"])
        s0, s1 = al[0][1] - 0.15, al[-1][2] + 0.3
        segs.append([round(s0, 2), round(s1, 2)])
        for i, (l, a, b) in enumerate(al):
            nxt = al[i + 1][1] if i + 1 < len(al) else s1
            end = nxt if nxt - b < 0.8 else b + 0.3
            caps.append([round(T + a - s0, 2), round(T + end - s0, 2), l])
        T += s1 - s0
        print(c["id"], f"part {p['t']}: {s0:.2f}-{s1:.2f} match={q:.2f}")
    c["segs"], c["caps"], c["dur"] = segs, caps, round(T, 2)
    print(c["id"], "duration", round(T, 1))
json.dump(clips, open("plan.json", "w"), ensure_ascii=False, indent=1)
