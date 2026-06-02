#!/usr/bin/env python3
"""Q2: two WebSocket clients on ONE codex app-server (ws://loopback).
Does a second connection receive live events for a thread the first one drives?
Read-only, approvals never."""
import asyncio, json, os, subprocess, sys, time, contextlib
import websockets  # provided via `uv run --with websockets`

CWD = sys.argv[1] if len(sys.argv) > 1 else "/tmp"
PORT = 47821
URL = f"ws://127.0.0.1:{PORT}"

class C:
    def __init__(self, ws, label): self.ws, self.label, self._id, self.inbox = ws, label, 0, []
    async def send(self, method, params=None, req=True):
        m = {"method": method}
        if req: self._id += 1; m["id"] = self._id
        if params is not None: m["params"] = params
        await self.ws.send(json.dumps(m)); return m.get("id")
    async def pump(self, secs):
        end = time.time() + secs
        while time.time() < end:
            try:
                raw = await asyncio.wait_for(self.ws.recv(), timeout=max(0.01, end - time.time()))
                try: self.inbox.append(json.loads(raw))
                except Exception: self.inbox.append({"_raw": raw[:200]})
            except asyncio.TimeoutError: break
            except Exception: break
    def take(self):
        out = self.inbox[:]; self.inbox = []; return out

def tally(ms):
    t = {}
    for m in ms:
        k = m.get("method") or (f"RESP#{m['id']}" if "id" in m and "result" in m else "ERR" if "error" in m else next(iter(m)))
        t[k] = t.get(k, 0) + 1
    return t

async def main():
    srv = subprocess.Popen(["codex", "app-server", "--listen", URL.replace("ws://", "ws://")],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    # wait for port
    up = False
    for _ in range(100):
        if srv.poll() is not None:
            print("server exited rc", srv.returncode, srv.stderr.read()[:500]); return
        try:
            async with websockets.connect(URL):
                up = True; break
        except Exception:
            await asyncio.sleep(0.1)
    print("server up:", up)

    async with websockets.connect(URL) as wa, websockets.connect(URL) as wb:
        A, B = C(wa, "A"), C(wb, "B")
        for c in (A, B):
            await c.send("initialize", {"clientInfo": {"name": f"ws{c.label}", "version": "0"}, "capabilities": {"experimentalApi": False}})
        await A.pump(5); await B.pump(5)
        print("A init ok:", any(m.get("id") == 1 and "result" in m for m in A.inbox))
        print("B init ok:", any(m.get("id") == 1 and "result" in m for m in B.inbox))
        A.take(); B.take()
        await A.send("initialized", {}, req=False); await B.send("initialized", {}, req=False)
        await asyncio.sleep(0.3); A.take(); B.take()

        # A starts a thread
        await A.send("thread/start", {"cwd": CWD, "sandbox": "read-only", "approvalPolicy": "never"})
        await A.pump(6)
        tid = None
        for m in A.take():
            r = m.get("result") or {}
            if isinstance(r.get("thread"), dict): tid = r["thread"]["id"]
        print("A thread/start ->", tid)

        # B: does it see A's thread without resuming? does resume work + subscribe?
        await B.send("thread/list", {"limit": 50}); await B.pump(4)
        b_sees = any(t.get("id") == tid for m in B.take() if "result" in m for t in (m["result"].get("threads", []) if isinstance(m.get("result"), dict) else []))
        print(f"Q2a — B's thread/list sees A's thread: {b_sees}")
        await B.send("thread/resume", {"threadId": tid}); await B.pump(4)
        bres = [ (m.get('method') or ('RESP' if 'result' in m else 'ERR:'+str(m.get('error',{}).get('message','')[:60]))) for m in B.take()]
        print("   B thread/resume ->", bres[:6])

        # A runs a turn; capture both streams concurrently
        await A.send("turn/start", {"threadId": tid,
            "input": [{"type": "text", "text": "Reply with exactly one word: pong"}],
            "cwd": CWD, "approvalPolicy": "never", "sandboxPolicy": {"type": "readOnly"}})
        # pump both until A sees turn/completed
        a_all, b_all = [], []
        end = time.time() + 75
        while time.time() < end:
            await asyncio.gather(A.pump(1.5), B.pump(1.5))
            a_batch, b_batch = A.take(), B.take()
            a_all += a_batch; b_all += b_batch
            if any(x.get("method") == "turn/completed" for x in a_batch): break
        await B.pump(1); b_all += B.take()

        print("\nA turn methods:", dict(sorted(tally(a_all).items(), key=lambda x:-x[1])))
        b_notifs = [m for m in b_all if m.get("method")]
        print(f"\nQ2b — B received {len(b_notifs)} notification(s) during A's turn:")
        if b_notifs:
            print("   ", dict(sorted(tally(b_notifs).items(), key=lambda x:-x[1])))
            print("   => FAN-OUT: a 2nd connection (after resume) DOES get live events.")
        else:
            print("   => NO fan-out: events scoped to the originating connection.")
    srv.terminate()
    print("\nDONE")

asyncio.run(main())
