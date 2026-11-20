import json,re,math,random,statistics as st
import sys
from collections import Counter
random.seed(11)
if len(sys.argv)<2:
    raise SystemExit("usage: counters.py <transcripts.json> [ARM ...]\n"
                     "  COUNTERS_PER_CONV=<out.json> also writes per-conversation values")
F=json.load(open(sys.argv[1]))
LABELS={"A":"prod","B":"prod-Tier2","C":"old/chat-latest","D":"prod/chat-latest","E":"old/5.4","X":"dev v1","Y":"dev v2","Z":"dev v2 cln","W":"dev v3","V":"dev v3 B","U":"dev v4"}
# Arms derive from the data (or argv after the input file); one source of truth, so a
# report can never list an arm whose turns were never scored.
ARMS=[(a,LABELS.get(a,a)) for a in (sys.argv[2:] or sorted({k.split("|")[1] for k in F}))]
def turns(a): return [t for k,v in F.items() if k.endswith("|"+a) for r,t in v if r=="Aurie"]
def convs(a): return {k:[t for r,t in v if r=="Aurie"] for k,v in F.items() if k.endswith("|"+a)}
def w(s): return re.findall(r"[a-z']+", s.replace("’","'").lower())
def ng(ws,n): return [" ".join(ws[i:i+n]) for i in range(len(ws)-n+1)]
STOP=set("the a an and or of to in is it that this for you your i my me we with on at as be".split())
def fs(t): return re.split(r'(?<=[.!?])\s', t.split("\n")[0].strip())[0]
def op(t,n=2): return " ".join(w(fs(t))[:n])

INTERJ=r"^\s*(yeah|yes|oh|ah|oof|ouch|yikes|mm+|okay|ok|god|wow|ooh|right|hm+|well)\b"
def interj_stats(a):
    ts=turns(a); hits=[]
    for t in ts:
        m=re.match(INTERJ,t.lower())
        if m: hits.append(m.group(1))
    if not hits: return 0,0,0,{}
    c=Counter(hits)
    return len(hits)/len(ts)*100, c.most_common(1)[0][1]/len(hits)*100, len(c), dict(c.most_common(5))

pool=Counter()
for a,_ in ARMS:
    for t in turns(a): pool.update(set(ng(w(t),4)))
# The shared-vocabulary estimator is DROPPED (pool-dependent: a fixed arm's value
# moved 26.1 -> 34.2 purely because the other arms in the pool changed). The
# vocabulary is still built, for naming offending phrases - never for a rate.
shared=[g for g,c in pool.most_common() if c>=6 and not all(x in STOP for x in g.split())][:30]
def eqn(a,n=None,reps=200):
    n = n or EQN_N
    ts=turns(a); out=[]
    for _ in range(reps):
        s=random.sample(ts,min(n,len(ts))); p=Counter()
        for t in s: p.update(set(ng(w(t),4)))
        cand=[g for g,c in p.most_common() if c>=2 and not all(x in STOP for x in g.split())][:10]
        out.append(sum(1 for t in s if any(g in " ".join(w(t)) for g in cand))/len(s)*100)
    return st.mean(out), sorted(out)[5], sorted(out)[194]
def modal_op(a):
    o=[op(t) for t in turns(a) if op(t)]; return Counter(o).most_common(1)[0][1]/len(o)*100
def op_ent(a):
    o=[op(t) for t in turns(a) if op(t)]; c=Counter(o); n=len(o)
    return -sum(v/n*math.log2(v/n) for v in c.values())/math.log2(n)*100
def sig(t):
    paras=[p for p in t.split("\n") if p.strip()]
    sents=[s for s in re.split(r'(?<=[.!?])\s',t.replace("\n"," ")) if s.strip()]
    return (min(len(paras),3),min(len(sents),5),t.strip().endswith("?"),len(w(fs(t)))<=12)
def modal_sig(a):
    s=[sig(t) for t in turns(a)]; return Counter(s).most_common(1)[0][1]/len(s)*100
ARTIFACT_PATTERNS=[r"^\s*[#>]|\*\*|\|\s*-{3,}", r"\{\s*\"",
                   r"\bT[1-5]\b|Tier \d", r"\{\{|\}\}|(?<![\w])\}|\{(?![\{])"]
def is_multiq(t): return t.count("?")>=2
def is_eitheror(t): return bool(re.search(r"\b(or)\b[^.?!]{0,80}\?",t))
def artifacts(t): return sum(1 for pat in ARTIFACT_PATTERNS if re.search(pat,t))
def multiq(a): return sum(1 for t in turns(a) if is_multiq(t))
def eitheror(a):
    q=[t for t in turns(a) if "?" in t]
    return sum(1 for t in q if is_eitheror(t))/len(q)*100
