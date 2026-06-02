#!/usr/bin/env python3
"""Persisted-thread resume + multi-client fan-out (the real Q2).
A completes a turn (persists rollout), B resumes, A runs turn 2, observe B."""
import asyncio, json, subprocess, sys, time, tempfile, os
import websockets
CWD = sys.argv[1] if len(sys.argv) > 1 else "/tmp"
PORT = 47833; URL = f"ws://127.0.0.1:{PORT}"

class C:
    def __init__(s, ws, label): s.ws, s.label, s._id, s.box = ws, label, 0, []
    async def send(s, method, params=None, req=True):
        m = {"method": method}
        if req: s._id += 1; m["id"] = s._id
        if params is not None: m["params"] = params
        await s.ws.send(json.dumps(m)); return m.get("id")
    async def pump(s, secs):
        end = time.time() + secs
        while time.time() < end:
            try:
                raw = await asyncio.wait_for(s.ws.recv(), timeout=max(0.01, end - time.time()))
                try: s.box.append(json.loads(raw))
                except: s.box.append({"_raw": raw[:120]})
            except asyncio.TimeoutError: break
            except Exception: break
    def take(s):
        o = s.box[:]; s.box = []; return o
def tally(ms):
    t = {}
    for m in ms:
        k = m.get("method") or (f"RESP#{m['id']}" if "id" in m and "result" in m else "ERR" if "error" in m else next(iter(m)))
        t[k] = t.get(k, 0) + 1
    return t
async def runturn(A, tid, text):
    await A.send("turn/start", {"threadId": tid, "input": [{"type": "text", "text": text}],
        "cwd": CWD, "approvalPolicy": "never", "effort": "low", "sandboxPolicy": {"type": "readOnly"}})
    end = time.time() + 90; got = []
    while time.time() < end:
        await A.pump(1.5); b = A.take(); got += b
        if any(x.get("method") == "turn/completed" for x in b): break
    return got

async def main():
    srv = subprocess.Popen(["codex","app-server","--listen",URL],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    for _ in range(100):
        if srv.poll() is not None: print("server died",srv.stderr.read()[:300]); return
        try:
            async with websockets.connect(URL): break
        except: await asyncio.sleep(0.1)
    async with websockets.connect(URL) as wa:
        A = C(wa, "A")
        await A.send("initialize", {"clientInfo":{"name":"A","version":"0"},"capabilities":{"experimentalApi":False}}); await A.pump(5); A.take()
        await A.send("initialized", {}, req=False); await A.pump(0.3); A.take()
        await A.send("thread/start", {"cwd": CWD, "sandbox": "read-only", "approvalPolicy": "never", "ephemeral": False})
        await A.pump(6); tid = None
        for m in A.take():
            r = m.get("result") or {}
            if isinstance(r.get("thread"), dict): tid = r["thread"]["id"]
        print("A thread (ephemeral=False):", tid)
        # turn 1 to completion -> should persist a rollout
        t1 = await runturn(A, tid, "Reply with exactly one word: alpha")
        print("turn1 completed:", any(m.get("method")=="turn/completed" for m in t1))

        # B connects AFTER turn1, tries to see + resume the now-persisted thread
        async with websockets.connect(URL) as wb:
            B = C(wb, "B")
            await B.send("initialize", {"clientInfo":{"name":"B","version":"0"},"capabilities":{"experimentalApi":False}}); await B.pump(5); B.take()
            await B.send("initialized", {}, req=False); await B.pump(0.3); B.take()
            await B.send("thread/list", {"limit": 50}); await B.pump(4)
            sees = False
            for m in B.take():
                if "result" in m:
                    for t in (m["result"].get("threads", []) if isinstance(m.get("result"),dict) else []):
                        if t.get("id") == tid: sees = True
            print("B thread/list sees A's (now persisted) thread:", sees)
            rid = await B.send("thread/resume", {"threadId": tid}); await B.pump(5)
            resume_ok = False; hist = None
            for m in B.take():
                if m.get("id") == rid and "result" in m:
                    resume_ok = True
                    r = m["result"]; th = r.get("thread", {})
                    turns = th.get("turns"); hist = (th.get("itemsView"), len(turns) if isinstance(turns,list) else turns)
                if m.get("id") == rid and "error" in m: print("   resume ERR:", m["error"])
            print(f"B resume ok: {resume_ok} | (itemsView, #turns)={hist}")
            B.take()
            # A runs turn 2; does B (resumed) receive live content?
            async def watchB():
                end = time.time()+90
                while time.time()<end:
                    await B.pump(1.5)
                    if any(x.get("method")=="turn/completed" for x in B.box[-30:]): break
            t2_task = asyncio.create_task(runturn(A, tid, "Reply with exactly one word: omega"))
            wb_task = asyncio.create_task(watchB())
            t2 = await t2_task
            await wb_task
            bmsgs = [m for m in B.box if m.get("method")]
            print("\nA turn2 methods:", dict(sorted(tally(t2).items(),key=lambda x:-x[1])))
            print(f"\nB received during A's turn2: {len(bmsgs)} notifications")
            print("   ", dict(sorted(tally(bmsgs).items(),key=lambda x:-x[1])))
            content = [m for m in bmsgs if m.get("method") in
                       ("item/agentMessage/delta","item/started","item/completed","turn/started","turn/completed")]
            print(f"\n   B got CONTENT-stream events (items/deltas/turn): {len(content)}")
            print("   => " + ("B CO-RECEIVES live content after resume." if content
                              else "B does NOT get content; resume!=live subscription."))
    srv.terminate(); print("\nDONE")
asyncio.run(main())
