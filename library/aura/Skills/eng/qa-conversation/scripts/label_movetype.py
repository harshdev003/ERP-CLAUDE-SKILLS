import json,os,re,sys,random,urllib.request,concurrent.futures as cf
from collections import defaultdict
# Read lazily, not at import. Reading it here meant the script could not validate
# its own arguments, print usage, or refuse a bad input set without credentials -
# it died at line 3 with a KeyError instead. Input validation is free; API keys are
# only needed once a call is actually made.
def _key():
    try:
        return os.environ["OPENAI_EVALUATIONS_API_KEY"]
    except KeyError:
        raise SystemExit("OPENAI_EVALUATIONS_API_KEY is not set") from None
sys.path.insert(0,os.path.dirname(os.path.abspath(__file__)))
import service_tier
# Eval traffic: nobody waits on it, so half price by default.
# Override with AINATIVE_OPENAI_SERVICE_TIER=standard|fast.
TIER=service_tier.wire_value()
SYS="""You are classifying the KIND of conversational move ONE coach turn makes. The coach is Aurie.

Choose exactly one label:
- "probe_feeling"    asks about the feeling, what is hardest, what is underneath
- "probe_topic"      asks about a different part of the situation, a fact, an event, another person
- "reflection"       offers an observation or read, with no question attached
- "name_stuck"       explicitly names that the current approach is not landing, or that the user seems stuck
- "concrete"         asks for or offers a small specific concrete detail, or gets materially more specific
- "practical"        offers practical help, a next step, or asks about a decision to be made
- "presence"         stays with the user without asking anything: reassurance, permission to say nothing

If a turn does more than one, pick the one that carries the turn.

Return ONLY JSON: {"move": "<label>", "quote": "<short verbatim span>"}"""
def render(seq,idx):
    out=[]
    for i,m in enumerate(seq):
        who="USER" if m["role"]=="user" else "COACH"
        out.append(f">>> TARGET TURN <<<\n{who}: {m['content']}\n>>> END TARGET <<<" if i==idx else f"{who}: {m['content']}")
    return "\n\n".join(out)
def call(job):
    rep,src,pid,idx,seq=job
    b=json.dumps({"model":"gpt-5.4","reasoning_effort":"medium","service_tier":TIER,
      "messages":[{"role":"system","content":SYS},{"role":"user","content":render(seq,idx)}]}).encode()
    req=urllib.request.Request("https://api.openai.com/v1/chat/completions",data=b,
      headers={"Authorization":f"Bearer {_key()}","Content-Type":"application/json"})
    for a in range(4):
        try:
            r=json.load(urllib.request.urlopen(req,timeout=240))
            tx=r["choices"][0]["message"]["content"]; tx=tx[tx.find("{"):tx.rfind("}")+1]
            d=json.loads(tx); d.update(src=src,pid=pid,turn=idx,rep=rep); return d
        except Exception as e:
            if a==3:
                service_tier.warn_if_flex_refused(TIER,e)
                return {"src":src,"pid":pid,"turn":idx,"rep":rep,"error":str(e)[:80]}
if len(sys.argv)<3:
    raise SystemExit("usage: label_movetype.py <controls.json> <label>=<probe_run.json> [...]\n"
                     "  e.g. label_movetype.py ../references/controls/probe_controls.json prod=hard_prod.json v4=hard_v4.json")
# Match the probe FAMILIES, not four enumerated ids. The old tuple listed
# H1/H2/H3/P1 and therefore silently scored 4 of the 12 probes in the official
# stall sets: stall_amplification_v1 carries P1-P6 and the hard set carries H1-H6,
# so A12 was being measured on a third of its probe set with no sign anything was
# missing. CTRL-amplify-P4/P5 were dropped the same way.
#
# The digit is required so HS1-* (help_seeking_v1) does NOT match - that is a
# different probe set with a different denominator, and sweeping it in here would
# trade an under-count for a wrong one.
STALL_RE=re.compile(r"^(?:[HP]\d+-|CTRL-(?:stall|topichop|kindchange|amplify))")
def is_stall(pid): return bool(STALL_RE.match(pid))
# The controls loader uses the SAME predicate as the job filter. It previously
# carried its own ("H1" in k or "stall" in k), which dropped CTRL-amplify-P4/P5
# before they ever reached the filter - so widening the filter alone changed
# nothing on the documented invocation. Two filters that must agree will not.
SETS={"ctrl":{k:v for k,v in json.load(open(sys.argv[1])).items() if is_stall(k)}}
for spec in sys.argv[2:]:
    lbl,_,path=spec.partition("=")
    if not path: raise SystemExit(f"expected <label>=<file>, got {spec!r}")
    SETS[lbl]=json.load(open(path))
jobs=[]
matched={src:0 for src in SETS}
seen={src:len(D) for src,D in SETS.items()}
for src,D in SETS.items():
    for pid,seq in D.items():
        if not is_stall(pid): continue
        matched[src]+=1
        for i,m in enumerate(seq):
            if m["role"]=="assistant":
                for rep in range(3): jobs.append((rep,src,pid,i,seq))

# The id filter accepts only the probe shapes above. Production conversations are
# UUID-keyed, so on logged traffic every one is skipped - and because the controls
# file always matches, the run still printed a populated table with the arm
# silently absent. That is worse than an empty table: it looks like a result.
# Refuse per SOURCE, so a named arm contributing nothing is an error even when the
# controls carry the run.
empty=[src for src,n in matched.items() if n==0 and src!="ctrl"]
if empty or not jobs:
    bad = empty or list(SETS)
    sample = [pid for src in bad for pid in list(SETS[src])[:3]][:3]
    raise SystemExit(
        "label_movetype.py matched no stall probes in: " + ", ".join(bad) + "\n"
        f"  ids seen there: {sample}\n"
        f"  It accepts stall-probe ids: H<n>-*, P<n>-*, CTRL-stall/topichop/kindchange/amplify-*.\n"
        "  This script scores stall probes and has no denominator on logged\n"
        "  production traffic. Use the probe sets under references/probes/.")
# Always print per-source coverage, not only when something was skipped. A run
# that scores 4 of 12 probes looks identical to one that scores 12 of 12 unless
# the denominator is on screen - which is how A12 was measured on a third of its
# probe set without anyone noticing.
for src,n in matched.items():
    skipped = seen[src] - n
    print(f"  {src}: {n} of {seen[src]} conversations are stall probes"
          + (f"; {skipped} skipped" if skipped else ""))
random.seed(15); random.shuffle(jobs)
print(f"{len(jobs)} move-type calls")
res=[]
with cf.ThreadPoolExecutor(max_workers=14) as ex:
    for d in ex.map(call,jobs): res.append(d)
json.dump(res,open("movetypes.json","w"),indent=1)
maj=defaultdict(list)
for d in res:
    if "move" in d: maj[(d["src"],d["pid"],d["turn"])].append(d["move"])
from collections import Counter
seqs=defaultdict(list)
for (src,pid,turn),v in sorted(maj.items(), key=lambda x:(x[0][0],x[0][1],x[0][2])):
    seqs[(src,pid)].append((turn,Counter(v).most_common(1)[0][0]))
print(f"\n{'arm':6}{'probe':26}{'move sequence':58}{'kind repeats':>13}{'distinct':>9}")
for (src,pid),lst in sorted(seqs.items()):
    moves=[m for _,m in lst]
    rep=sum(1 for i in range(1,len(moves)) if moves[i]==moves[i-1])
    print(f"{src:6}{pid:26}{' > '.join(m[:9] for m in moves):58}{rep:>13}{len(set(moves)):>9}")
