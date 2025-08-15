
import os, json, sqlite3, subprocess, secrets, csv, io
from datetime import datetime
from typing import Optional
from fastapi import FastAPI, Request, Form, HTTPException, Depends, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, PlainTextResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from jinja2 import Environment, FileSystemLoader, select_autoescape
import paramiko

DB_PATH = os.getenv("MYST_MANAGER_DB", "/opt/myst-manager/manager.db")
HOST = os.getenv("UVICORN_HOST", "0.0.0.0")
PORT = int(os.getenv("UVICORN_PORT", "8080"))
ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin")
SECRET_KEY = os.getenv("SECRET_KEY", secrets.token_hex(32))

app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, same_site="lax")
app.mount("/static", StaticFiles(directory="static"), name="static")
env = Environment(loader=FileSystemLoader("templates"), autoescape=select_autoescape())

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
            api_port INTEGER NOT NULL DEFAULT 4050,
            wallet_id INTEGER,
            payout_address TEXT,
            capacity_mbps REAL,
            tags TEXT,
            notes TEXT,
            created_at TEXT,
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
        c.execute("""CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            node_id INTEGER NOT NULL,
            ts TEXT NOT NULL,
            sessions INTEGER,
            bytes_total INTEGER,
            api_ok INTEGER,
            nat_type TEXT
        )""")
        cols = [r[1] for r in c.execute("PRAGMA table_info(nodes)")]
        if "api_port" not in cols:
            c.execute("ALTER TABLE nodes ADD COLUMN api_port INTEGER NOT NULL DEFAULT 4050")
        if "capacity_mbps" not in cols:
            c.execute("ALTER TABLE nodes ADD COLUMN capacity_mbps REAL")
        if "tags" not in cols:
            c.execute("ALTER TABLE nodes ADD COLUMN tags TEXT")
        if "created_at" not in cols:
            c.execute("ALTER TABLE nodes ADD COLUMN created_at TEXT")
db_init()

def require_login(request: Request):
    if not request.session.get("auth"):
        raise HTTPException(status_code=302, detail="Redirect", headers={"Location": "/login"})
    return True

@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    tmpl = env.get_template("login.html")
    return tmpl.render(error=None)

@app.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...)):
    if username == ADMIN_USER and password == ADMIN_PASSWORD:
        request.session["auth"] = True
        return RedirectResponse("/nodes", status_code=303)
    tmpl = env.get_template("login.html")
    return HTMLResponse(tmpl.render(error="Invalid credentials"), status_code=401)

@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)

@app.get("/", response_class=HTMLResponse)
def home_redirect():
    return RedirectResponse("/nodes", status_code=302)

def get_setting(key: str, default: Optional[str]=None) -> Optional[str]:
    with db_conn() as c:
        r = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return r["value"] if r else default

def get_wallet_address(wallet_id: Optional[int]) -> Optional[str]:
    if not wallet_id: return None
    with db_conn() as c:
        r = c.execute("SELECT address FROM wallets WHERE id=?", (wallet_id,)).fetchone()
        return r["address"] if r else None

@app.get("/nodes", response_class=HTMLResponse)
def nodes_page(request: Request, _: bool = Depends(require_login)):
    with db_conn() as c:
        nodes = [dict(r) for r in c.execute("SELECT n.*, w.label AS wallet_label FROM nodes n LEFT JOIN wallets w ON n.wallet_id=w.id ORDER BY id DESC")]
    usd_per_gb = float(get_setting("usd_per_gb","0") or 0)
    for n in nodes:
        lm = json.loads(n["last_metrics"]) if n["last_metrics"] else {}
        docker_out = (lm.get("docker") or {}).get("out","")
        api_ok = ((lm.get("api_health") or {}).get("out","") or "").strip() != ""
        n["myst_running"] = ("myst-node" in docker_out) or api_ok
        n["sessions"] = (lm.get("sessions") or {}).get("count", 0)
        n["bandwidth_mbps"] = (lm.get("bandwidth") or {}).get("mbps", 0.0)
        n["nat_type"] = (lm.get("nat") or {}).get("type","")
        cap = n.get("capacity_mbps") or 0.0
        n["utilization_pct"] = round((n["bandwidth_mbps"]/cap)*100,1) if cap else None
        bytes_total = (lm.get("sessions") or {}).get("bytes", 0)
        n["est_usd"] = round((bytes_total/1e9) * usd_per_gb, 4) if usd_per_gb else 0.0
    tmpl = env.get_template("nodes.html")
    return tmpl.render(nodes=nodes, usd_per_gb=usd_per_gb)

