import re, json
import sys
if len(sys.argv)<2:
    raise SystemExit("usage: parse_transcripts.py <transcripts.md> [out.json]")
SRC=sys.argv[1]
lines=open(SRC,encoding="utf-8").read().split("\n")
scen=arm=None; convs={}; cur=None
for ln in lines:
    m=re.match(r"^## (\d+)\.\s",ln)
    if m: scen=int(m.group(1)); continue
    m=re.match(r"^### Arm ([A-Za-z0-9]+)\b",ln)
    if m:
        arm=m.group(1); convs.setdefault((scen,arm),[]); cur=None; continue
    m=re.match(r"^\*\*(User|Aurie):\*\*\s*(.*)$",ln)
    if m and arm is None:
        raise SystemExit(f"turn found before any '### Arm X' header (scenario {scen}). "
                         "Refusing to parse: turns would be attributed to the wrong arm.")
    if m and arm:
        convs[(scen,arm)].append([m.group(1),m.group(2).strip()]); cur=convs[(scen,arm)][-1]; continue
    if arm and cur and ln.strip() and not ln.startswith(("### ","## ","*","---","|","**")):
        cur[1]+="\n"+ln.strip()
json.dump({f"{s}|{a}":v for (s,a),v in convs.items()},open(sys.argv[2] if len(sys.argv)>2 else "full.json","w"))
print("convs:",len(convs))
_arms=sorted({k[1] for k in convs})
print("arms parsed:",_arms)
_empty=[k for k,v in convs.items() if not v]
if _empty: print("WARNING: empty conversations (arm header matched but no turns):",_empty)
n=[(k,len(v)) for k,v in list(convs.items())[:3]]
print(n)
print("roles ok:", all(v[0][0]=="User" for v in convs.values()))
