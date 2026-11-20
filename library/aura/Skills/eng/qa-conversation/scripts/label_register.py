import json,os,sys,random,urllib.request,concurrent.futures as cf
from collections import Counter,defaultdict
KEY=os.environ["OPENAI_EVALUATIONS_API_KEY"]
sys.path.insert(0,os.path.dirname(os.path.abspath(__file__)))
import service_tier
# Eval traffic: nobody waits on it, so half price by default.
# Override with AINATIVE_OPENAI_SERVICE_TIER=standard|fast.
TIER=service_tier.wire_value()
SYS="""You are labelling ONE coach turn for a single defect. The coach is Aurie, a warm, perceptive companion — not a therapist.

DEFECT: therapist / coaching register. Present when the turn:
- narrates its own process ("what I'm noticing is...", "let's sit with that", "I'm curious what comes up")
- uses stock clinical or self-help framings in place of ordinary speech
- is conspicuously "therapy-speak" or reads as a formulation being delivered to a client
- supplies meaning the user has NOT supported, or stays abstract where the user's own concrete words were available

NOT the defect, and must never be labelled as such:
- earned interpretation: a formulation that sharpens or crystallises something the user has already revealed. This is TARGET behaviour. Example of good, non-defective interpretation: "the fast yes protects you in the moment, and the quiet resentment is the bill that shows up later."
- warmth, directness, a proposed next step, a named pattern the user themselves raised
- naming a feeling accurately in plain language

Judge ONLY the turn between >>> TARGET TURN <<< and >>> END TARGET <<<. Everything else is context only. The quote you return MUST come from inside the target turn.

Return ONLY JSON: {"defect": true|false, "quote": "<short verbatim span or empty>", "why": "<one clause>"}"""
if len(sys.argv)<2:
    raise SystemExit("usage: label_register.py <transcripts.json>")
F=json.load(open(sys.argv[1]))
REPS=3
jobs=[]
for k,seq in F.items():
    if k.split("|")[1] not in ("A","W","V"): continue
    for i,(r,_) in enumerate(seq):
        if r=="Aurie":
            for rep in range(REPS): jobs.append((rep,k,i,seq))
def call(job):
    rep,k,i,seq=job
    parts=[]
    for n,(r,t) in enumerate(seq):
        who="USER" if r=="User" else "COACH"
        parts.append(f">>> TARGET TURN <<<\n{who}: {t}\n>>> END TARGET <<<" if n==i else f"{who}: {t}")
    conv="\n\n".join(parts)
    body=json.dumps({"model":"gpt-5.4","reasoning_effort":"medium","service_tier":TIER,
      "messages":[{"role":"system","content":SYS},{"role":"user","content":conv}]}).encode()
    req=urllib.request.Request("https://api.openai.com/v1/chat/completions",data=body,
      headers={"Authorization":f"Bearer {KEY}","Content-Type":"application/json"})
    for a in range(4):
        try:
            r=json.load(urllib.request.urlopen(req,timeout=240))
            tx=r["choices"][0]["message"]["content"]; tx=tx[tx.find("{"):tx.rfind("}")+1]
            d=json.loads(tx); d.update(_id=k,turn=i,rep=rep); return d
        except Exception as e:
            if a==3:
                service_tier.warn_if_flex_refused(TIER,e)
                return {"_id":k,"turn":i,"rep":rep,"error":str(e)[:90]}
random.seed(4); random.shuffle(jobs)
print(f"{len(jobs)} calls ({REPS} reps)")
res=[]
with cf.ThreadPoolExecutor(max_workers=14) as ex:
    for d in ex.map(call,jobs): res.append(d); print(".",end="",flush=True)
print(); json.dump(res,open("v1c_scores4.json","w"),indent=1)
# majority vote per turn
maj=defaultdict(list)
for d in res:
    if "defect" in d: maj[(d["_id"],d["turn"])].append(d["defect"])
print(f"\n{'arm':16}{'turns':>7}{'flagged (majority of 3)':>26}{'rate':>9}{'unanimous':>11}")
for arm,lbl in [("A","prod"),("W","v3 drive A"),("V","v3 drive B")]:
    keys=[k for k in maj if k[0].endswith("|"+arm)]
    flag=[k for k in keys if sum(maj[k])>len(maj[k])/2]
    unan=sum(1 for k in keys if len(set(maj[k]))==1)
    print(f"{lbl:16}{len(keys):>7}{len(flag):>26}{len(flag)/len(keys)*100:>8.1f}%{unan}/{len(keys):>10}")
print("\nflagged turns (majority):")
for arm,lbl in [("A","prod"),("W","v3 drive A"),("V","v3 drive B")]:
    print(f"  -- {lbl} --")
    for k in sorted([k for k in maj if k[0].endswith('|'+arm) and sum(maj[k])>len(maj[k])/2]):
        ex=[d for d in res if d.get("_id")==k[0] and d.get("turn")==k[1] and d.get("defect")]
        q=ex[0].get("quote","")[:95] if ex else ""
        print(f"     {k[0]} t{k[1]}: {q}")
