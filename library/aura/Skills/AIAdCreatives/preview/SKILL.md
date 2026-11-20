# Preview Skill

Start the tracker server if it's not already running, then give the user a clickable link to open the tracker.

---

## Steps

### Step 1 — Check if the server is already running

```bash
curl -s -o /dev/null -w "%{http_code}" http://localhost:7433/data.json 2>/dev/null
```

- If the status code is `200`, the server is already running. Skip to Step 3.
- Otherwise, proceed to Step 2.

### Step 2 — Start the server in the background

From the project root:

```bash
python3 scripts/tracker-server.py &
```

Wait ~1 second, then re-check with the same curl command to confirm it's up.

If it still fails after 2 attempts, tell the user:

> The tracker server failed to start. Try running `python3 scripts/tracker-server.py` manually and check for errors.

Stop here if the server won't start.

### Step 3 — Report to user

Tell the user:

> Tracker is live: [http://localhost:7433/tracker.html](http://localhost:7433/tracker.html)

If you just started the server (Step 2), add:

> Server started in the background. It will keep running until you close this terminal session.

That's all — do not do anything else.