@app.get("/wallets", response_class=HTMLResponse)
def wallets_page(request: Request, _: bool = Depends(require_login)):
    with db_conn() as c:
        wallets = [dict(r) for r in c.execute("SELECT * FROM wallets ORDER BY label")]
    tmpl = env.get_template("wallets.html")
    return tmpl.render(wallets=wallets)

@app.get("/server", response_class=HTMLResponse)
def server_page(request: Request, _: bool = Depends(require_login)):
    with db_conn() as c:
        acls  = [dict(r) for r in c.execute("SELECT * FROM acl ORDER BY port, id")]
    tmpl = env.get_template("server.html")
    return tmpl.render(acls=acls, port_panel=PORT)

@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, _: bool = Depends(require_login)):
    def gs(k, d=""): return get_setting(k, d) or d
    settings = {"hostname": gs("hostname"), "le_email": gs("le_email"),
                "telegram_token": gs("telegram_token"), "telegram_chat": gs("telegram_chat"),
                "usd_per_gb": gs("usd_per_gb","0")}
    tmpl = env.get_template("settings.html")
    return tmpl.render(settings=settings)

@app.post("/settings/save")
def settings_save(hostname: str = Form(""), le_email: str = Form(""),
                  telegram_token: str = Form(""), telegram_chat: str = Form(""),
                  usd_per_gb: str = Form("0"), _: bool = Depends(require_login)):
    with db_conn() as c:
        c.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", ("hostname", hostname.strip()))
        c.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", ("le_email", le_email.strip()))
        c.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", ("telegram_token", telegram_token.strip()))
        c.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", ("telegram_chat", telegram_chat.strip()))
        c.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", ("usd_per_gb", usd_per_gb.strip()))
    return RedirectResponse("/settings", status_code=303)

@app.post("/wallets/add")
def wallets_add(label: str = Form(...), address: str = Form(...), _: bool = Depends(require_login)):
    with db_conn() as c:
        c.execute("INSERT INTO wallets(label,address) VALUES(?,?)", (label.strip(), address.strip()))
    return RedirectResponse("/wallets", status_code=303)

@app.post("/wallets/{wallet_id}/delete")
def wallets_delete(wallet_id: int, _: bool = Depends(require_login)):
    with db_conn() as c:
        c.execute("DELETE FROM wallets WHERE id=?", (wallet_id,))
        c.execute("UPDATE nodes SET wallet_id=NULL WHERE wallet_id=?", (wallet_id,))
    return RedirectResponse("/wallets", status_code=303)

@app.post("/nodes/add")
def add_node(host: str = Form(...), user: str = Form(...), port: int = Form(22),
             auth_type: str = Form("password"), password: str = Form(""),
             key_path: str = Form(""), wg_port: int = Form(51820), api_port: int = Form(4050),
             capacity_mbps: float = Form(None), payout_address: str = Form(""), tags: str = Form(""),
             notes: str = Form(""), _: bool = Depends(require_login)):
    use_password = 1 if auth_type == "password" else 0
    with db_conn() as c:
        c.execute("""INSERT INTO nodes(host,user,port,use_password,password,key_path,wg_port,api_port,wallet_id,payout_address,capacity_mbps,tags,notes,created_at,last_seen,last_metrics)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'),NULL,NULL)""",
                  (host,user,port,use_password,password,key_path,wg_port,api_port,None,payout_address or None,capacity_mbps,tags,notes))
    return RedirectResponse("/nodes", status_code=303)

@app.post("/nodes/{node_id}/delete")
def node_delete(node_id: int, _: bool = Depends(require_login)):
    with db_conn() as c:
        c.execute("DELETE FROM nodes WHERE id=?", (node_id,))
    return RedirectResponse("/nodes", status_code=303)

