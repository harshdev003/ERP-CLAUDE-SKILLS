"""Pre-release TTFT / TTLT comparison across models and configs.

Production latency is measured by eng:analyze-aura-latency, per prompter, from real
traffic. Use THIS only for a config that is not deployed yet, where no production data
can exist. Read the interpretation limit at the bottom of the output before quoting a
number anywhere.

usage: measure_latency.py <arms.json> [samples] [warmups]

The service tier is REQUIRED and is not defaulted. Every other script in this skill
falls back to flex to halve the bill, but latency is exactly what this one measures, and
flex is slower by design — inheriting it would produce a confident number for a tier
production does not run. Set the tier production uses for this prompter:

    AINATIVE_OPENAI_SERVICE_TIER=standard measure_latency.py arms.json

The env var is required UNLESS every arm names its own `"service_tier"`, in which case
the config is already explicit and speaks for itself. The tier used is printed in the
header and labelled per arm in the results table, because a latency figure without its
tier is unquotable.

arms.json:
  {"arms": [{"label": "5.4-low",  "prompt": "dev.json", "reasoning_effort": "low"},
            {"label": "5.4-med",  "prompt": "dev.json", "reasoning_effort": "medium"}],
   "user_turns": ["opening user message", "second user message"]}

An arm may override the run-wide tier with `"service_tier": "standard"|"flex"|"fast"` —
comparing two tiers is a legitimate question for this harness, and the only one in the
skill for which a tier difference between arms is not a confound.

`prompt` is a Braintrust prompt JSON as fetched by the API; model, reasoning_effort and
verbosity default to that prompt's own options and may be overridden per arm.
"""
import json,os,sys,time,statistics as st
import urllib.request
from collections import defaultdict

KEY=os.environ["OPENAI_EVALUATIONS_API_KEY"]
sys.path.insert(0,os.path.dirname(os.path.abspath(__file__)))
import service_tier
if len(sys.argv)<2: raise SystemExit(__doc__)
CFG=json.load(open(sys.argv[1]))
# The arms are read BEFORE demanding a tier: a config where every arm names its
# own is already explicit, and that is the tier-vs-tier comparison this harness
# exists to allow. Demanding the env var first would reject it.
_ARMS=CFG["arms"]
if all(a.get("service_tier") for a in _ARMS):
    RUN_TIER=None                       # every arm speaks for itself
else:
    try:
        RUN_TIER=service_tier.require_explicit(None,"measure_latency.py")
    except service_tier.ServiceTierError as e:
        raise SystemExit(f"error: {e}")
N=int(sys.argv[2]) if len(sys.argv)>2 else 30   # M8 requires n>=30
WARM=int(sys.argv[3]) if len(sys.argv)>3 else 2

def build(arm):
    pd=json.load(open(arm["prompt"]))["prompt_data"]
    sysmsgs=[{"role":"system","content":m["content"]} for m in pd["prompt"]["messages"] if m["role"]=="system"]
    o=pd["options"]; p=o.get("params",{})
    body={"model":arm.get("model",o["model"]),
          "messages":sysmsgs+[{"role":"user","content":t} for t in CFG["user_turns"][:1]],
          "service_tier":service_tier.wire_value(arm["service_tier"]) if arm.get("service_tier") else RUN_TIER,
          "stream":True,"stream_options":{"include_usage":True}}
    for k,src in (("reasoning_effort",p.get("reasoning_effort")),("verbosity",p.get("verbosity"))):
        v=arm.get(k,src)
        if v is not None: body[k]=v
    return body

def one(body):
    """Return (ttft_ms, ttlt_ms, usage) for a single streamed call."""
    req=urllib.request.Request("https://api.openai.com/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Authorization":f"Bearer {KEY}","Content-Type":"application/json"})
    t0=time.perf_counter(); ttft=None; usage=None
    with urllib.request.urlopen(req,timeout=300) as r:
        for raw in r:                                  # SSE, one event per line
            if not raw.startswith(b"data: "): continue
            chunk=raw[6:].strip()
            if chunk==b"[DONE]": break
            try: d=json.loads(chunk)
            except Exception: continue
            if d.get("usage"): usage=d["usage"]
            for ch in d.get("choices") or []:
                if (ch.get("delta") or {}).get("content") and ttft is None:
                    ttft=(time.perf_counter()-t0)*1000
    return ttft,(time.perf_counter()-t0)*1000,usage

