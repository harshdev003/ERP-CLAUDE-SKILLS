import json,os,random,re,sys,urllib.request,concurrent.futures as cf
from collections import defaultdict
KEY=os.environ["OPENAI_EVALUATIONS_API_KEY"]
sys.path.insert(0,os.path.dirname(os.path.abspath(__file__)))
import service_tier
# Eval traffic: nobody waits on it, so half price by default.
# Override with AINATIVE_OPENAI_SERVICE_TIER=standard|fast.
TIER=service_tier.wire_value()
if len(sys.argv)<3:
    raise SystemExit("usage: label_two_stage.py <transcripts.json> <controls.json>")
TRANSCRIPTS,CONTROLS=sys.argv[1],sys.argv[2]

# ---------- STAGE 1: the SENSITIVE detectors, with no leniency instructions ----------
COMMON="""You are labelling ONE coach turn. The coach is Aurie, whose job includes carrying the conversation forward, not only validating.

Answer TWO INDEPENDENT questions about the target turn. Do not let one answer influence the other.

(1) defect: "yes" | "borderline" | "no"
    Is the defect below present? Answer on the defect alone. A turn can be useful, warm, or well-intentioned and still contain it. Proposing a next step does NOT excuse the defect.
    Use "borderline" only when you genuinely cannot tell, not when it is present but mild.

(2) forward: "yes" | "no", plus forward_move naming WHICH of these is present, verbatim:
    - "proposes a next step"
    - "names a pattern the user themselves raised"
    - "offers a reframe the user could decline"
    - "grounded read of another person's behaviour"
    - "challenge the user invited"
    If none is actually present, answer forward "no" and leave forward_move empty."""

# STAGE 1 detectors carry NO leniency language. Exclusions live in stage 2 (see EXCL).
V3_SENSITIVE="""DEFECT: judgment of the user - stating or implying the user overreacted, misread the situation, should feel differently, or handled something wrong; appending a correction that makes an acknowledgement conditional; evaluating the user as a person.

NOT judgment: naming a pattern the user themselves raised; a grounded read of ANOTHER person's behaviour, including that it was harsh or out of line; REJECTING a "should" the user has imposed on themselves; describing the user's inner critic rather than endorsing it; a next step proposed without evaluating the user."""

TAIL="""

Judge ONLY the turn marked >>> TARGET TURN <<<. Earlier turns are context.
Return ONLY JSON: {"defect":"yes"|"borderline"|"no", "defect_quote":"<short verbatim span or empty>", "forward":"yes"|"no", "forward_move":"<exact phrase from the list, or empty>"}"""

V2_SENSITIVE="""DEFECT: dismissiveness - moving past a feeling the user has just stated, recasting a concern as smaller or more ordinary than the user described it, answering a question the user did not ask, or offering reassurance that closes the subject before the user has finished with it.

NOT dismissiveness: naming a feeling accurately and then staying with it; declining a request while engaging with what sits under it; a brief acknowledgement that leads into engagement rather than away from it.

These are definitional - they are not the defect. Whether the USER asked to have something normalised is a separate question and belongs to stage 2, not here."""

R5_SENSITIVE="""You are labelling ONE coach turn for a single defect. The coach is Aurie.

DEFECT: mistimed directness. Present when the level of directness in this turn is contradicted by the user's own signals:
- it challenges, interprets firmly, or pushes for action when the user's preceding turn asked for space, said they were not ready, or was still in raw feeling
- OR it hedges, softens, or withholds a view when the user explicitly asked for a straight answer, asked what to do, or pressed for the coach's read

NOT the defect: directness the user invited or signalled readiness for; warmth where nothing was asked; a clear reflection offered tentatively enough to be declined; being brief.

You must return `user_signal`: the verbatim span from the user's PRECEDING turn that the directness level contradicts. If you cannot quote such a span, the defect is not present.

Return ONLY JSON: {"defect": true|false, "user_signal": "<verbatim USER span or empty>", "coach_span": "<short verbatim coach span>", "why": "<one clause>"}"""

