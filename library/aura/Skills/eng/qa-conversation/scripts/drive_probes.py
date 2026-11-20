import json,os,sys,urllib.request,concurrent.futures as cf
KEY=os.environ["OPENAI_EVALUATIONS_API_KEY"]
sys.path.insert(0,os.path.dirname(os.path.abspath(__file__)))
import service_tier
# Eval traffic: nobody waits on it, so half price by default.
# Override with AINATIVE_OPENAI_SERVICE_TIER=standard|fast.
TIER=service_tier.wire_value()
P=json.load(open(os.environ["DRIVE_PROMPT"]))["prompt_data"]
SYS=[{"role":"system","content":m["content"]} for m in P["prompt"]["messages"] if m["role"]=="system"]
O=P["options"]; PARAMS=O["params"]
if len(sys.argv)<3:
    raise SystemExit("usage: DRIVE_PROMPT=<prompt.json> drive_probes.py <probes.json> <out.json>")
PROBES=json.load(open(sys.argv[1]))
def send(msgs):
    b={"model":O["model"],"messages":SYS+msgs,"service_tier":TIER,
       "reasoning_effort":PARAMS.get("reasoning_effort"),"verbosity":PARAMS.get("verbosity")}
    b={k:v for k,v in b.items() if v is not None}
    req=urllib.request.Request("https://api.openai.com/v1/chat/completions",data=json.dumps(b).encode(),
        headers={"Authorization":f"Bearer {KEY}","Content-Type":"application/json"})
    for a in range(4):
        try:
            r=json.load(urllib.request.urlopen(req,timeout=300))
            return r["choices"][0]["message"]["content"].strip()
        except Exception as e:
            if a==3:
                service_tier.warn_if_flex_refused(TIER,e)
                return f"__ERROR__ {str(e)[:150]}"
def play(item):
    pid,spec=item
    msgs=[]
    for ut in spec["turns"]:
        msgs.append({"role":"user","content":ut})
        msgs.append({"role":"assistant","content":send(msgs)})
    return pid,msgs
out={}
with cf.ThreadPoolExecutor(max_workers=6) as ex:
    for pid,msgs in ex.map(play,PROBES.items()): out[pid]=msgs
json.dump(out,open(sys.argv[2],"w"),indent=1)
print(f"wrote {sys.argv[2]}: {len(out)} probes, {sum(1 for m in out.values() for x in m if x['role']=='assistant')} coach turns")