@app.post("/nodes/{node_id}/deploy")
def deploy(node_id: int, request: Request, _: bool = Depends(require_login)):
    mgmt_ip = request.client.host
    with db_conn() as c:
        r = c.execute("SELECT * FROM nodes WHERE id=?", (node_id,)).fetchone()
    if not r: raise HTTPException(404, "Node not found")
    n = dict(r)
    if n.get("last_metrics"):
        lm = json.loads(n["last_metrics"])
        docker_out = (lm.get("docker") or {}).get("out","")
        api_ok = ((lm.get("api_health") or {}).get("out","") or "").strip() != ""
        if ("myst-node" in docker_out) or api_ok:
            return RedirectResponse("/nodes", status_code=303)
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
        cmd = f"sudo MGMT_IP='{mgmt_ip}' PAYOUT_ADDRESS='{payout or ''}' WG_PORT='{n['wg_port']}' API_PORT='{n['api_port']}' bash /tmp/remote_install.sh --non-interactive"
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
    return RedirectResponse("/nodes", status_code=303)

@app.post("/nodes/{node_id}/collect")
def collect(node_id: int, _: bool = Depends(require_login)):
    with db_conn() as c:
        r = c.execute("SELECT * FROM nodes WHERE id=?", (node_id,)).fetchone()
    if not r: raise HTTPException(404, "Node not found")
    n = dict(r)
    api_port = n.get("api_port", 4050)
    cmds = {
        "uptime": "uptime -p",
        "docker": "docker ps --format '{{.Names}}|{{.Status}}' | grep myst-node || true",
        "ufw": "ufw status | sed -n '1,30p'",
        "traffic": "command -v vnstat >/dev/null 2>&1 && vnstat --oneline b || echo 'vnstat not installed'",
        "api_health": f"curl -s --max-time 2 http://127.0.0.1:{api_port}/tequilapi/health || echo ''",
        "api_sessions": f"curl -s --max-time 2 http://127.0.0.1:{api_port}/tequilapi/sessions || echo ''",
        "api_nat": f"curl -s --max-time 2 http://127.0.0.1:{api_port}/tequilapi/nat/type || echo ''"
    }
    data = {}
    def _ssh(cmd):
        try:
            if n["use_password"]:
                client = paramiko.SSHClient(); client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                client.connect(n["host"], port=n["port"], username=n["user"], password=n.get("password"), timeout=25)
                _, stdout, stderr = client.exec_command(cmd, timeout=25)
                out, err = stdout.read().decode().strip(), stderr.read().decode().strip()
                rc = stdout.channel.recv_exit_status(); client.close()
                return rc, out, err
            else:
                client = paramiko.SSHClient(); client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                pkey = paramiko.RSAKey.from_private_key_file(n.get("key_path"))
                client.connect(n["host"], port=n["port"], username=n["user"], pkey=pkey, timeout=25)
                _, stdout, stderr = client.exec_command(cmd, timeout=25)
                out, err = stdout.read().decode().strip(), stderr.read().decode().strip()
                rc = stdout.channel.recv_exit_status(); client.close()
                return rc, out, err
        except Exception as e:
            return 255, "", str(e)
    for k, cmd in cmds.items():
        rc, out, err = _ssh(cmd); data[k] = {"rc": rc, "out": out, "err": err}
    sessions_cnt = 0; bytes_total = 0
    try:
        ses = data.get("api_sessions",{}).get("out","")
        if ses:
            obj = json.loads(ses)
            if isinstance(obj, list):
                sessions_cnt = len(obj)
                for s in obj:
                    bt = s.get("bytes_sent",0) if isinstance(s,dict) else 0
                    bytes_total += int(bt) if isinstance(bt,int) else 0
    except Exception: pass
    data["sessions"] = {"count": sessions_cnt, "bytes": bytes_total}
    mbps = 0.0
    try:
        tr = data.get("traffic",{}).get("out","")
        parts = tr.split(";")
        if len(parts) >= 3:
            rx = int(parts[1]); tx = int(parts[2]); total = rx + tx
            mbps = round((total * 8) / (3600 * 24) / 1e6, 3)
    except Exception: pass
    data["bandwidth"] = {"mbps": mbps}
    nat_type = ""
    try:
        nat_out = data.get("api_nat",{}).get("out","")
        nat_type = (json.loads(nat_out).get("type") if nat_out else "") or ""
    except Exception: pass
    data["nat"] = {"type": nat_type}
    now = datetime.utcnow().isoformat()
    with db_conn() as c:
        c.execute("UPDATE nodes SET last_seen=?, last_metrics=? WHERE id=?", (now, json.dumps(data), node_id))
        c.execute("INSERT INTO metrics(node_id, ts, sessions, bytes_total, api_ok, nat_type) VALUES(?,?,?,?,?,?)",
                  (node_id, now, sessions_cnt, bytes_total, 1 if data.get('api_health',{}).get('out','') else 0, nat_type))
    return RedirectResponse("/nodes", status_code=303)