def pct(v,q):
    if not v: return float("nan")
    s=sorted(v); i=min(len(s)-1,max(0,int(round(q*(len(s)-1)))))
    return s[i]

ARMS=CFG["arms"]
bodies={a["label"]:build(a) for a in ARMS}
print(f"{len(ARMS)} arms x {N} samples (plus {WARM} discarded warm-ups each)")
print("service tier: per-arm (see the table)" if RUN_TIER is None
      else f"service tier: {RUN_TIER}" + ("  (some arms override it — see the table)"
           if any(a.get("service_tier") for a in ARMS) else ""))
if N < 30:
    print(f"WARNING: n={N}, below the n>=30 M8 requires. Latency is heavy-tailed and\n"
          f"server-load dominated; under-powered runs flip the ordering. Validated at n=3, a\n"
          f"medium-effort arm measured FASTER than the same prompt at low effort, which\n"
          f"cannot be real. Treat anything under n=30 as a harness check, not a result.")
print("input tokens are held constant: every arm sends identical messages\n")

# Warm-ups are discarded: the first call on a prefix pays uncached prefill, so a cold
# arm compared against a warm one is a comparison of cache states, not of configs.
for a in ARMS:
    for _ in range(WARM):
        try: one(bodies[a["label"]])
        except Exception: pass
print("warm-up complete\n")

res=defaultdict(lambda: {"ttft":[],"ttlt":[],"usage":None})
# Round-robin, NOT arm-at-a-time: batching an arm measures server load and time of day
# as much as it measures the config.
for i in range(N):
    for a in ARMS:
        lbl=a["label"]
        try:
            ttft,ttlt,usage=one(bodies[lbl])
            if ttft is not None: res[lbl]["ttft"].append(ttft)
            res[lbl]["ttlt"].append(ttlt)
            if usage: res[lbl]["usage"]=usage
        except Exception as e:
            print(f"  sample {i+1} {lbl}: error {str(e)[:60]}")
    print(f"  round {i+1}/{N} done",flush=True)

if N < 30:
    print("\nn below 30 - the table below is a harness check, not a comparison.")
# p50/p95/p99 for both, so this table lines up with eng:analyze-aura-latency's tail
print(f"\n{'arm':16}{'tier':>9}{'n':>4}{'TTFT p50':>10}{'p95':>8}{'p99':>8}"
      f"{'TTLT p50':>10}{'p95':>8}{'p99':>8}{'in tok':>8}{'out':>7}{'reason':>8}")
for a in ARMS:
    lbl=a["label"]; r=res[lbl]; u=r["usage"] or {}
    rt=(u.get("completion_tokens_details") or {}).get("reasoning_tokens","-")
    tier=bodies[lbl]["service_tier"]
    print(f"{lbl:16}{tier:>9}{len(r['ttft']):>4}{pct(r['ttft'],.50):>10.0f}{pct(r['ttft'],.95):>8.0f}{pct(r['ttft'],.99):>8.0f}"
          f"{pct(r['ttlt'],.50):>10.0f}{pct(r['ttlt'],.95):>8.0f}{pct(r['ttlt'],.99):>8.0f}"
          f"{u.get('prompt_tokens','-'):>8}{u.get('completion_tokens','-'):>7}{rt:>8}")
inp={ (res[a['label']]['usage'] or {}).get('prompt_tokens') for a in ARMS }
if len({i for i in inp if i}) > 1:
    print("\nWARNING: input token counts differ between arms, so this is not a clean config\n"
          "comparison - some of the difference is prompt length.")
print("""
Interpretation limit. This harness sends a small system prompt; production sends
~18-20K cached tokens through IntelligenceService, which shifts the balance between
prefill and queueing. Only the RELATIVE ORDERING between these configs transfers -
the absolute milliseconds do not. An arm 40% faster here is not 40% faster in prod.
The tier column is part of the comparison, not decoration: flex trades latency for half
the price, so two arms on different tiers are not a config comparison.
On a reasoning model most of TTFT is the reasoning block, so this chiefly measures the
reasoning-effort lever. Post-release, eng:analyze-aura-latency per prompter is the
number that counts.""")
