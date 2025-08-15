import os, json, sqlite3, subprocess, shlex
from datetime import datetime
from typing import Optional
from fastapi import FastAPI, Request, Form, HTTPException, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape
from pydantic import BaseModel
import paramiko
from dotenv import load_dotenv
from fastapi.security import HTTPBasic, HTTPBasicCredentials
import secrets

# Load env
load_dotenv(dotenv_path="/opt/myst-manager/.env", override=True)

DB_PATH = os.getenv("MYST_MANAGER_DB", "/opt/myst-manager/manager.db")
HOST = os.getenv("UVICORN_HOST", "0.0.0.0")
PORT = int(os.getenv("UVICORN_PORT", "8080"))
ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin")

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
env = Environment(loader=FileSystemLoader("templates"), autoescape=select_autoescape())
security = HTTPBasic()

# ---------- Auth ----------
def auth(credentials: HTTPBasicCredentials = Depends(security)):
    correct_username = secrets.compare_digest(credentials.username, ADMIN_USER)
    correct_password = secrets.compare_digest(credentials.password, ADMIN_PASSWORD)
    if not (correct_username and correct_password):
        raise HTTPException(status_code=401, detail="Unauthorized",
                            headers={"WWW-Authenticate": "Basic"})
    return True

# ---------- DB ----------
def db_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def db_init():
    with db_conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS nodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            host TEXT NOT NULL,
            user TEXT NOT NULL,
            port INTEGER NOT NULL DEFAULT 22,
            use_password INTEGER NOT NULL DEFAULT 1,
            password TEXT,
            key_path TEXT,
            wg_port INTEGER NOT NULL DEFAULT 51820,
            wallet_id INTEGER,
            payout_address TEXT,
            notes TEXT,
            last_seen TEXT,
            last_metrics TEXT
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS acl (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            port INTEGER NOT NULL,
            proto TEXT NOT NULL DEFAULT 'tcp',
            cidr TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS wallets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            label TEXT NOT NULL,
            address TEXT NOT NULL
        )""")
        cols = [r[1] for r in c.execute("PRAGMA table_info(nodes)")]
        if "wallet_id" not in cols:
            c.execute("ALTER TABLE nodes ADD COLUMN wallet_id INTEGER")
db_init()

class Node(BaseModel):
    host: str
    user: str
    port: int = 22
    use_password: bool = True
    password: Optional[str] = None
    key_path: Optional[str] = None
    wg_port: int = 51820
    wallet_id: Optional[int] = None
    payout_address: Optional[str] = None
    notes: Optional[str] = None

def ssh_exec(host, port, user, password=None, key_path=None, cmd="uptime", timeout=30):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        if key_path:
            pkey = paramiko.RSAKey.from_private_key_file(key_path)
            client.connect(host, port=port, username=user, pkey=pkey, timeout=timeout)
        else:
            client.connect(host, port=port, username=user, password=password, timeout=timeout)
        stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout)
        out = stdout.read().decode()
        err = stderr.read().decode()
        rc = stdout.channel.recv_exit_status()
        return rc, out, err
    finally:
        try: client.close()
        except Exception: pass

def run(cmd: str) -> str:
    proc = subprocess.run(shlex.split(cmd), capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"{cmd} -> {proc.returncode}: {proc.stderr.strip()}")
    return proc.stdout.strip()

def get_wallet_address(wallet_id: Optional[int]) -> Optional[str]:
    if not wallet_id: return None
    with db_conn() as c:
        r = c.execute("SELECT address FROM wallets WHERE id=?", (wallet_id,)).fetchone()
        return r["address"] if r else None

@app.get("/", response_class=HTMLResponse)
def index(request: Request, _: bool = Depends(auth)):
    with db_conn() as c:
        nodes = [dict(r) for r in c.execute(
            "SELECT n.*, w.label AS wallet_label FROM nodes n LEFT JOIN wallets w ON n.wallet_id=w.id ORDER BY id DESC")]
        acls  = [dict(r) for r in c.execute("SELECT * FROM acl ORDER BY port, id")]
        wallets = [dict(r) for r in c.execute("SELECT * FROM wallets ORDER BY label")]
    up = sum(1 for n in nodes if n.get("last_metrics") and "myst-node" in (json.loads(n["last_metrics"]).get("docker","{}")).get("out",""))
    total = len(nodes)
    tmpl = env.get_template("index.html")
    return tmpl.render(nodes=nodes, acls=acls, wallets=wallets, up=up, total=total, env=os.environ)

@app.post("/wallets/add")
def wallets_add(label: str = Form(...), address: str = Form(...), _: bool = Depends(auth)):
    with db_conn() as c:
        c.execute("INSERT INTO wallets(label,address) VALUES(?,?)", (label.strip(), address.strip()))
    return RedirectResponse("/", status_code=303)

@app.post("/wallets/{wallet_id}/delete")
def wallets_delete(wallet_id: int, _: bool = Depends(auth)):
    with db_conn() as c:
        c.execute("DELETE FROM wallets WHERE id=?", (wallet_id,))
        c.execute("UPDATE nodes SET wallet_id=NULL WHERE wallet_id=?", (wallet_id,))
    return RedirectResponse("/", status_code=303)

@app.post("/nodes/add")
def add_node(host: str = Form(...), user: str = Form(...), port: int = Form(22),
             auth_type: str = Form("password"), password: str = Form(""),
             key_path: str = Form(""), wg_port: int = Form(51820),
             wallet_id: int = Form(0), payout_address: str = Form(""),
             notes: str = Form(""), _: bool = Depends(auth)):
    use_password = 1 if auth_type == "password" else 0
    wallet_id = wallet_id if wallet_id != 0 else None
    with db_conn() as c:
        c.execute("""INSERT INTO nodes(host,user,port,use_password,password,key_path,wg_port,wallet_id,payout_address,notes,last_seen,last_metrics)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                  (host,user,port,use_password,password,key_path,wg_port,wallet_id,payout_address or None,notes,None,None))
    return RedirectResponse("/", status_code=303)

