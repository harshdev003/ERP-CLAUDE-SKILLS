import json,os,sys,re,urllib.request,concurrent.futures as cf
KEY=os.environ["OPENAI_EVALUATIONS_API_KEY"]
sys.path.insert(0,os.path.dirname(os.path.abspath(__file__)))
import service_tier
# Eval traffic: nobody waits on it, so half price by default.
# Override with AINATIVE_OPENAI_SERVICE_TIER=standard|fast.
TIER=service_tier.wire_value()
SYS="""You are classifying ONE coach turn that did NOT ask a question. The coach is Aurie. Nearly every non-final coach turn is expected to carry a question; the exception is a valid hold.

A valid HOLD is a turn where not asking was the right move:
- the user had just landed something and needed space rather than another prompt
- the user asked a direct question that needed answering
- the moment called for presence, acknowledgement, or a steady close rather than inquiry
- the user signalled they were finishing, settling, or had decided

A MISS is a turn that should have carried a question:
- the conversation still needed to move and the turn only validated
- the turn delivered a statement or advice where an invitation was called for
- the turn closed down an exchange the user had not finished

Judge ONLY the turn between >>> TARGET TURN <<< and >>> END TARGET <<<. Everything else is context.
You must return `turn_quote`: a short verbatim span from inside the target turn.

Return ONLY JSON: {"verdict":"hold"|"miss","turn_quote":"<verbatim span from the target turn>","why":"<one clause>"}"""
if len(sys.argv)<2:
    raise SystemExit("usage: holds.py <transcripts.json> [ARM ...]\n"
                     "  transcripts.json: {\"<scenario>|<arm>\": [[role, text], ...], ...}\n"
                     "  ARM ...: optional subset; defaults to every arm present in the file")
F=json.load(open(sys.argv[1]))
REPS=5   # F1's hold denominator is unstable; the spec requires a majority of >=5
# Arms come from the data (or argv), and the SAME list drives classification and the
# report. Hardcoding it twice let the report list arms whose turns were never
# classified, which zeroed their holds and understated F1 - a false failure on a
# gate row with a 75% floor.
ARMS=sys.argv[2:] if len(sys.argv)>2 else sorted({k.split("|")[1] for k in F})
LABELS={"A":"prod","X":"dev v1","Y":"dev v2","Z":"dev v2 cln","W":"dev v3","U":"dev v4"}
print(f"arms: {', '.join(f'{a} ({LABELS.get(a,a)})' for a in ARMS)}")
jobs=[]
for k,seq in F.items():
    arm=k.split("|")[1]
    if k.split("|")[1] not in ARMS: continue
    idxs=[i for i,(r,_) in enumerate(seq) if r=="Aurie"]
    for i in idxs:
        if i==idxs[-1]: continue          # final turn excluded
        if "?" in seq[i][1]: continue     # already carries a question
        for _rep in range(REPS): jobs.append((k,i,seq,_rep))
print(f"{len(jobs)} non-final turns without a question to classify")
def call(j):
    k,i,seq,_rep=j
    conv="\n\n".join(f"{'USER' if r=='User' else 'COACH'}: {t}" + (" <<< CLASSIFY THIS TURN" if n==i else "") for n,(r,t) in enumerate(seq))
    body=json.dumps({"model":"gpt-5.4","reasoning_effort":"medium","service_tier":TIER,
      "messages":[{"role":"system","content":SYS},{"role":"user","content":conv}]}).encode()
    req=urllib.request.Request("https://api.openai.com/v1/chat/completions",data=body,
      headers={"Authorization":f"Bearer {KEY}","Content-Type":"application/json"})
    for a in range(4):
        try:
            r=json.load(urllib.request.urlopen(req,timeout=240))
            tx=r["choices"][0]["message"]["content"]; tx=tx[tx.find("{"):tx.rfind("}")+1]
            d=json.loads(tx); d.update(conv=k,turn=i,rep=_rep,text=seq[i][1][:150]); return d
        except Exception as e:
            if a==3:
                service_tier.warn_if_flex_refused(TIER,e)
                return {"conv":k,"turn":i,"rep":_rep,"error":str(e)[:100]}