@app.post("/nodes/collect_all")
def collect_all(_: bool = Depends(require_login)):
    with db_conn() as c:
        ids = [r["id"] for r in c.execute("SELECT id FROM nodes")]
    for nid in ids:
        try: collect(nid)
        except Exception: pass
    return RedirectResponse("/nodes", status_code=303)

@app.get("/export")
def export(_: bool = Depends(require_login)):
    with db_conn() as c:
        data = {"nodes":[dict(r) for r in c.execute("SELECT * FROM nodes")],
                "wallets":[dict(r) for r in c.execute("SELECT * FROM wallets")],
                "acls":[dict(r) for r in c.execute("SELECT * FROM acl")],
                "settings":[dict(r) for r in c.execute("SELECT * FROM settings")]}
    return JSONResponse(data)

@app.post("/import_json")
async def import_json(file: UploadFile = File(...), _: bool = Depends(require_login)):
    content = await file.read()
    data = json.loads(content)
    with db_conn() as c:
        for w in data.get("wallets", []):
            c.execute("INSERT INTO wallets(label,address) VALUES(?,?)", (w["label"], w["address"]))
        for n in data.get("nodes", []):
            c.execute("""INSERT INTO nodes(host,user,port,use_password,password,key_path,wg_port,api_port,wallet_id,payout_address,capacity_mbps,tags,notes,created_at,last_seen,last_metrics)
                         VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (n["host"],n["user"],n.get("port",22),n.get("use_password",1),n.get("password"),n.get("key_path"),
                 n.get("wg_port",51820),n.get("api_port",4050),n.get("wallet_id"),
                 n.get("payout_address"), n.get("capacity_mbps"), n.get("tags"), n.get("notes"),
                 n.get("created_at"), n.get("last_seen"), n.get("last_metrics")))
        for a in data.get("acls", []):
            c.execute("INSERT INTO acl(port, proto, cidr, enabled) VALUES(?,?,?,?)", (a["port"], a.get("proto","tcp"), a["cidr"], a.get("enabled",1)))
        for s in data.get("settings", []):
            c.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (s["key"], s["value"]))
    return RedirectResponse("/nodes", status_code=303)

@app.post("/import_csv_nodes")
async def import_csv_nodes(file: UploadFile = File(...), _: bool = Depends(require_login)):
    content = (await file.read()).decode()
    reader = csv.DictReader(io.StringIO(content))
    with db_conn() as c:
        for row in reader:
            c.execute("""INSERT INTO nodes(host,user,port,use_password,password,key_path,wg_port,api_port,wallet_id,payout_address,capacity_mbps,tags,notes,created_at)
                         VALUES(?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))""",
                (row.get("host"), row.get("user","ubuntu"), int(row.get("port",22)),
                 1 if row.get("auth","password")=="password" else 0,
                 row.get("password",""), row.get("key_path"),
                 int(row.get("wg_port",51820)), int(row.get("api_port",4050)),
                 None, row.get("payout_address"), float(row.get("capacity_mbps",0) or 0), row.get("tags",""), row.get("notes","")))
    return RedirectResponse("/nodes", status_code=303)

# ACL
@app.post("/acl/add")
def acl_add(port: int = Form(...), cidr: str = Form(...), proto: str = Form("tcp"), _: bool = Depends(require_login)):
    with db_conn() as c:
        c.execute("INSERT INTO acl(port, proto, cidr, enabled) VALUES(?,?,?,1)", (port, proto, cidr))
    return RedirectResponse("/server", status_code=303)

@app.post("/acl/{acl_id}/toggle")
def acl_toggle(acl_id: int, _: bool = Depends(require_login)):
    with db_conn() as c:
        r = c.execute("SELECT enabled FROM acl WHERE id=?", (acl_id,)).fetchone()
        if not r: raise HTTPException(404, "Not found")
        newv = 0 if r["enabled"] else 1
        c.execute("UPDATE acl SET enabled=? WHERE id=?", (newv, acl_id))
    return RedirectResponse("/server", status_code=303)

@app.post("/acl/{acl_id}/delete")
def acl_delete(acl_id: int, _: bool = Depends(require_login)):
    with db_conn() as c:
        c.execute("DELETE FROM acl WHERE id=?", (acl_id,))
    return RedirectResponse("/server", status_code=303)

@app.post("/acl/apply")
def acl_apply(request: Request, _: bool = Depends(require_login)):
    client_ip = request.client.host
    with db_conn() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM acl WHERE enabled=1")]
    desired = rows + [
        {"port": 22, "proto": "tcp", "cidr": client_ip},
        {"port": PORT, "proto": "tcp", "cidr": client_ip},
    ]
    try:
        out = subprocess.run("ufw status", shell=True, capture_output=True, text=True).stdout
        if "Status: inactive" in out:
            subprocess.run("ufw default deny incoming", shell=True, check=False)
            subprocess.run("ufw default allow outgoing", shell=True, check=False)
            subprocess.run("ufw --force enable", shell=True, check=False)
    except Exception:
        subprocess.run("ufw default deny incoming", shell=True, check=False)
        subprocess.run("ufw default allow outgoing", shell=True, check=False)
        subprocess.run("ufw --force enable", shell=True, check=False)
    for port in [22, PORT, 80, 443]:
        status = subprocess.run("ufw status numbered", shell=True, capture_output=True, text=True).stdout.splitlines()
        for line in status[::-1]:
            if f"{port}/tcp" in line and ("ALLOW" in line or "ALLOW IN" in line):
                s = line.strip()
                if s.startswith("[") and "]" in s:
                    num = s[1:s.index("]")].strip()
                    subprocess.run(f"ufw delete {num}", shell=True, check=False)
        for r in desired:
            if r["port"] == port:
                cidr = r["cidr"]; proto = r.get("proto","tcp")
                subprocess.run(f"ufw allow from {cidr} to any port {port} proto {proto}", shell=True, check=False)
    return RedirectResponse("/server", status_code=303)

# TLS
@app.post("/tls/generate")
def tls_generate(hostname: str = Form(...), email: str = Form(...), _: bool = Depends(require_login)):
    with db_conn() as c:
        c.execute("INSERT INTO settings(key,value) VALUES('hostname',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (hostname.strip(),))
        c.execute("INSERT INTO settings(key,value) VALUES('le_email',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (email.strip(),))
    script = f"""#!/usr/bin/env bash
set -euo pipefail
apt-get update -y
apt-get install -y nginx certbot python3-certbot-nginx
cat >/etc/nginx/sites-available/mysteriumnet.conf <<NG
server {{
  listen 80;
  server_name {hostname};
  location / {{
    proxy_pass http://127.0.0.1:{PORT};
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
  }}
}}
NG
ln -sf /etc/nginx/sites-available/mysteriumnet.conf /etc/nginx/sites-enabled/mysteriumnet.conf
nginx -t && systemctl restart nginx
ufw allow 80/tcp || true
ufw allow 443/tcp || true
certbot --nginx -d "{hostname}" --non-interactive --agree-tos -m "{email}" --redirect
systemctl reload nginx
echo "TLS ready at https://{hostname}"
"""
    os.makedirs("generated", exist_ok=True)
    path = f"generated/setup_tls_{hostname}.sh"
    with open(path, "w") as f:
        f.write(script)
    os.chmod(path, 0o755)
    return PlainTextResponse(f"Generated: /opt/myst-manager/app/{path}\nRun it as root.")

# DB backup
@app.get("/backup_db")
def backup_db(_: bool = Depends(require_login)):
    return FileResponse(DB_PATH, filename="manager.db")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=HOST, port=PORT)