@app.post("/nodes/{node_id}/deploy")
def deploy(node_id: int, request: Request, _: bool = Depends(auth)):
    mgmt_ip = request.client.host
    with db_conn() as c:
        r = c.execute("SELECT * FROM nodes WHERE id=?", (node_id,)).fetchone()
    if not r: raise HTTPException(404, "Node not found")
    n = dict(r)
    payout = n.get("payout_address") or get_wallet_address(n.get("wallet_id"))
    res = {"ok": False, "stdout": "", "stderr": ""}
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        if n["use_password"]:
            client.connect(n["host"], port=n["port"], username=n["user"], password=n.get("password"), timeout=60)
        else:
            pkey = paramiko.RSAKey.from_private_key_file(n.get("key_path"))
            client.connect(n["host"], port=n["port"], username=n["user"], pkey=pkey, timeout=60)
        sftp = client.open_sftp()
        sftp.put("/opt/myst-manager/remote_install.sh", "/tmp/remote_install.sh")
        sftp.chmod("/tmp/remote_install.sh", 0o755)
        sftp.close()
        cmd = f"sudo MGMT_IP='{mgmt_ip}' PAYOUT_ADDRESS='{payout or ''}' WG_PORT='{n['wg_port']}' bash /tmp/remote_install.sh --non-interactive"
        stdin, stdout, stderr = client.exec_command(cmd, timeout=1200)
        out = stdout.read().decode()
        err = stderr.read().decode()
        rc = stdout.channel.recv_exit_status()
        res['ok'] = (rc == 0)
        res['stdout'] = out[-4000:]
        res['stderr'] = err[-4000:]
    except Exception as e:
        res['stderr'] = str(e)
    now = datetime.utcnow().isoformat()
    with db_conn() as c:
        c.execute("UPDATE nodes SET last_seen=?, last_metrics=? WHERE id=?", (now, json.dumps(res), node_id))
    return RedirectResponse("/", status_code=303)

