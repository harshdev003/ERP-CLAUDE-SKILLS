import json,os,sys,random,urllib.request,concurrent.futures as cf,statistics as st,itertools
KEY=os.environ["OPENAI_EVALUATIONS_API_KEY"]
sys.path.insert(0,os.path.dirname(os.path.abspath(__file__)))
import service_tier
# Eval traffic: nobody waits on it, so half price by default.
# Override with AINATIVE_OPENAI_SERVICE_TIER=standard|fast.
TIER=service_tier.wire_value()
GUARD="""IMPORTANT: perceptive meaning-making is part of this coach's intended voice. Do NOT penalise interpretation as such. A formulation that crystallises something the user has already revealed — for example "the fast yes protects you in the moment, and the quiet resentment is the bill that shows up later" — is target behaviour and should score WELL. Penalise interpretation only when it is unsupported by what the user said, needlessly abstract, clinical, or canned."""
DIMS={
"v1a":("CONVERSATIONAL NATURALNESS","""**1** Scripted coaching language: recurring stock constructions, near-identical turn shapes, phrasing that could have been sent to anyone.
**3** Mostly natural but with a visible formula, or generic phrasing where the user's own words were available.
**5** Sounds like something a warm, articulate person could actually say in this conversation: picks up the user's specific words, varies its shape from turn to turn, answers what was just said."""),
"v1b":("AURIE VOICE","""**1** Competent but anonymous — nothing perceived, or warmth with no insight in it.
**3** Warm and relational but generic; meaning-making absent or diffuse.
**5** Sees something real and says it compactly: perceptive, warm, grounded, memorable — without sounding like a clinical formulation being delivered."""),
}
if len(sys.argv)<2:
    raise SystemExit("usage: judge_v1ab.py <transcripts.json>")
F=json.load(open(sys.argv[1]))
items=[(k,v) for k,v in F.items() if k.split("|")[1] in ("A","W","V")]
REPS=5
def call(job):
    rep,dim,k,seq=job
    name,anchors=DIMS[dim]
    sys=f"""You are scoring one coaching conversation on ONE dimension only. The coach is Aurie.

{name}
{anchors}

{GUARD}

Score the whole conversation. Return ONLY JSON: {{"score": <1-5>, "evidence": "<short verbatim quote>"}}"""
    body=json.dumps({"model":"gpt-5.4","reasoning_effort":"medium","service_tier":TIER,
      "messages":[{"role":"system","content":sys},
        {"role":"user","content":"CONVERSATION:\n\n"+"\n\n".join(f"{'USER' if r=='User' else 'COACH'}: {t}" for r,t in seq)}]}).encode()
    req=urllib.request.Request("https://api.openai.com/v1/chat/completions",data=body,
      headers={"Authorization":f"Bearer {KEY}","Content-Type":"application/json"})
    for a in range(4):
        try:
            r=json.load(urllib.request.urlopen(req,timeout=240))
            tx=r["choices"][0]["message"]["content"]; tx=tx[tx.find("{"):tx.rfind("}")+1]
            d=json.loads(tx); d.update(_id=k,dim=dim,rep=rep); return d
        except Exception as e:
            if a==3:
                service_tier.warn_if_flex_refused(TIER,e)
                return {"_id":k,"dim":dim,"rep":rep,"error":str(e)[:100]}
jobs=[(r,d,k,v) for r in range(REPS) for d in DIMS for k,v in items]
random.seed(21); random.shuffle(jobs)
print(f"{len(jobs)} calls ({REPS} reps x {len(DIMS)} dims x {len(items)} conversations)")
res=[]
with cf.ThreadPoolExecutor(max_workers=14) as ex:
    for d in ex.map(call,jobs): res.append(d); print(".",end="",flush=True)
print(); json.dump(res,open("v1ab_scores_b.json","w"),indent=1)
LB=[("A","prod"),("W","v3 drive A"),("V","v3 drive B")]
for dim in DIMS:
    print(f"\n=== {DIMS[dim][0]} ===")
    print(f"{'arm':14}{'per-run means':32}{'pooled':>8}{'SD':>7}{'5/5':>7}{'<=3':>7}")
    P={}
    for arm,lbl in LB:
        means=[]
        for r in range(REPS):
            v=[x["score"] for x in res if x.get("rep")==r and x.get("dim")==dim and x.get("_id","").endswith("|"+arm) and "score" in x]
            if v: means.append(st.mean(v))
        allv=[x["score"] for x in res if x.get("dim")==dim and x.get("_id","").endswith("|"+arm) and "score" in x]
        P[arm]=(st.mean(allv), st.stdev(means) if len(means)>1 else 0)
        print(f"{lbl:14}{'  '.join(f'{m:.2f}' for m in means):32}{st.mean(allv):>8.2f}{P[arm][1]:>7.3f}"
              f"{sum(1 for x in allv if x==5)/len(allv)*100:>6.0f}%{sum(1 for x in allv if x<=3)/len(allv)*100:>6.0f}%")
    for (a,la),(b,lb) in itertools.combinations(LB,2):
        gap=abs(P[a][0]-P[b][0]); sd=(P[a][1]+P[b][1])/2 or 1e-9
        print(f"   {la:12} vs {lb:12} gap {gap:.2f}  ratio to run SD {gap/sd:.1f}")