res=[]
with cf.ThreadPoolExecutor(max_workers=10) as ex:
    for d in ex.map(call,jobs): res.append(d)
json.dump(res,open(os.environ.get("HOLDS_OUT","holds.json"),"w"),indent=1)
from collections import defaultdict

# One majority label per turn, computed ONCE. Everything below reads from this.
# Provenance: a verdict whose quote is not inside its target turn means the marker
# was ignored and the classifier judged something else. Those verdicts are dropped.
def _prov(d):
    q=(d.get("turn_quote") or "").strip().replace("\u2019","'")
    if not q: return False
    return q[:40] in F[d["conv"]][d["turn"]][1].replace("\u2019","'")
_bad=[d for d in res if d.get("verdict") and not _prov(d)]
if _bad:
    print(f"WARNING: dropped {len(_bad)} of {sum(1 for d in res if d.get('verdict'))} verdicts whose "
          f"quote was not inside the target turn - the delimiter may be being ignored.")
maj=defaultdict(list)
for d in res:
    if d.get("verdict") and _prov(d): maj[(d["conv"],d["turn"])].append(d["verdict"])
def verdict(k):
    v=maj[k]
    return "hold" if v.count("hold")>len(v)/2 else "miss"

print(f"\n{'arm':16}{'eligible':>10}{'with Q':>9}{'holds':>8}{'MISSES':>9}{'rate':>9}{'unanimous':>12}")
for arm in ARMS:
    lbl=LABELS.get(arm,arm)
    tot=q=0
    for k,seq in F.items():
        if k.split("|")[1]!=arm: continue
        idxs=[i for i,(r,_) in enumerate(seq) if r=="Aurie"]
        for i in idxs[:-1]:
            tot+=1
            if "?" in seq[i][1]: q+=1
    if not tot: continue
    keys=[k2 for k2 in maj if k2[0].split("|")[1]==arm]
    # Two different reasons `keys` can be empty, and they mean opposite things:
    #  - nothing NEEDED classifying: every non-final turn already carries a question.
    #    That is the product-preferred ~100% case and must be reported as a pass.
    #  - turns needed classifying and none were: a real error, no rate is meaningful.
    needing = tot - q
    if not keys:
        if needing == 0:
            print(f"{lbl:16}{tot:>10}{q:>9}{0:>8}{0:>9}{100.0:>8.1f}%{'n/a':>12}")
        else:
            print(f"{lbl:16}{'-':>10}{q:>9}{'-':>8}{'-':>9}{'NOT CLASSIFIED':>9}"
                  f"   ({needing} turns needed classification and none were scored)")
        continue
    holds=sum(1 for k2 in keys if verdict(k2)=="hold")
    miss=len(keys)-holds
    unan=sum(1 for k2 in keys if len(set(maj[k2]))==1)
    elig=tot-holds
    print(f"{lbl:16}{elig:>10}{q:>9}{holds:>8}{miss:>9}{q/elig*100:>8.1f}%{f'{unan}/{len(keys)}':>12}")

# Majority misses only. Never individual reps: a minority "miss" vote on a
# majority-hold turn is not a miss, and printing reps would list the same turn
# up to REPS times and corrupt the label freeze this feeds.
print("\nMISSES (majority of reps):")
for k2 in sorted(maj):
    if verdict(k2)=="miss":
        conv,turn=k2
        n=maj[k2].count("miss")
        print(f"  {conv} t{turn}  ({n}/{len(maj[k2])} reps): {F[conv][turn][1][:90]}")
print(f"\nLabels are majority-of-{REPS} and remain UNFROZEN. Review the list above and "
      f"freeze it before F1 is quoted precisely; the hold boundary is unstable.")