@app.post("/nodes/{node_id}/collect")
def collect(node_id: int, _: bool = Depends(auth)):
    with db_conn() as c:
        r = c.execute("SELECT * FROM nodes WHERE id=?", (node_id,)).fetchone()
    if not r: raise HTTPException(404, "Node not found")
    n = dict(r)
    cmds = {
        "uptime": "uptime -p",
        "docker": "docker ps --format '{{.Names}}|{{.Status}}' | grep myst-node || true",
        "ufw": "ufw status | sed -n '1,30p'",
        "traffic": "command -v vnstat >/dev/null 2>&1 && vnstat --oneline b || echo 'vnstat not installed'",
        "api_health": "curl -s --max-time 2 http://127.0.0.1:4050/tequilapi/health || echo ''"
    }
    data = {}
    for k, cmd in cmds.items():
        try:
            if n["use_password"]:
                rcc, outt, errt = ssh_exec(n["host"], n["port"], n["user"], n["password"], None, cmd, timeout=20)
            else:
                rcc, outt, errt = ssh_exec(n["host"], n["port"], n["user"], None, n["key_path"], cmd, timeout=20)
            data[k] = {"rc": rcc, "out": outt.strip(), "err": errt.strip()}
        except Exception as e:
            data[k] = {"rc": 255, "out": "", "err": str(e)}
    now = datetime.utcnow().isoformat()
    with db_conn() as c:
        c.execute("UPDATE nodes SET last_seen=?, last_metrics=? WHERE id=?", (now, json.dumps(data), node_id))
    return RedirectResponse("/", status_code=303)

@app.post("/nodes/collect_all")
def collect_all(_: bool = Depends(auth)):
    with db_conn() as c:
        ids = [r["id"] for r in c.execute("SELECT id FROM nodes")]
    for nid in ids:
        try:
            collect(nid)  # reuse handler
        except Exception:
            pass
    return RedirectResponse("/", status_code=303)

@app.post("/nodes/{node_id}/delete")
def node_delete(node_id: int, _: bool = Depends(auth)):
    with db_conn() as c:
        c.execute("DELETE FROM nodes WHERE id=?", (node_id,))
    return RedirectResponse("/", status_code=303)

@app.post("/acl/add")
def acl_add(port: int = Form(...), cidr: str = Form(...), proto: str = Form("tcp"), _: bool = Depends(auth)):
    with db_conn() as c:
        c.execute("INSERT INTO acl(port, proto, cidr, enabled) VALUES(?,?,?,1)", (port, proto, cidr))
    return RedirectResponse("/", status_code=303)

@app.post("/acl/{acl_id}/toggle")
def acl_toggle(acl_id: int, _: bool = Depends(auth)):
    with db_conn() as c:
        r = c.execute("SELECT enabled FROM acl WHERE id=?", (acl_id,)).fetchone()
        if not r: raise HTTPException(404, "Not found")
        newv = 0 if r["enabled"] else 1
        c.execute("UPDATE acl SET enabled=? WHERE id=?", (newv, acl_id))
    return RedirectResponse("/", status_code=303)

@app.post("/acl/{acl_id}/delete")
def acl_delete(acl_id: int, _: bool = Depends(auth)):
    with db_conn() as c:
        c.execute("DELETE FROM acl WHERE id=?", (acl_id,))
    return RedirectResponse("/", status_code=303)

@app.post("/acl/apply")
def acl_apply(request: Request, _: bool = Depends(auth)):
    client_ip = request.client.host
    with db_conn() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM acl WHERE enabled=1")]
    # Use current PORT from env
    port_panel = int(os.getenv("UVICORN_PORT", "8080"))
    desired = rows + [
        {"port": 22, "proto": "tcp", "cidr": client_ip},
        {"port": port_panel, "proto": "tcp", "cidr": client_ip},
    ]
    try:
        out = run("ufw status")
        if "Status: inactive" in out:
            run("ufw default deny incoming")
            run("ufw default allow outgoing")
            run("ufw --force enable")
    except Exception:
        run("ufw default deny incoming")
        run("ufw default allow outgoing")
        run("ufw --force enable")
    for port in [22, port_panel, 80, 443]:
        status = subprocess.run(["ufw", "status", "numbered"], capture_output=True, text=True).stdout.splitlines()
        for line in status[::-1]:
            if f"{port}/tcp" in line and ("ALLOW" in line or "ALLOW IN" in line):
                num = None
                s = line.strip()
                if s.startswith("[") and "]" in s:
                    try:
                        num = s[1:s.index("]")].strip()
                        int(num)
                    except Exception:
                        num = None
                if num:
                    subprocess.run(["ufw", "delete", num], capture_output=True, text=True)
        for r in desired:
            if r["port"] == port:
                cidr = r["cidr"]
                proto = r.get("proto","tcp")
                try:
                    run(f"ufw allow from {cidr} to any port {port} proto {proto}")
                except Exception:
                    pass
    return RedirectResponse("/", status_code=303)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=HOST, port=PORT)
