#!/usr/bin/env python3
"""DM / cross-thread mechanics over App Server (stdio). Ephemeral threads.
Tests: inject_items semantics, dm-via-turn/start to idle thread, delivery to busy thread."""
import json, os, subprocess, threading, queue, time, tempfile, sys
CWD = sys.argv[1] if len(sys.argv) > 1 else "/tmp"
p = subprocess.Popen(["codex","app-server","--listen","stdio://"],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,bufsize=1)
q=queue.Queue()
def rd(s):
    for line in s:
        line=line.rstrip("\n")
        if line:
            try:q.put(json.loads(line))
            except:q.put({"_raw":line})
threading.Thread(target=rd,args=(p.stdout,),daemon=True).start()
_id=[0]; state={}
def send(method,params=None,req=True):
    m={"method":method}
    if req:_id[0]+=1;m["id"]=_id[0]
    if params is not None:m["params"]=params
    p.stdin.write(json.dumps(m)+"\n");p.stdin.flush();return m.get("id")
def pump(secs,collect=None):
    end=time.time()+secs;got=[]
    while time.time()<end:
        try:m=q.get(timeout=max(0.01,end-time.time()))
        except queue.Empty:break
        got.append(m)
        r=m.get("result") or {}
        if isinstance(r.get("thread"),dict): state[m.get("id")]=r["thread"]["id"]
    return got
def wait_result(rid,secs=8):
    end=time.time()+secs
    while time.time()<end:
        for m in pump(0.5):
            pass
        if rid in state: return state[rid]
    return None
def start_thread(cwd):
    rid=send("thread/start",{"cwd":cwd,"sandbox":"read-only","approvalPolicy":"never","ephemeral":True})
    t0=time.time()
    while time.time()-t0<8:
        pump(0.5)
        if rid in state:return state[rid]
    return None
def run_turn(tid,text,secs=70):
    send("turn/start",{"threadId":tid,"input":[{"type":"text","text":text}],"cwd":CWD,"approvalPolicy":"never","effort":"low","sandboxPolicy":{"type":"readOnly"}})
    msgs=[];end=time.time()+secs
    while time.time()<end:
        b=pump(2);msgs+=b
        if any(x.get("method")=="turn/completed" for x in b):break
        if any(x.get("method")=="turn/failed" for x in b):break
    txt=""
    for m in msgs:
        if m.get("method")=="item/completed":
            it=m.get("params",{}).get("item",{})
            if it.get("type")=="agentMessage": txt=it.get("text","")
    return msgs,txt

send("initialize",{"clientInfo":{"name":"dm","version":"0"},"capabilities":{"experimentalApi":False}});pump(4)
send("initialized",{},req=False);pump(0.5)
A=start_thread(CWD); B=start_thread(CWD)
print("thread A:",A,"\nthread B:",B)

# --- TEST 1: inject_items into B, then ask B about it ---
print("\n[T1] inject_items into B (Responses API user message), then ask B to recall it")
inj=send("thread/inject_items",{"threadId":B,"items":[
    {"type":"message","role":"user","content":[{"type":"input_text","text":"REMEMBER: the project codeword is BANANA."}]}]})
r=pump(4)
err=[m for m in r if m.get("id")==inj and "error" in m]
ok=[m for m in r if m.get("id")==inj and "result" in m]
print("   inject response:", "ERROR "+json.dumps(err[0]['error']) if err else ("ok" if ok else "no reply"))
_,ans=run_turn(B,"What is the project codeword I told you earlier? One word.")
print("   B recall answer:",repr(ans[:80]),"=> inject_items is model-visible:" , "YES" if "BANANA" in ans.upper() else "NO/unclear")

# --- TEST 2: dm via turn/start to IDLE thread A ---
print("\n[T2] DM to idle thread A via turn/start (the send_message_to_thread equivalent)")
_,ans=run_turn(A,"From @B via dm: reply with exactly the word ACK.")
print("   A processed dm, replied:",repr(ans[:80]))

# --- TEST 3: deliver to a BUSY thread (start a long turn on A, then send another) ---
print("\n[T3] deliver to BUSY thread: start turn, then turn/start again + turn/steer while active")
send("turn/start",{"threadId":A,"input":[{"type":"text","text":"Count slowly from 1 to 20, one number per line."}],"cwd":CWD,"approvalPolicy":"never","effort":"low","sandboxPolicy":{"type":"readOnly"}})
time.sleep(2)  # let it become active
busy_start=send("turn/start",{"threadId":A,"input":[{"type":"text","text":"second message while busy"}],"cwd":CWD,"approvalPolicy":"never","effort":"low","sandboxPolicy":{"type":"readOnly"}})
r=pump(3)
e2=[m for m in r if m.get("id")==busy_start and "error" in m]
print("   turn/start on busy thread:", "REJECTED "+json.dumps(e2[0]['error']) if e2 else "accepted (queued?)")
steer=send("turn/steer",{"threadId":A,"input":[{"type":"text","text":"also mention the word KIWI"}]})
r=pump(3)
es=[m for m in r if m.get("id")==steer and "error" in m]
ok_s=[m for m in r if m.get("id")==steer and "result" in m]
print("   turn/steer on busy thread:", "ERROR "+json.dumps(es[0]['error']) if es else ("ok (input added to active turn)" if ok_s else "no reply"))
# drain remainder
end=time.time()+40
while time.time()<end:
    b=pump(2)
    if any(x.get("method")=="turn/completed" for x in b):break
p.terminate();print("\nDONE")
