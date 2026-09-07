"""
RKE2 Deep Monitor — Prometheus-based cluster monitoring service.
Pure Prometheus — no kubeconfig, no kubectl, no pod exec.
"""

import asyncio
import json as json_mod
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import RedirectResponse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from service.metrics_store import MetricsStore
from service.ws_manager import ConnectionManager
from service.remediation import generate_remediations
from service.alerter import Alerter
from service.prometheus_scraper import PrometheusScraper
from service.analytics import Analytics
from service import auth

log = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────────
CLUSTER_NAME_ENV = os.environ.get("CLUSTER_NAME", "")
RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS", "7"))
LISTEN_HOST = os.environ.get("LISTEN_HOST", "0.0.0.0")
LISTEN_PORT = int(os.environ.get("LISTEN_PORT", "8080"))
DB_PATH = os.environ.get("DB_PATH", "")
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")

# ── Globals ──────────────────────────────────────────────────────────────
store = MetricsStore(db_path=DB_PATH or None, retention_days=RETENTION_DAYS)
ws_manager = ConnectionManager()
alerter = Alerter(store=store)
prom_scraper = PrometheusScraper(store=store)
analytics = Analytics(store=store)
executor = ThreadPoolExecutor(max_workers=2)


def _cluster_name() -> str:
    """Cluster name from DB setting (UI-configurable), with env-var fallback."""
    return store.get_setting("cluster_name", CLUSTER_NAME_ENV) or "cluster"
_prom_task = None


def _get_latest_report():
    return store.get_latest_snapshot()


def _load_datasources():
    for ds in store.get_datasources():
        if not ds["enabled"]:
            continue
        if ds["type"] == "prometheus":
            prom_scraper.configure(ds["url"], interval=ds["scrape_interval"])
            log.info("Prometheus: %s (interval: %ds)", ds["url"], ds["scrape_interval"])


# ── Prometheus background loop ───────────────────────────────────────────
async def _prometheus_loop():
    loop = asyncio.get_event_loop()
    await asyncio.sleep(5)
    while True:
        if prom_scraper._enabled:
            try:
                report = await loop.run_in_executor(executor, prom_scraper.scrape)
                if ws_manager.client_count > 0 and isinstance(report, dict) and report.get("checks"):
                    await ws_manager.broadcast({"type": "snapshot", "data": {
                        "timestamp": report.get("timestamp"),
                        "overall_status": report.get("overall_status"),
                        "metrics_scraped": prom_scraper._metrics_scraped,
                        "check_count": len(report.get("checks", [])),
                    }})
                    await ws_manager.broadcast({"type": "checks", "data": report.get("checks", [])})
                # Evaluate alerts
                if report.get("checks"):
                    alerter.evaluate("default", _cluster_name(), report)
            except Exception as exc:
                log.warning("Prometheus loop error: %s", exc)
        await asyncio.sleep(prom_scraper._scrape_interval if prom_scraper._enabled else 30)


