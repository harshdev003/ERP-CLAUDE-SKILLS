import json,os,sys,random,urllib.request,concurrent.futures as cf,statistics as st,itertools
KEY=os.environ["OPENAI_EVALUATIONS_API_KEY"]
sys.path.insert(0,os.path.dirname(os.path.abspath(__file__)))
import service_tier
# Eval traffic: nobody waits on it, so half price by default.
# Override with AINATIVE_OPENAI_SERVICE_TIER=standard|fast.
TIER=service_tier.wire_value()
# Conversation-level judged rows ONLY. Rows measured some other way are absent and
# listed in UNSUPPORTED below, with the instrument to use instead.
ROWS={
"R1":("Emotional contact","Does Aurie meet the user emotionally when the moment calls for it?",
 """**1** Does not engage the feeling at all: processes content, asks about facts, or moves to a next step without acknowledging what the user expressed.
**3** The feeling is named, but the turn's centre of gravity is elsewhere - the acknowledgement is a clause or opening line and the bulk is explanation, reframing, question or plan.
**5** Meets the most vulnerable thing the user just said FIRST, at the size the user gave it, and that meeting is the SUBSTANCE of the turn rather than its preface. Points at the specific thing, not the general category of feeling.

Deduct for: an acknowledgement used as a runway; generic naming where the user offered something specific; meeting a smaller thing than the user offered."""),
"R2":("Empathic attunement","Accurate understanding without amplifying, flattening, or distorting.",
 """**1** Generic empathy, or reassurance that does not fit · **3** Broadly right, misjudges intensity · **5** Tracks the specific shape and intensity, including nuance the user has clearly implied, without adding unsupported depth"""),
"R3":("Grounded interpretation","Reflections supported by what the user actually said, offered tentatively enough to be refused.",
 """**1** Invents motive; states an underlying "real" meaning as settled · **3** Supported, but delivered as conclusion rather than offer · **5** Traceable to the user's own words and held loosely.
CARVE-OUT: a grounded read of someone else's behaviour is CORRECT and must not be scored down, including saying what someone did sounded harsh or out of line. Fails only on mind-reading asserted as fact."""),
"R6":("User agency","Room to disagree, refine, choose, and arrive at it themselves.",
 """**1** User's job is to confirm Aurie's read · **3** Room exists but is not invited · **5** Disagreement is made easy; ambiguity left open where it should be"""),
"R9":("Cumulative relational distance","Across the conversation, does Aurie drift toward observer or analyst? REVERSE-SCORED: 1 is BEST.",
 """**1** Alongside the user throughout (BEST) · **3** Recedes into commentary in the back half · **5** Analyses the user from outside the conversation (WORST)"""),
}

# These rows are NOT conversation-level scales any more. Scoring them here would
# measure the ceilinged construct the rubric retired.
UNSUPPORTED={
 "R4":"R4 requires a named integrated_sentence and a code-enforced cap. It is a floor, not a ranking scale.",
 "R5":"R5 was retired as a scale (two designs ceilinged). It is now a per-turn mistimed-directness defect count - use scripts/label_two_stage.py.",
 "R7":"R7 requires its user-side denominator (only conversations where help was sought) and matched scripts. Scoring it without the denominator rewards unsolicited advice and manufactures a regression.",
 "V1b":"Retired: ceilinged in six consecutive measurements. Holistic voice preference goes to V4 forced-choice.",
}
import sys as _sys
_asked=[a for a in _sys.argv[2:] if a in UNSUPPORTED]
if _asked:
    raise SystemExit("\n".join(f"{r}: {UNSUPPORTED[r]}" for r in _asked))
if len(sys.argv)<2:
    raise SystemExit("usage: judge_row.py <transcripts.json> [ROW ...]")
F=json.load(open(sys.argv[1]))
LABELS={"A":"prod","B":"prod-Tier2","C":"old/chat-latest","D":"prod/chat-latest","E":"old/5.4","X":"dev v1","Y":"dev v2","Z":"dev v2 cln","W":"dev v3","V":"dev v3 B","U":"dev v4"}
# one source of truth for the arm list, as in counters.py and holds.py
ARMS=[(a,LABELS.get(a,a)) for a in sorted({k.split("|")[1] for k in F})]
# The baseline is resolved before any API spend, so an unresolvable BASELINE_ARM exits
# without making a single judge call.
BASELINE=os.environ.get("BASELINE_ARM") or ARMS[0][0]
if BASELINE not in {a for a,_ in ARMS}:
    raise SystemExit(f"BASELINE_ARM={BASELINE} is not in this transcript set: {[a for a,_ in ARMS]}")
