# Follow-up: Aurie Care fit

**Question:** of the people who triggered a guardrail, how many were at a natural moment to be offered Aurie Care (therapist and coach matching: Therapy, Coaching, Support Now, Medication Management; screens under `Products/Aurie`)?

Computed from the dataset (`dataset.json`), no new classification. Each user is placed once, by the strongest signal across their alerts, about **themselves** (`subject == "self"`):

| Bucket (strongest first) | Rule on any of the user's alerts | Offer |
|---|---|---|
| asked for help | `asked_for == "professional-help"` or `reason == "therapy-or-treatment"` | Therapy |
| self-assessment | `reason == "diagnosis"` | Therapy (assessment) |
| medication | `reason == "medication"` | Medication Management |
| ongoing struggle | `reason` in `mental-health-struggle`, `substance-use`, `eating-or-body`, and the alert's `severity` is not `crisis` | Therapy / Support Now |
| crisis | any alert with `severity == "crisis"` and none of the above | crisis line first, follow-up only |
| not a fit | everything else | — |

```python
import collections, json
rows = json.load(open("dataset.json"))
ORDER = ["asked-for-help", "self-assessment", "medication", "ongoing-struggle", "crisis", "none"]
def bucket(r):
    if r["subject"] == "self":
        if r["asked_for"] == "professional-help" or r["reason"] == "therapy-or-treatment": return "asked-for-help"
        if r["reason"] == "diagnosis": return "self-assessment"
        if r["reason"] == "medication": return "medication"
        if r["reason"] in {"mental-health-struggle", "substance-use", "eating-or-body"} and r["severity"] != "crisis":
            return "ongoing-struggle"
    return "crisis" if r["severity"] == "crisis" else "none"
best = {}
for r in rows:
    b = bucket(r); u = r["user"]
    if u not in best or ORDER.index(b) < ORDER.index(best[u]): best[u] = b
print(collections.Counter(best.values()), "of", len(best), "users")
```

Report `already in care` alongside: those users may still want a different provider, but they are not new to care. The headline is the first four buckets; say how many of those users also had a crisis alert.
