import json, os, sys, urllib.request, concurrent.futures as cf
KEY=os.environ["OPENAI_EVALUATIONS_API_KEY"]
sys.path.insert(0,os.path.dirname(os.path.abspath(__file__)))
import service_tier
# Eval traffic: nobody waits on it, so half price by default.
# Override with AINATIVE_OPENAI_SERVICE_TIER=standard|fast.
TIER=service_tier.wire_value()
P=json.load(open(os.environ.get("DRIVE_PROMPT","dev3.json")))["prompt_data"]
SYS=[{"role":"system","content":m["content"]} for m in P["prompt"]["messages"] if m["role"]=="system"]
OPTS=P["options"]; MODEL=OPTS["model"]; PARAMS=OPTS["params"]
STATE=os.environ.get("DRIVE_STATE","drive_state_dev2.json")

def load(): return json.load(open(STATE)) if os.path.exists(STATE) else {}
def save(s): json.dump(s,open(STATE,"w"),indent=1)

def send(item):
    sid, msgs = item
    body={"model":MODEL,"messages":SYS+msgs,"service_tier":TIER,
          "reasoning_effort":PARAMS.get("reasoning_effort"),
          "verbosity":PARAMS.get("verbosity")}
    body={k:v for k,v in body.items() if v is not None}
    req=urllib.request.Request("https://api.openai.com/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Authorization":f"Bearer {KEY}","Content-Type":"application/json"})
    for a in range(4):
        try:
            r=json.load(urllib.request.urlopen(req,timeout=300))
            return sid, r["choices"][0]["message"]["content"].strip(), r.get("usage",{})
        except Exception as e:
            if a==3:
                service_tier.warn_if_flex_refused(TIER,e)
                return sid, f"__ERROR__ {str(e)[:200]}", {}

if __name__=="__main__":
    turns=json.load(open(sys.argv[1]))       # {scenario_id: user turn text}
    st=load()
    # GUARD: refuse to append a duplicate consecutive user turn, and refuse if the
    # previous round left a user turn without a reply (i.e. a crashed run already wrote state).
    for sid,ut in turns.items():
        msgs=st.get(sid,{}).get("messages",[])
        if msgs and msgs[-1]["role"]=="user":
            raise SystemExit(f"ABORT {sid}: last message is an unanswered user turn — state is mid-round, do not re-run")
        prev=[m["content"] for m in msgs if m["role"]=="user"]
        if prev and prev[-1].strip()==ut.strip():
            raise SystemExit(f"ABORT {sid}: this user turn duplicates the previous one — refusing to append")
    jobs=[]
    for sid,ut in turns.items():
        msgs=st.get(sid,{}).get("messages",[])
        msgs=msgs+[{"role":"user","content":ut}]
        st.setdefault(sid,{})["messages"]=msgs
        jobs.append((sid,msgs))
    with cf.ThreadPoolExecutor(max_workers=12) as ex:
        for sid,reply,usage in ex.map(send,jobs):
            st[sid]["messages"].append({"role":"assistant","content":reply})
            st[sid].setdefault("usage",[]).append(usage)
    save(st)
    for sid in sorted(turns, key=lambda x:int(x)):
        m=st[sid]["messages"]
        print(f"\n{'='*16} SCENARIO {sid} · exchange {len([x for x in m if x['role']=='user'])} {'='*16}")
        print(f"USER: {m[-2]['content']}")
        print(f"\nAURIE: {m[-1]['content']}")