# ── Lifecycle ────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    global _prom_task
    logging.basicConfig(level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S")
    log.info("Krista Monitoring starting (Prometheus mode)...")
    log.info("  Retention: %d days", RETENTION_DAYS)
    auth.setup_default_admin(store)
    _load_datasources()
    _prom_task = asyncio.create_task(_prometheus_loop())
    yield
    if _prom_task:
        _prom_task.cancel()
    executor.shutdown(wait=False)


app = FastAPI(title="Krista Monitoring", version="5.0.0", lifespan=lifespan)
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
AUTH_ENABLED = True
PUBLIC_PATHS = {"/login", "/api/login", "/favicon.ico", "/api/status", "/api/health"}


# ── Auth Middleware ──────────────────────────────────────────────────────
class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        path = request.url.path
        if path in PUBLIC_PATHS or path.startswith("/ws"):
            return await call_next(request)
        token = request.cookies.get("session")
        session = auth.get_session(token) if token else None
        if not session:
            return RedirectResponse(url="/login") if not path.startswith("/api/") else JSONResponse(status_code=401, content={"error": "Not authenticated"})
        request.state.user = session
        if session["role"] == "read" and request.method in ("POST", "PUT", "DELETE") and path not in ("/api/login", "/api/logout"):
            return JSONResponse(status_code=403, content={"error": "Read-only access"})
        return await call_next(request)

app.add_middleware(AuthMiddleware)


# ── Login ────────────────────────────────────────────────────────────────
LOGIN_HTML = """<!DOCTYPE html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"><title>Krista Monitoring — Login</title>
<style>*{margin:0;padding:0;box-sizing:border-box}body{background:#0a0e1a;color:#e2e8f0;font-family:'Inter',sans-serif;display:flex;justify-content:center;align-items:center;min-height:100vh}.box{background:#131a2e;border:1px solid #2a3650;border-radius:16px;padding:40px;width:360px;box-shadow:0 8px 40px rgba(0,0,0,0.5)}.box h1{color:#06b6d4;font-size:1.3em;text-align:center;margin-bottom:24px}label{display:block;color:#8892a8;font-size:0.82em;margin-bottom:4px}input{width:100%;padding:10px;background:#0a0e1a;color:#e2e8f0;border:1px solid #2a3650;border-radius:8px;margin-bottom:16px}button{width:100%;padding:10px;background:#3b82f6;color:#fff;border:none;border-radius:8px;cursor:pointer;font-weight:600}button:hover{background:#2563eb}.err{color:#ef4444;font-size:0.82em;text-align:center;margin-bottom:12px;display:none}</style></head><body>
<div class="box"><h1>Krista Monitoring</h1><div class="err" id="err"></div><form onsubmit="login(event)"><label>Username</label><input id="u" autocomplete="username" required autofocus><label>Password</label><input id="p" type="password" autocomplete="current-password" required><button type="submit">Sign In</button></form></div>
<script>async function login(e){e.preventDefault();const err=document.getElementById('err');err.style.display='none';const r=await fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:document.getElementById('u').value,password:document.getElementById('p').value})});if(r.ok)window.location.href='/';else{const d=await r.json();err.textContent=d.error;err.style.display='block';}}</script></body></html>"""

@app.get("/login", response_class=HTMLResponse)
async def login_page(): return HTMLResponse(LOGIN_HTML)

@app.post("/api/login")
async def api_login(request: Request):
    body = await request.json()
    user = store.get_user(body.get("username", ""))
    if not user or not auth.verify_password(body.get("password", ""), user["password_hash"]):
        return JSONResponse(status_code=401, content={"error": "Invalid credentials"})
    token = auth.create_session(user["username"], user["role"])
    resp = JSONResponse(content={"message": "OK", "role": user["role"]})
    resp.set_cookie("session", token, httponly=True, max_age=auth.SESSION_EXPIRY, samesite="lax")
    return resp

@app.post("/api/logout")
async def api_logout(request: Request):
    auth.delete_session(request.cookies.get("session", ""))
    resp = JSONResponse(content={"message": "OK"})
    resp.delete_cookie("session")
    return resp

@app.get("/api/me")
async def get_me(request: Request):
    s = getattr(request.state, "user", None)
    return {"username": s["username"], "role": s["role"]} if s else {}


# ── Dashboard ────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def dashboard():
    p = os.path.join(STATIC_DIR, "index.html")
    return FileResponse(p) if os.path.exists(p) else HTMLResponse("<h1>Dashboard not found</h1>")

@app.get("/favicon.ico")
async def favicon(): return Response(status_code=204)


# ── API: Status & Overview ───────────────────────────────────────────────
@app.get("/api/health")
async def get_health():
    # Ultra-cheap liveness/readiness check. Does NOT touch the DB so it
    # never blocks behind the scrape's save_snapshot lock — kubelet probes
    # using /api/status used to time out during heavy saves.
    return {"ok": True}

@app.get("/api/status")
async def get_status():
    r = _get_latest_report()
    if not r:
        return {"overall_status": "UNKNOWN", "message": "No data yet", "collecting": prom_scraper._collecting}
    return {"timestamp": r.get("timestamp"), "overall_status": r.get("overall_status"),
            "cluster_info": r.get("cluster_info", {}), "total_duration_sec": r.get("total_duration_sec"),
            "check_count": len(r.get("checks", [])), "collect_count": prom_scraper._collect_count,
            "collecting": prom_scraper._collecting, "ws_clients": ws_manager.client_count}

@app.get("/api/overview")
async def get_overview(hours: int = Query(default=24)):
    summary = store.get_metric_summary(hours=hours)
    latest = _get_latest_report()
    counts = {"healthy": 0, "warning": 0, "critical": 0, "unknown": 0}
    if latest:
        for ch in latest.get("checks", []):
            s = ch.get("status", "UNKNOWN").lower()
            if s in counts: counts[s] += 1
    return {"status_timeline": summary["status_timeline"], "node_metrics": summary["node_metrics"],
            "check_status_counts": counts, "snapshot_count": summary["snapshot_count"],
            "latest_timestamp": latest["timestamp"] if latest else None,
            "overall_status": latest["overall_status"] if latest else "UNKNOWN",
            "cluster_info": latest.get("cluster_info", {}) if latest else {}}


# ── API: Checks ──────────────────────────────────────────────────────────
@app.get("/api/checks")
async def get_checks():
    r = _get_latest_report()
    return {"checks": r.get("checks", []), "timestamp": r.get("timestamp")} if r else {"checks": []}

@app.get("/api/checks/{category}")
async def get_checks_by_category(category: str):
    r = _get_latest_report()
    return {"checks": [c for c in r.get("checks", []) if c.get("category") == category]} if r else {"checks": []}

@app.get("/api/remediations")
async def get_remediations():
    r = _get_latest_report()
    return {"remediations": generate_remediations(r) if r else []}


# ── API: Alerts ──────────────────────────────────────────────────────────
@app.get("/api/alerts")
async def get_alerts(): return {"alerts": store.get_active_alerts()}

@app.post("/api/alerts/clear")
async def clear_alerts(): store.clear_all_active_alerts(); return {"message": "Cleared"}

@app.get("/api/alert-rules")
async def get_alert_rules(): return {"rules": store.get_alert_rules(), "webhook_configured": len(store.get_notification_channels()) > 0}

@app.post("/api/alert-rules/create")
async def create_rule(request: Request):
    b = await request.json()
    if not b.get("name") or not b.get("metric_name"): return JSONResponse(status_code=400, content={"error": "name and metric required"})
    return {"id": store.create_alert_rule(b), "message": "Rule created"}

@app.put("/api/alert-rules/{rid}")
async def update_rule(rid: int, request: Request): store.update_alert_rule(rid, await request.json()); return {"message": "Updated"}

@app.delete("/api/alert-rules/{rid}")
async def delete_rule(rid: int): store.delete_alert_rule(rid); return {"message": "Deleted"}

@app.post("/api/alert-rules/{rid}/toggle")
async def toggle_rule(rid: int, request: Request): store.toggle_alert_rule(rid, (await request.json()).get("enabled", True)); return {"message": "Toggled"}


# ── API: Notification Channels ───────────────────────────────────────────
@app.get("/api/notifications")
async def get_notifications(): return {"channels": store.get_notification_channels()}

@app.post("/api/notifications/create")
async def create_notification(request: Request):
    b = await request.json()
    if not b.get("name") or not b.get("webhook_url"): return JSONResponse(status_code=400, content={"error": "Name and URL required"})
    return {"id": store.create_notification_channel(b["name"], b["webhook_url"], b.get("categories", "")), "message": "Created"}

@app.put("/api/notifications/{cid}")
async def update_notification(cid: int, request: Request):
    b = await request.json()
    if "enabled" in b and not b.get("name"):
        channels = store.get_notification_channels()
        existing = next((c for c in channels if c["id"] == cid), None)
        if existing:
            store.update_notification_channel(cid, existing["name"], existing["webhook_url"], existing["categories"], b["enabled"])
            return {"message": "Toggled"}
    store.update_notification_channel(cid, b.get("name", ""), b.get("webhook_url", ""), b.get("categories", ""), b.get("enabled", True))
    return {"message": "Updated"}

@app.delete("/api/notifications/{cid}")
async def delete_notification(cid: int): store.delete_notification_channel(cid); return {"message": "Deleted"}

@app.post("/api/notifications/{cid}/test")
async def test_notification(cid: int):
    ch = next((c for c in store.get_notification_channels() if c["id"] == cid), None)
    if not ch: return JSONResponse(status_code=404, content={"error": "Not found"})
    alerter._send_webhook(ch["webhook_url"], ch["name"], {"severity": "info", "rule_name": "Test", "cluster_name": _cluster_name(), "category": "test", "metric_name": "test", "metric_value": 0, "threshold": 0, "item_names": "test", "message": "Test notification from Krista Monitoring", "summary": "Webhook connectivity test"})
    return {"message": f"Test sent to '{ch['name']}'"}


# ── API: Analytics ───────────────────────────────────────────────────────
@app.get("/api/anomalies")
async def get_anomalies(): return {"anomalies": analytics.detect_anomalies()}

@app.get("/api/sla")
async def get_sla(hours: int = Query(default=168)): return analytics.get_uptime(hours=hours)

@app.get("/api/compare")
async def get_compare(metric: str = Query(default="cpu_percent")): return analytics.compare_periods(metric_name=metric)


# ── API: App Settings ────────────────────────────────────────────────────
@app.get("/api/settings")
async def get_settings():
    return {"cluster_name": _cluster_name(), "cluster_name_source": "db" if store.get_setting("cluster_name") else ("env" if CLUSTER_NAME_ENV else "default")}

@app.put("/api/settings")
async def update_settings(request: Request):
    b = await request.json()
    if "cluster_name" in b:
        store.set_setting("cluster_name", str(b["cluster_name"]).strip())
    return {"message": "Saved", "cluster_name": _cluster_name()}


# ── API: Data Sources ────────────────────────────────────────────────────
@app.get("/api/datasources")
async def get_datasources(): return {"datasources": store.get_datasources(), "prometheus_status": prom_scraper.status}

@app.post("/api/datasources/create")
async def create_ds(request: Request):
    b = await request.json()
    if not b.get("name") or not b.get("url"): return JSONResponse(status_code=400, content={"error": "Name and URL required"})
    ds_id = store.save_datasource(b["name"], b.get("type", "prometheus"), b["url"], int(b.get("scrape_interval", 30)))
    _load_datasources()
    return {"id": ds_id, "message": "Saved"}

@app.delete("/api/datasources/{did}")
async def delete_ds(did: int): store.delete_datasource(did); prom_scraper._enabled = False; _load_datasources(); return {"message": "Deleted"}

@app.post("/api/datasources/{did}/toggle")
async def toggle_ds(did: int, request: Request): store.toggle_datasource(did, (await request.json()).get("enabled", True)); _load_datasources(); return {"message": "Toggled"}

@app.post("/api/datasources/test")
async def test_ds(request: Request):
    url = (await request.json()).get("url", "")
    if not url: return JSONResponse(status_code=400, content={"error": "URL required"})
    prom_scraper.prometheus_url = url
    return prom_scraper.test_connection()

@app.get("/api/prometheus/status")
async def prom_status(): return prom_scraper.status


# ── API: Metrics ─────────────────────────────────────────────────────────
@app.get("/api/metrics")
async def get_metrics(metric_name: str = Query(default=None), item_name: str = Query(default=None),
                      category: str = Query(default=None), check_name: str = Query(default=None),
                      hours: int = Query(default=24), start: str = Query(default=None),
                      end: str = Query(default=None), limit: int = Query(default=5000)):
    return {"data": store.get_time_series(metric_name=metric_name, item_name=item_name, category=category, check_name=check_name, start=start, end=end, hours=hours, limit=limit)}

@app.get("/api/metrics/available")
async def get_available_metrics(): return {"metrics": store.get_available_metrics()}

@app.get("/api/snapshots")
async def get_snapshots(hours: int = Query(default=168)): return {"snapshots": store.get_snapshots(hours=hours)}

@app.get("/api/snapshots/{sid}")
async def get_snapshot_detail(sid: int):
    s = store.get_snapshot_by_id(sid)
    return s if s else JSONResponse(status_code=404, content={"error": "Not found"})


# ── API: Special pages ───────────────────────────────────────────────────
@app.get("/api/certificates")
async def get_certs():
    r = _get_latest_report()
    if not r: return {"certificates": []}
    return {"certificates": [{
        "name": i["name"], "status": i["status"], "check": c["name"],
        "days_until_expiry": i.get("metrics",{}).get("days_until_expiry"),
        "namespace": i.get("details",{}).get("namespace",""),
        "expiry_date": i.get("details",{}).get("expiry_date",""),
        "note": i.get("details",{}).get("note",""),
    } for c in r.get("checks",[]) if "cert" in c["name"].lower() for i in c.get("items",[])]}

@app.get("/api/waste")
async def get_waste():
    r = _get_latest_report()
    if not r: return {"waste": []}
    for c in r.get("checks",[]):
        if c["name"] == "Pod Resource Waste": return {"waste": c.get("items",[]), "summary": c.get("summary","")}
    return {"waste": []}

@app.get("/api/forecast")
async def get_forecast(): return analytics.get_forecast()

@app.get("/api/idle")
async def get_idle():
    r = _get_latest_report()
    if not r: return {"idle": []}
    for c in r.get("checks",[]):
        if "Unused" in c["name"]: return {"idle": c.get("items",[]), "summary": c.get("summary","")}
    return {"idle": []}


# ── API: Reports & Backup ────────────────────────────────────────────────
@app.get("/api/report/html")
async def dl_html():
    r = _get_latest_report()
    if not r: return JSONResponse(status_code=404, content={"error": "No data"})
    from reporters.html_report import _build_html
    ts = r.get("timestamp","").replace(":","").replace(" ","_").replace("-","")[:15]
    return Response(content=_build_html(r, None), media_type="text/html", headers={"Content-Disposition": f'attachment; filename="report_{ts}.html"'})

@app.get("/api/report/json")
async def dl_json():
    r = _get_latest_report()
    if not r: return JSONResponse(status_code=404, content={"error": "No data"})
    ts = r.get("timestamp","").replace(":","").replace(" ","_").replace("-","")[:15]
    return Response(content=json_mod.dumps(r, indent=2, default=str), media_type="application/json", headers={"Content-Disposition": f'attachment; filename="report_{ts}.json"'})

@app.get("/api/backup")
async def backup():
    import shutil
    if not os.path.exists(store.db_path): return JSONResponse(status_code=404, content={"error": "No DB"})
    bak = store.db_path + ".backup"; shutil.copy2(store.db_path, bak)
    return FileResponse(bak, media_type="application/octet-stream", filename=f"krista_monitoring_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db")

@app.post("/api/restore")
async def restore(request: Request):
    import shutil
    body = await request.body()
    if len(body) < 100: return JSONResponse(status_code=400, content={"error": "Invalid"})
    shutil.copy2(store.db_path, store.db_path + ".pre_restore")
    with open(store.db_path, "wb") as f: f.write(body)
    store._init_db()
    return {"message": "Restored"}

@app.post("/api/report/send")
async def send_report():
    channels = store.get_notification_channels()
    if not channels: return JSONResponse(status_code=400, content={"error": "No notification channels configured"})
    r = _get_latest_report()
    if not r: return JSONResponse(status_code=404, content={"error": "No data"})
    import requests as req
    checks = r.get("checks",[])
    cn = _cluster_name()
    payload = {"@type":"MessageCard","@context":"http://schema.org/extensions","themeColor":"10b981",
               "summary": f"Krista Monitoring Report — {cn}",
               "sections":[{"activityTitle":f"Krista Monitoring Report — {cn}","facts":[
                   {"name":"Status","value":r.get("overall_status","?")},
                   {"name":"Nodes","value":str(r.get("cluster_info",{}).get("node_count","?"))},
                   {"name":"Checks","value":f"{sum(1 for c in checks if c['status']=='HEALTHY')} healthy, {sum(1 for c in checks if c['status']=='WARNING')} warn, {sum(1 for c in checks if c['status']=='CRITICAL')} crit"},
               ]}]}
    for ch in channels:
        if ch["enabled"]:
            try: req.post(ch["webhook_url"], json=payload, timeout=10)
            except: pass
    return {"message": "Report sent"}


# ── API: Stats & Users ───────────────────────────────────────────────────
@app.get("/api/stats")
async def get_stats(): return {**store.get_stats(), "ws_clients": ws_manager.client_count}

@app.get("/api/users")
async def list_users(): return {"users": store.get_all_users()}

@app.post("/api/users/create")
async def create_user(request: Request):
    b = await request.json()
    if not b.get("username") or not b.get("password"): return JSONResponse(status_code=400, content={"error": "Required"})
    pw_err = auth.validate_password(b["password"])
    if pw_err: return JSONResponse(status_code=400, content={"error": pw_err})
    if store.get_user(b["username"]): return JSONResponse(status_code=400, content={"error": "User exists"})
    return {"id": store.create_user(b["username"], auth.hash_password(b["password"]), b.get("role","read"))}

@app.delete("/api/users/{uid}")
async def delete_user(uid: int): store.delete_user(uid); return {"message": "Deleted"}

@app.put("/api/users/{uid}/role")
async def update_role(uid: int, request: Request): store.update_user_role(uid, (await request.json()).get("role","read")); return {"message": "Updated"}

@app.put("/api/users/{uid}/password")
async def update_pw(uid: int, request: Request):
    pw = (await request.json()).get("password","")
    if not pw: return JSONResponse(status_code=400, content={"error": "Required"})
    pw_err = auth.validate_password(pw)
    if pw_err: return JSONResponse(status_code=400, content={"error": pw_err})
    store.update_user_password(uid, auth.hash_password(pw)); return {"message": "Updated"}


# ── API: Manual collect ──────────────────────────────────────────────────
@app.post("/api/collect")
async def trigger():
    if prom_scraper._collecting: return {"status": "already_collecting"}
    asyncio.ensure_future(_manual_scrape())
    return {"status": "started"}

async def _manual_scrape():
    loop = asyncio.get_event_loop()
    report = await loop.run_in_executor(executor, prom_scraper.scrape)
    if report and report.get("checks"):
        alerter.evaluate("default", _cluster_name(), report)


# ── WebSocket ────────────────────────────────────────────────────────────
@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws_manager.connect(ws)
    try:
        while True:
            data = await ws.receive_text()
            if data == "ping": await ws.send_json({"type": "pong"})
    except WebSocketDisconnect: pass
    except Exception: pass
    finally: await ws_manager.disconnect(ws)


def main():
    import uvicorn
    uvicorn.run("service.app:app", host=LISTEN_HOST, port=LISTEN_PORT, log_level=LOG_LEVEL.lower())

if __name__ == "__main__":
    main()
