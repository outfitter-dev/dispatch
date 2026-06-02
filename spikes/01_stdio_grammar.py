#!/usr/bin/env python3
"""Drive `codex app-server --listen stdio://` over pipes (raw JSONL = SDK path).
Confirms grammar + guardian. Read-only, approvals never."""
import json, os, subprocess, sys, threading, queue, time
CWD = sys.argv[1] if len(sys.argv) > 1 else "/tmp"
p = subprocess.Popen(["codex", "app-server", "--listen", "stdio://"],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
q = queue.Queue(); _id = [0]
def rd(stream, tag):
    for line in stream:
        line = line.rstrip("\n")
        if not line: continue
        if tag == "OUT":
            try: q.put(json.loads(line))
            except Exception: q.put({"_raw": line})
        else: q.put({"_stderr": line})
threading.Thread(target=rd, args=(p.stdout, "OUT"), daemon=True).start()
threading.Thread(target=rd, args=(p.stderr, "ERR"), daemon=True).start()
def send(method, params=None, req=True):
    m = {"method": method}
    if req: _id[0] += 1; m["id"] = _id[0]
    if params is not None: m["params"] = params
    p.stdin.write(json.dumps(m) + "\n"); p.stdin.flush(); return m.get("id")
def drain(secs):
    out, end = [], time.time() + secs
    while time.time() < end:
        try: out.append(q.get(timeout=max(0.01, end - time.time())))
        except queue.Empty: break
    return out
def tally(ms):
    t = {}
    for m in ms:
        k = m.get("method") or (f"RESP#{m['id']}" if "id" in m and "result" in m else
            "ERR" if "error" in m else next(iter(m)))
        t[k] = t.get(k, 0) + 1
    return t

send("initialize", {"clientInfo": {"name": "stdio-tap", "version": "0"}, "capabilities": {"experimentalApi": False}})
init = drain(6)
print("Q1 — stdio raw-JSONL initialize:")
for m in init:
    if m.get("id") == 1 and "result" in m: print("   OK. result keys:", list(m["result"].keys()))
    if "error" in m: print("   ERR", m["error"])
    if "_stderr" in m: print("   [stderr]", m["_stderr"][:160])
send("initialized", {}, req=False); time.sleep(0.3)
send("thread/start", {"cwd": CWD, "sandbox": "read-only", "approvalPolicy": "never"})
tid = None
for m in drain(6):
    r = m.get("result") or {}
    if "thread" in r and isinstance(r["thread"], dict):
        tid = r["thread"].get("id") or r["thread"].get("threadId")
        print("   thread obj keys:", list(r["thread"].keys()))
    if m.get("method") == "thread/started":
        th = (m.get("params") or {}).get("thread", {})
        tid = tid or th.get("id")
    if "error" in m: print("   thread/start ERR:", m["error"])
    if "_stderr" in m: print("   [stderr]", m["_stderr"][:160])
print("   thread/start -> tid =", tid)
turn = []
if tid:
    send("turn/start", {"threadId": tid,
        "input": [{"type": "text", "text": "Reply with exactly one word: pong"}],
        "cwd": CWD, "approvalPolicy": "never", "sandboxPolicy": {"type": "readOnly"}})
    end = time.time() + 75
    while time.time() < end:
        b = drain(2); turn += b
        if any(x.get("method") == "turn/completed" for x in b): break
print("\nGrammar — methods during one read-only turn:")
for k, v in sorted(tally(turn).items(), key=lambda x: -x[1]): print(f"   {v:>3} {k}")
# show ordered method timeline (dedup consecutive)
seq = [m.get("method") for m in turn if m.get("method")]
comp = []
for s in seq:
    if not comp or comp[-1] != s: comp.append(s)
print("\n   ordered (collapsed):", " -> ".join(comp))
# final answer text
txt = ""
for m in turn:
    if m.get("method") == "item/agentMessage/delta":
        d = m.get("params", {})
        txt += d.get("delta", d.get("text", "")) if isinstance(d, dict) else ""
print("   agent text captured:", repr(txt[:120]))
g = [m for m in turn if "uardian" in (m.get("method") or "") or "pprovalReview" in (m.get("method") or "")]
print("\nQ3 — guardian/auto-approval-review events (read-only turn):", len(g))
for m in g[:4]: print("   ", m.get("method"), json.dumps(m.get("params", {}))[:160])
p.terminate()
print("\nDONE")
