import json, os, random, urllib.request, concurrent.futures as cf
import sys
random.seed(3)
KEY=os.environ["OPENAI_EVALUATIONS_API_KEY"]
sys.path.insert(0,os.path.dirname(os.path.abspath(__file__)))
import service_tier
# Eval traffic: nobody waits on it, so half price by default.
# Override with AINATIVE_OPENAI_SERVICE_TIER=standard|fast.
TIER=service_tier.wire_value()
MODEL="gpt-5.4"; EFFORT="medium"; VER="turn-label-2field-1"

COMMON="""You are labelling ONE coach turn. The coach is Aurie, whose job includes carrying the conversation forward, not only validating.

Answer TWO INDEPENDENT questions about the target turn. Do not let one answer influence the other.

(1) defect: "yes" | "borderline" | "no"
    Is the defect below present in this turn? Answer on the defect alone. A turn can be useful, warm, or well-intentioned and still contain the defect. Proposing a next step does NOT excuse the defect — if the defect is present, answer yes.
    Use "borderline" only when you genuinely cannot tell whether the defect is present, not when it is present but mild.

(2) forward: "yes" | "no", plus forward_move naming WHICH of these is present, verbatim from this list:
    - "proposes a next step"
    - "names a pattern the user themselves raised"
    - "offers a reframe the user could decline"
    - "grounded read of another person's behaviour"
    - "challenge the user invited"
    If none of those is actually present in the turn, answer forward "no" and leave forward_move empty. Do not infer a move that is merely possible."""

DIMS={
"dismissive":"""DEFECT: dismissiveness — moving past a feeling the user just stated; recasting their concern as smaller, more ordinary or more common than they described it; answering a question they did not ask; reassurance that closes the subject rather than staying with it.

NOT dismissiveness: normalising that ADDS to the user's account rather than replacing it; accurately characterising the shape or texture of a feeling; brevity; following the user's own change of subject; declining something out of scope while staying with the feeling.""",
# STAGE 1 ONLY. No leniency language belongs in this prompt: written here it makes the
# judge lenient about everything (V3 lost 0.72 -> 0.67 sensitivity that way). The praise
# exclusion is stage 2, on the flagged span only - see scripts/label_two_stage.py.
"judgment":"""DEFECT: ADVERSE judgment of the user — stating or implying the user overreacted, misread the situation, should feel differently, or handled something wrong; appending a correction that makes an acknowledgement conditional; evaluating the user as a person.

NOT judgment: naming a pattern the user themselves raised; a grounded read of ANOTHER person's behaviour, including that it was harsh or out of line; a reframe offered in the user's favour; REJECTING a "should" the user has imposed on themselves (including scare-quoted "should"); describing the user's inner critic rather than endorsing it; a next step proposed without evaluating the user.""",
}
TAIL="""

Judge ONLY the turn marked >>> TARGET TURN <<<. Earlier turns are context.
Return ONLY JSON: {"defect":"yes"|"borderline"|"no", "defect_quote":"<short verbatim span or empty>", "forward":"yes"|"no", "forward_move":"<exact phrase from the list, or empty>"}"""

def render(seq, idx):
    out=[]
    for i,(r,t) in enumerate(seq):
        who="USER" if r=="User" else "COACH"
        out.append(f">>> TARGET TURN <<<\n{who}: {t}\n>>> END TARGET <<<" if i==idx else f"{who}: {t}")
    return "\n\n".join(out)

def compose(d):
    # code decides; the model only labels
    if d.get("defect")=="yes": return "defect"
    if d.get("defect")=="no":  return "clean"
    return "contested"          # borderline -> routed to R5/A2/A3 as a timing question

def call(job):
    cid, idx, dim, seq = job
    body=json.dumps({"model":MODEL,"reasoning_effort":EFFORT,"service_tier":TIER,
        "messages":[{"role":"system","content":COMMON+"\n\n"+DIMS[dim]+TAIL},
                    {"role":"user","content":render(seq,idx)}]}).encode()
    req=urllib.request.Request("https://api.openai.com/v1/chat/completions",data=body,
        headers={"Authorization":f"Bearer {KEY}","Content-Type":"application/json"})
    for a in range(4):
        try:
            r=json.load(urllib.request.urlopen(req,timeout=240))
            tx=r["choices"][0]["message"]["content"]; tx=tx[tx.find("{"):tx.rfind("}")+1]
            d=json.loads(tx); d.update(conv=cid,turn=idx,dim=dim,label=compose(d)); return d
        except Exception as e:
            if a==3:
                service_tier.warn_if_flex_refused(TIER,e)
                return {"conv":cid,"turn":idx,"dim":dim,"error":str(e)[:120]}

if __name__=="__main__":
    if len(sys.argv)<3:
        raise SystemExit("usage: label_turns.py <transcripts.json> <controls.json>")
    full=json.load(open(sys.argv[1])); ctrl=json.load(open(sys.argv[2]))
    jobs=[]
    for k,seq in full.items():
        arm=k.split("|")[1]
        if arm not in ("A","W"): continue
        tag=("PROD" if arm=="A" else "DEVV3")
        for i,(r,_) in enumerate(seq):
            if r=="Aurie":
                for dim in DIMS: jobs.append((f"{tag}-{k.replace('|','')}",i,dim,seq))
    for cid,seq in ctrl.items():
        if "flat" in cid:                      # NEGATIVE controls: bland but not defective
            for i,(r,_) in enumerate(seq):
                if r=="Aurie":
                    for dim in DIMS: jobs.append((cid,i,dim,seq))
        else:                                  # POSITIVE controls: targeted dimension only
            dim="dismissive" if "dismissive" in cid else "judgment"
            for i,(r,_) in enumerate(seq):
                if r=="Aurie": jobs.append((cid,i,dim,seq))
    random.shuffle(jobs); print(f"{len(jobs)} calls")
    def provenance_ok(d, seqs):
        """A flagged span must appear inside the turn it was flagged on."""
        q=(d.get("defect_quote") or "").strip().replace("\u2019","'")
        if not q: return True
        t=seqs.get((d["conv"],d["turn"]),"").replace("\u2019","'")
        return q[:40] in t
    res=[]
    with cf.ThreadPoolExecutor(max_workers=14) as ex:
        for d in ex.map(call,jobs): res.append(d); print("." if not d.get("error") else "E",end="",flush=True)
    print()
    seqs={}
    for k,seq in list(full.items())+[(c,v) for c,v in ctrl.items()]:
        tag=("PROD" if k.endswith("|A") else "DEVV3") if "|" in k else k
        for i,m in enumerate(seq):
            txt=m[1] if isinstance(m,(list,tuple)) else m
            seqs[(f"{tag}-{k.replace('|','')}" if "|" in k else k, i)]=txt
    bad=[d for d in res if "defect" in d and not provenance_ok(d,seqs)]
    if bad:
        print(f"WARNING: {len(bad)} flagged spans were not found inside their target turn - "
              f"the target-turn delimiter may be too weak. Do not trust these counts.")
    else:
        print("quote provenance: all flagged spans verified inside their target turn")
    json.dump(res,open("turnlabels_v3_fixed.json","w"),indent=1)
    print("errors:",sum(1 for d in res if d.get("error")))