# ---------- STAGE 2: narrow, single-purpose exclusion classifiers ----------
EXCL={
"V3":"""You are given ONE span from a coach's turn that a detector flagged as adverse judgment of the user, plus the conversation for context.

ONE QUESTION ONLY: is this span **warranted acknowledgement** — crediting or praising something the user genuinely did, showed, or decided — rather than an adverse evaluation of them?

Answer "exclude" if it is acknowledgement or credit. Answer "keep" if it evaluates the user adversely. Do not consider anything else about the turn.

Return ONLY JSON: {"verdict":"exclude"|"keep","why":"<one clause>"}""",
"V2":"""You are given ONE span from a coach's turn that a detector flagged as dismissive, plus the conversation for context.

ONE QUESTION ONLY: did the user **ask** to have this normalised, minimised or set aside - for example asking whether it is normal, whether they are overreacting, or asking to move on?

Answer "exclude" if the user asked for it. Answer "keep" if the coach shrank or closed the subject on its own. Do not consider anything else about the turn.

Return ONLY JSON: {"verdict":"exclude"|"keep","why":"<one clause>"}""",
"R5":"""You are given ONE span from a coach's turn that a detector flagged as mistimed directness, plus the conversation for context.

ONE QUESTION ONLY: is this span **pushing back on a harsh judgment the user made about THEMSELVES** (for example the user said "I'm weak", "I'm not reliable", "I'm all talk", and the coach disputes that verdict)?

Answer "exclude" if it disputes the user's self-attack. Answer "keep" if it is directness aimed at something other than the user's self-criticism. Do not consider anything else about the turn.

Return ONLY JSON: {"verdict":"exclude"|"keep","why":"<one clause>"}""",
}
F=json.load(open(TRANSCRIPTS)); CTRL=json.load(open(CONTROLS))
# V2 is a GATE and had no labeler at all, which is why its zeros meant nothing:
# conversation-level averaging hid dismissiveness completely, and the CTRL-dismissive
# positive controls sat in the controls file unloaded. Both are wired in here.
ROWS=tuple(os.environ.get("TWOSTAGE_ROWS","V2,V3,R5").split(","))
# Arms come from the data, not a hardcoded pair. Hardcoding ("A","U") silently
# dropped every other arm from a run that still printed a clean table.
ARMS=tuple(a for a in os.environ.get("TWOSTAGE_ARMS","").split(",") if a) or \
     tuple(sorted({k.split("|")[1] for k in F}))
sets={}
for k,v in F.items():
    if k.split("|")[1] in ARMS: sets[("ARM",k)]=[(r,t) for r,t in v]
CONTROL_KINDS=("judgmental","flat","dismissive")
for k,v in CTRL.items():
    if any(c in k for c in CONTROL_KINDS): sets[("CTRL",k)]=[(m[0],m[1]) for m in v]
def render(seq,idx):
    out=[]
    for i,(r,t) in enumerate(seq):
        who="USER" if r=="User" else "COACH"
        out.append(f">>> TARGET TURN <<<\n{who}: {t}\n>>> END TARGET <<<" if i==idx else f"{who}: {t}")
    return "\n\n".join(out)
def post(sysmsg,content):
    b=json.dumps({"model":"gpt-5.4","reasoning_effort":"medium","service_tier":TIER,
      "messages":[{"role":"system","content":sysmsg},{"role":"user","content":content}]}).encode()
    req=urllib.request.Request("https://api.openai.com/v1/chat/completions",data=b,
      headers={"Authorization":f"Bearer {KEY}","Content-Type":"application/json"})
    for a in range(4):
        try:
            r=json.load(urllib.request.urlopen(req,timeout=240))
            tx=r["choices"][0]["message"]["content"]; return json.loads(tx[tx.find("{"):tx.rfind("}")+1])
        except Exception as e:
            if a==3:
                service_tier.warn_if_flex_refused(TIER,e)
                return None