def lens(a):
    cs=convs(a); wt=[len(t.split()) for t in turns(a)]
    per=[sum(len(t.split()) for t in ts) for ts in cs.values()]
    th=[]
    for ts in cs.values():
        k=max(1,len(ts)//3)
        th.append((st.mean([len(t.split()) for t in ts[:k]]), st.mean([len(t.split()) for t in ts[-k:]])))
    return st.mean(wt), st.mean(per), st.mean([x for x,_ in th]), st.mean([y for _,y in th])

hdr=f"{'metric':40}" + "".join(f"{l[:11]:>12}" for _,l in ARMS)
print(hdr); print("-"*len(hdr))
def row(name,f,fmt="{:.1f}%"):
    print(f"{name:40}" + "".join(f"{fmt.format(f(a)):>12}" for a,_ in ARMS))
# "equal-n" has to actually be equal: one common n across every arm, set by the
# shortest arm. Sampling each arm at its own length is not comparable, and a longer
# arm has a larger pool to repeat within - which is why this estimator exists.
EQN_N=min(85, min(len(turns(a)) for a,_ in ARMS))
if EQN_N < 40:
    print(f"WARNING: equal-n bootstrap uses n={EQN_N} (shortest arm). Below ~40 turns "
          f"this estimate is too noisy to compare arms; treat N1 as not measured.")
_eqn={a: eqn(a) for a,_ in ARMS}   # one bootstrap per arm; mean and interval come from the SAME run
print(f"{'N1 equal-n tic load (n='+str(EQN_N)+')':40}" + "".join(f"{_eqn[a][0]:>11.1f}%" for a,_ in ARMS))
print(f"{'   90% CI':40}" + "".join(f"{'['+str(round(_eqn[a][1]))+'-'+str(round(_eqn[a][2]))+']':>12}" for a,_ in ARMS))
row("N2 modal-opener share", modal_op)
row("N2 opener entropy (higher better)", op_ent)
row("N3 modal response-shape share", modal_sig)
print(f"{'F2 multi-question turns (count)':40}" + "".join(f"{multiq(a):>12}" for a,_ in ARMS))
row("F4 either/or share of question turns", eitheror)
print()
print(f"{'interjection openers (descriptive)':40}" + "".join(f"{interj_stats(a)[0]:>11.1f}%" for a,_ in ARMS))
print(f"{'  CONCENTRATION: modal share of them':40}" + "".join(f"{interj_stats(a)[1]:>11.1f}%" for a,_ in ARMS))
print(f"{'  distinct interjections used':40}" + "".join(f"{interj_stats(a)[2]:>12}" for a,_ in ARMS))
for a,l in ARMS: print(f"    {l:8} {interj_stats(a)[3]}")
print()
for a,l in ARMS:
    x=lens(a); print(f"F9 {l:8} {x[0]:.1f} w/turn · {x[1]:.0f} w/conv · first third {x[2]:.0f} -> last third {x[3]:.0f}")
print("\nF10 format artifacts:")
for a,l in ARMS:
    bad=0
    for t in turns(a):
        bad+=artifacts(t)
    print(f"  {l:8} {bad}")


# ---- per-conversation values -------------------------------------------------
# The pooled table above cannot carry a confidence interval, so the Aug-21
# calibration re-implemented F4, F9 and F10 to get the spread - and the
# re-implementations then disagreed with the validated detectors here. Emitting
# per-conversation rows from THESE functions removes the reason to re-implement:
# bootstrap over conversations, which is the right unit anyway, since turns within
# a conversation are not independent.
import os as _os
_out = _os.environ.get("COUNTERS_PER_CONV")
if _out:
    per = []
    for a, lbl in ARMS:
        for cid, ts in convs(a).items():
            qs = [t for t in ts if "?" in t]
            words = [len(t.split()) for t in ts]
            k = max(1, len(ts) // 3)
            per.append({
                "arm": a, "arm_label": lbl, "conversation": cid, "turns": len(ts),
                "F2_multiquestion_turns": sum(1 for t in ts if is_multiq(t)),
                "F4_eitheror_questions": sum(1 for t in qs if is_eitheror(t)),
                "F4_question_turns": len(qs),
                "F9_words_per_turn": (st.mean(words) if words else 0.0),
                "F9_words_per_conversation": sum(words),
                "F9_first_third": (st.mean(words[:k]) if words else 0.0),
                "F9_last_third": (st.mean(words[-k:]) if words else 0.0),
                "F10_artifact_hits": sum(artifacts(t) for t in ts),
            })
    json.dump(per, open(_out, "w"), indent=1)
    print(f"\nwrote {len(per)} per-conversation rows to {_out} "
          f"({len(ARMS)} arms) - bootstrap over conversations for a CI")
