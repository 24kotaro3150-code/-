import sherpa_onnx, wave, numpy as np, json
D="sherpa-onnx-zipformer-ja-reazonspeech-2024-08-01/"
rec=sherpa_onnx.OfflineRecognizer.from_transducer(encoder=D+"encoder-epoch-99-avg-1.int8.onnx",decoder=D+"decoder-epoch-99-avg-1.onnx",joiner=D+"joiner-epoch-99-avg-1.int8.onnx",tokens=D+"tokens.txt",num_threads=4)
w=wave.open("audio.wav"); a=np.frombuffer(w.readframes(w.getnframes()),dtype=np.int16).astype(np.float32)/32768
old=json.load(open("transcript.json"))
# candidate cut points: midpoints of VAD gaps, prefer longer gaps
cuts=[((old[i]["end"]+old[i+1]["start"])/2, old[i+1]["start"]-old[i]["end"]) for i in range(len(old)-1)]
spans=[];s=0.0;T=len(a)/16000
while s<T-0.5:
  cand=[c for c in cuts if s+6<c[0]<=s+22]
  if not cand or T-s<22: e=T
  else:
    e=max(cand,key=lambda c:(c[1]>0.5, c[0]))[0]  # latest cut with a decent pause
  spans.append((s,e)); s=e
segs=[]
for s,e in spans:
  x=rec.create_stream(); x.accept_waveform(16000,a[int(s*16000):int(e*16000)]); rec.decode_stream(x); r=x.result
  segs.append({"start":s,"end":e,"text":r.text,"tokens":list(r.tokens),"ts":[s+t for t in r.timestamps]})
json.dump(segs,open("transcript.json","w"),ensure_ascii=False)
with open("transcript.txt","w") as f:
  for g in segs: f.write(f"[{g['start']:7.2f}-{g['end']:7.2f}] {g['text']}\n")