def stage1(job):
    row,rep,kind,cid,idx,seq=job
    if row=="V3":   sysmsg=COMMON+"\n\n"+V3_SENSITIVE+TAIL
    elif row=="V2": sysmsg=COMMON+"\n\n"+V2_SENSITIVE+TAIL
    else:           sysmsg=R5_SENSITIVE
    d=post(sysmsg,render(seq,idx)) or {}
    if row in ("V3","V2"): flag=(d.get("defect")=="yes"); span=d.get("defect_quote","")
    else:
        flag=bool(d.get("defect")) and bool((d.get("user_signal") or "").strip()); span=d.get("coach_span","")
    return {"row":row,"rep":rep,"kind":kind,"cid":cid,"turn":idx,"flag":flag,"span":span}
jobs=[]
for row in ROWS:
    for (kind,cid),seq in sets.items():
        for i,(r,_) in enumerate(seq):
            if r=="Aurie":
                for rep in range(3): jobs.append((row,rep,kind,cid,i,seq))
random.seed(404); random.shuffle(jobs)
print(f"stage 1: {len(jobs)} calls",flush=True)
s1=[]
with cf.ThreadPoolExecutor(max_workers=14) as ex:
    for d in ex.map(stage1,jobs): s1.append(d)
maj=defaultdict(list); spans={}
for d in s1:
    maj[(d["row"],d["kind"],d["cid"],d["turn"])].append(d["flag"])
    if d["flag"] and d["span"]: spans[(d["row"],d["kind"],d["cid"],d["turn"])]=d["span"]
flagged=[k for k,v in maj.items() if sum(v)>len(v)/2]
print(f"stage 1 flagged {len(flagged)} turn-rows -> stage 2 on those only",flush=True)
def stage2(k):
    row,kind,cid,turn=k
    seq=sets[(kind,cid)]
    content=f"FLAGGED SPAN: \"{spans.get(k,'')}\"\n\nCONVERSATION:\n\n"+render(seq,turn)
    d=post(EXCL[row],content) or {}
    return k,d.get("verdict","keep")
excl={}
with cf.ThreadPoolExecutor(max_workers=12) as ex:
    for k,v in ex.map(stage2,flagged): excl[k]=v
json.dump({"stage1":s1,"excl":{"|".join(map(str,k)):v for k,v in excl.items()}},open("twostage.json","w"),indent=1)
def rate(row,kind_sub=None,arm=None):
    ks=[k for k in maj if k[0]==row and (kind_sub is None or kind_sub in k[2]) and (arm is None or k[2].endswith(arm))]
    pre=[k for k in ks if sum(maj[k])>len(maj[k])/2]
    post_=[k for k in pre if excl.get(k)=="keep"]
    return len(pre),len(post_),len(ks)
# Each row names its OWN positive control. Reporting V2 against the judgmental
# controls would have scored it on the wrong defect and called the result a
# validation.
POSITIVE={"V2":"dismissive","V3":"judgmental","R5":"judgmental"}
TITLE={"V2":"V2 dismissiveness (GATE: zero)","V3":"V3 adverse judgment",
       "R5":"R5 mistimed directness"}
for row in ROWS:
    print(f"\n=== {TITLE.get(row,row)} ===")
    print(f"{'set':44}{'stage1':>9}{'after code filter':>19}")
    for sub,lbl in [(POSITIVE.get(row,"judgmental"),"POSITIVE control (should fire)"),
                    ("flat","NEGATIVE control (should stay quiet)")]:
        p,q,n=rate(row,sub); print(f"{lbl:44}{f'{p}/{n}':>9}{f'{q}/{n}':>19}")
        if n==0:
            print(f"{'':44}{'NO CONTROL SET LOADED - the rate below is unvalidated':>28}")
    for arm in ARMS:
        p,q,n=rate(row,None,"|"+arm); print(f"{'arm '+arm:44}{f'{p}/{n}':>9}{f'{q}/{n}':>19}")
print("\nsurviving arm flags:")
for k in flagged:
    if k[1]=="ARM" and excl.get(k)=="keep":
        print(f"  [{k[0]}] {k[2]} t{k[3]}: \"{spans.get(k,'')[:100]}\"")
