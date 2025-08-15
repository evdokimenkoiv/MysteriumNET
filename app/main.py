import os, json, sqlite3, subprocess, secrets, csv, io
from datetime import datetime
from typing import Optional
from fastapi import FastAPI, Request, Form, HTTPException, Depends, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, PlainTextResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from jinja2 import Environment, FileSystemLoader, select_autoescape

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
        c.execute('''CREATE TABLE IF NOT EXISTS nodes (
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
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS wallets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            label TEXT NOT NULL,
            address TEXT NOT NULL
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )''')
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

@app.get("/nodes", response_class=HTMLResponse)
def nodes_page(request: Request, _: bool = Depends(require_login)):
    with db_conn() as c:
        nodes = [dict(r) for r in c.execute("SELECT * FROM nodes ORDER BY id DESC")]
    tmpl = env.get_template("nodes.html")
    return tmpl.render(nodes=nodes)

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

@app.get("/wallets", response_class=HTMLResponse)
def wallets_page(request: Request, _: bool = Depends(require_login)):
    with db_conn() as c:
        wallets = [dict(r) for r in c.execute("SELECT * FROM wallets ORDER BY label")]
    tmpl = env.get_template("wallets.html")
    return tmpl.render(wallets=wallets)

@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, _: bool = Depends(require_login)):
    tmpl = env.get_template("settings.html")
    return tmpl.render(settings={})
