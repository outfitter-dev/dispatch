#!/usr/bin/env python3
import json, os, subprocess, threading, queue, time, tempfile, sys
REVIEWER = sys.argv[1] if len(sys.argv) > 1 else "user"
def sh(c, cwd): return subprocess.run(c, cwd=cwd, capture_output=True, text=True)
def new_repo():
    d = tempfile.mkdtemp(prefix="codex_lab4_")
    for c in (["git","init","-q"],["git","config","user.email","t@t.t"],["git","config","user.name","t"]): sh(c,d)
    open(os.path.join(d,"README.md"),"w").write("# lab\n"); sh(["git","add","-A"],d); sh(["git","commit","-qm","i"],d); return d
repo = new_repo(); print("repo:", repo, "reviewer:", REVIEWER)
p = subprocess.Popen(["codex","app-server","--listen","stdio://"],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,bufsize=1)
q = queue.Queue(); state = {"tid": None}
def rd(s):
    for line in s:
        line=line.rstrip("\n")
        if line:
            try: q.put(json.loads(line))
            except: q.put({"_raw": line})
threading.Thread(target=rd,args=(p.stdout,),daemon=True).start()
_id=[0]
def send(method,params=None,req=True):
    m={"method":method}
    if req:_id[0]+=1;m["id"]=_id[0]
    if params is not None:m["params"]=params
    p.stdin.write(json.dumps(m)+"\n");p.stdin.flush();return m.get("id")
def respond(rid,result): p.stdin.write(json.dumps({"id":rid,"result":result})+"\n");p.stdin.flush()
def pump(secs, log=True):
    end=time.time()+secs; saw=set()
    while time.time()<end:
        try: m=q.get(timeout=max(0.01,end-time.time()))
        except queue.Empty: break
        r=m.get("result") or {}
        if isinstance(r.get("thread"),dict): state["tid"]=r["thread"]["id"]
        meth=m.get("method","")
        if meth.startswith("mcpServer/startupStatus"): continue
        saw.add(meth)
        if not log: continue
        if meth=="item/started":
            it=m.get("params",{}).get("item",{}); print(f"  >item/started type={it.get('type')}")
        elif meth=="item/completed":
            it=m.get("params",{}).get("item",{}); print(f"  >item/completed type={it.get('type')} | {json.dumps(it)[:200]}")
        elif "utoApprovalReview" in meth:
            print(f"  >GUARDIAN {meth} | {json.dumps(m.get('params',{}))[:260]}")
        elif "requestApproval" in meth and "id" in m:
            pa=m.get("params",{}); print(f"  >>>APPROVAL {meth} cmd={pa.get('command')!r}")
            respond(m["id"],{"decision":"accept"}); print("     -> accepted")
        elif meth in ("turn/completed","turn/failed","turn/started","thread/status/changed","warning","serverRequest/resolved"):
            print(f"  >{meth} {json.dumps(m.get('params',{}))[:120]}")
        elif meth and "delta" not in meth:
            print(f"  >{meth}")
        if meth in ("turn/completed","turn/failed"): return True
    return False
send("initialize",{"clientInfo":{"name":"l","version":"0"},"capabilities":{"experimentalApi":False}}); pump(5, log=False)
send("initialized",{},req=False); pump(0.5, log=False)
send("thread/start",{"cwd":repo,"sandbox":"workspace-write","approvalPolicy":"untrusted","approvalsReviewer":REVIEWER})
t0=time.time()
while state["tid"] is None and time.time()-t0<8: pump(1, log=False)
print("tid:", state["tid"])
if state["tid"]:
    send("turn/start",{"threadId":state["tid"],"input":[{"type":"text","text":"Create a file notes.txt with the line HELLO. Then run `cat notes.txt`. Do it now; do not ask questions."}],"cwd":repo,"approvalPolicy":"untrusted","effort":"low","sandboxPolicy":{"type":"workspaceWrite","networkAccess":False}})
    print("--- turn stream ---")
    done=False;t0=time.time()
    while not done and time.time()-t0<260: done=pump(3)
print("notes.txt exists:", os.path.exists(os.path.join(repo,"notes.txt")), "| git:", repr(sh(["git","status","--porcelain"],repo).stdout.strip()))
p.terminate()