others=[a for a,_ in ARMS if a!=BASELINE]
items=[(k,v) for k,v in F.items() if k.split("|")[1] in {a for a,_ in ARMS}]
REPS=3
def significance(delta, sd):
    """Return (cell, flagged) for one delta against its own pair's judge SD.

    A zero SD across a handful of reps is an ABSENCE of a variance estimate, not
    infinite significance: three agreeing reps are weak evidence that variance is
    really zero. So sd == 0 is never flagged, and the cell says so rather than
    printing a ratio the flag could contradict.
    """
    if sd <= 0:
        return f"{'sd=0':>8}", False
    ratio = abs(delta) / sd
    return f"{ratio:>7.1f}x", ratio > 2

def call(job):
    rep,rid,k,seq=job
    name,desc,anchors=ROWS[rid]
    sysmsg=f"""You are scoring one coaching conversation on ONE dimension only. The coach is Aurie.

{rid} — {name}
{desc}

{anchors}

Score the whole conversation 1-5. Return ONLY JSON: {{"score": <1-5>, "evidence": "<short verbatim quote>"}}"""
    b=json.dumps({"model":"gpt-5.4","reasoning_effort":"medium","service_tier":TIER,
      "messages":[{"role":"system","content":sysmsg},
        {"role":"user","content":"CONVERSATION:\n\n"+"\n\n".join(f"{'USER' if r=='User' else 'COACH'}: {t}" for r,t in seq)}]}).encode()
    req=urllib.request.Request("https://api.openai.com/v1/chat/completions",data=b,
      headers={"Authorization":f"Bearer {KEY}","Content-Type":"application/json"})
    for a in range(4):
        try:
            r=json.load(urllib.request.urlopen(req,timeout=240))
            tx=r["choices"][0]["message"]["content"]; tx=tx[tx.find("{"):tx.rfind("}")+1]
            d=json.loads(tx); d.update(_id=k,row=rid,rep=rep); return d
        except Exception as e:
            if a==3:
                service_tier.warn_if_flex_refused(TIER,e)
                return {"_id":k,"row":rid,"rep":rep,"error":str(e)[:70]}
_want=[a for a in sys.argv[2:] if a in ROWS]
_unknown=[a for a in sys.argv[2:] if a not in ROWS and a not in UNSUPPORTED]
if _unknown: raise SystemExit(f"unknown row(s): {_unknown}. Available: {sorted(ROWS)}")
SELECTED=_want or list(ROWS)
print(f"scoring rows: {', '.join(SELECTED)}")
jobs=[(r,rid,k,v) for r in range(REPS) for rid in SELECTED for k,v in items]
random.seed(41); random.shuffle(jobs)
print(f"{len(jobs)} calls ({REPS} reps x {len(ROWS)} rows x {len(items)} conversations)",flush=True)
res=[]
with cf.ThreadPoolExecutor(max_workers=14) as ex:
    for d in ex.map(call,jobs): res.append(d)
json.dump(res,open("rblock_scores.json","w"),indent=1)
# The report is generated from ARMS, not from hardcoded letters. Deltas are shown
# against a baseline arm - BASELINE_ARM, else the first arm present - so a transcript
# set with any arms at all reports, and every arm that was paid for is shown.

hdr=f"{'row':4}{'':26}" + "".join(f"{l[:8]:>9}" for _,l in ARMS)
if others: hdr += "".join(f"{'d '+a:>8}{'(xSD)':>8}" for a in others)
print(f"\nbaseline arm: {BASELINE} ({dict(ARMS)[BASELINE]})")
print(hdr)
for rid in SELECTED:
    line=f"{rid:4}{ROWS[rid][0][:25]:26}"
    vals={}
    for arm,lbl in ARMS:
        means=[]
        for r in range(REPS):
            v=[x["score"] for x in res if x.get("rep")==r and x.get("row")==rid and x.get("_id","").endswith("|"+arm) and "score" in x]
            if v: means.append(st.mean(v))
        vals[arm]=(st.mean(means) if means else float("nan"),
                   st.stdev(means) if len(means)>1 else 0.0)
    line+="".join(f"{vals[a][0]:>9.2f}" for a,_ in ARMS)
    # Each delta is shown with the SD of ITS OWN pair, so the flag can never disagree
    # with the figure printed beside it.
    hits=[]; degenerate=[]
    for a in others:
        d=vals[a][0]-vals[BASELINE][0]
        sd=(vals[BASELINE][1]+vals[a][1])/2
        cell,flagged=significance(d,sd)
        line+=f"{d:>+8.2f}{cell}"
        if flagged: hits.append(a)
        if sd<=0 and abs(d)>0: degenerate.append(a)
    note=""
    if hits: note+=f"  <-- {', '.join(hits)} exceed 2x their own judge SD"
    if degenerate: note+=f"  [no judge-variance estimate for {', '.join(degenerate)}: every rep agreed]"
    print(line+note)
print("\n(R9 is reverse-scored: a negative delta is an improvement)")
print("Run SD is JUDGE variance across reps. It is NOT drive variance, which is roughly")
print("3x larger - a single drive per arm cannot support a comparative claim.")
