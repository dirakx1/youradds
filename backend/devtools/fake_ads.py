"""A local stand-in for the Meta Graph, TikTok Business and Google Ads REST APIs, covering exactly the calls
yourads makes (campaign create / list / pause / budget, plus the credential probes used by the Connections page).

It is stateful (campaigns you create show up in the next list) and strict about request shapes, so a connector
that sends a malformed body fails here the way it would upstream. What it can NOT tell you is whether the real
API has changed or rejects something it accepts - for that use the sandbox/test-account smoke test
(devtools/smoke_ads.py). Error replies copy each platform's real envelope (Meta: HTTP 400 {"error": {code}},
TikTok: HTTP 200 with a non-zero "code", Google: HTTP 4xx {"error": {status}}).

Magic access tokens trigger failures: a token containing EXPIRED -> invalid credentials, NOPERM -> permission
denied, RATELIMIT -> rate limited. Anything else is accepted.

Run standalone:   python -m devtools.fake_ads --port 9100
then point the app at it (printed on start):
  META_GRAPH_URL=http://127.0.0.1:9100/meta/v26.0  TIKTOK_API_URL=http://127.0.0.1:9100/tiktok/open_api/v1.3
  GOOGLE_ADS_API_URL=http://127.0.0.1:9100/google/v18
Inspect state at /__state, request log at /__log, wipe with POST /__reset.
"""
import itertools
from datetime import date, timedelta
from urllib.parse import urlencode
import json
import re
import socket
import threading
import time
from urllib.parse import parse_qsl

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

META_OBJECTIVES = {"OUTCOME_SALES", "OUTCOME_TRAFFIC", "OUTCOME_LEADS", "OUTCOME_AWARENESS", "OUTCOME_ENGAGEMENT", "OUTCOME_APP_PROMOTION"}
TIKTOK_OBJECTIVES = {"WEB_CONVERSIONS", "TRAFFIC", "LEAD_GENERATION", "REACH", "APP_PROMOTION", "VIDEO_VIEWS", "ENGAGEMENT"}
GOOGLE_CHANNELS = {"SEARCH", "DISPLAY", "SHOPPING", "VIDEO", "PERFORMANCE_MAX", "DEMAND_GEN"}


def build_app() -> FastAPI:
    app = FastAPI(title="fake ads APIs")
    state = {"ids": itertools.count(1000), "meta": {}, "tiktok": {}, "google": {}, "budgets": {}, "log": []}

    def nid():
        return next(state["ids"])

    @app.middleware("http")
    async def record(request: Request, call_next):
        if not request.url.path.startswith("/__"):
            state["log"].append({"method": request.method, "path": request.url.path, "t": round(time.time(), 3)})
        return await call_next(request)

    @app.get("/__state")
    def dump():
        return {k: v for k, v in state.items() if k not in ("ids", "log")}

    @app.get("/__log")
    def log():
        return state["log"]

    @app.post("/__reset")
    def reset():
        for k in ("meta", "tiktok", "google", "budgets", "log"):
            state[k].clear()
        return {"ok": True}

    # ------------------------------------------------------------------ Meta Graph API
    def meta_err(message, code, status=400, typ="OAuthException"):
        return JSONResponse({"error": {"message": message, "type": typ, "code": code, "fbtrace_id": "FAKE"}}, status)

    def meta_auth(p):
        tok = p.get("access_token", "")
        if not tok:
            return meta_err("An access token is required to request this resource.", 104)
        if "EXPIRED" in tok:
            return meta_err("Error validating access token: Session has expired.", 190)
        if "NOPERM" in tok:
            return meta_err("(#200) Requires ads_management permission to manage the object.", 200, 403)
        if "RATELIMIT" in tok:
            return meta_err("(#17) User request limit reached.", 17)
        return None

    def meta_insights(acct, p, base_url, request):
        """Daily campaign rows for every campaign of the account over time_range, 2 rows per page (exercises paging).
        Campaign names ending in '-leads' report lead actions, others purchases (with values)."""
        tr = json.loads(p.get("time_range", "{}"))
        d0, d1 = date.fromisoformat(tr["since"]), date.fromisoformat(tr["until"])
        rows = []
        for c in [c for c in state["meta"].values() if c["acct"] == acct]:
            d = d0
            while d <= d1:
                n = int(c["id"]) % 5 + 1
                row = {"campaign_id": c["id"], "date_start": d.isoformat(), "date_stop": d.isoformat(), "impressions": str(1000 * n),
                       "clicks": str(30 * n), "spend": f"{12.5 * n:.2f}", "account_currency": state.get("currency", "USD")}
                if c["name"].endswith("-leads"):
                    row["actions"] = [{"action_type": "lead", "value": str(n)}]
                else:
                    row["actions"] = [{"action_type": "purchase", "value": str(n)}, {"action_type": "omni_purchase", "value": str(n)},
                                      {"action_type": "link_click", "value": "99"}]
                    row["action_values"] = [{"action_type": "omni_purchase", "value": f"{40.0 * n:.2f}"}, {"action_type": "purchase", "value": "1.00"}]
                rows.append(row)
                d += timedelta(days=1)
        page, size = int(p.get("page", 0)), 2
        chunk = rows[page * size:(page + 1) * size]
        out = {"data": chunk, "paging": {}}
        if (page + 1) * size < len(rows):
            q = {k: v for k, v in p.items() if k != "page"}
            out["paging"]["next"] = f"{base_url}?{urlencode({**q, 'page': page + 1})}"
        return out

    @app.api_route("/meta/{version}/{path:path}", methods=["GET", "POST"])
    async def meta(version: str, path: str, request: Request):
        p = dict(request.query_params)
        if request.method == "POST":
            p.update(parse_qsl((await request.body()).decode(), keep_blank_values=True))   # Graph takes form-encoded bodies
        if (bad := meta_auth(p)):
            return bad
        camps = state["meta"]
        m = re.fullmatch(r"(act_\d+)/insights", path)
        if m and request.method == "GET":
            return meta_insights(m.group(1), p, str(request.url.replace(query="")), request)
        m = re.fullmatch(r"(act_\d+)/campaigns", path)
        if m:
            acct = m.group(1)
            if request.method == "GET":
                rows = [{k: v for k, v in c.items() if k != "acct"} for c in camps.values() if c["acct"] == acct]
                return {"data": rows[: int(p.get("limit", 25))], "paging": {}}
            for required in ("name", "objective", "special_ad_categories", "status"):
                if required not in p:
                    return meta_err(f"(#100) Param {required} must be specified.", 100, typ="OAuthException")
            if p["objective"] not in META_OBJECTIVES:
                return meta_err("Invalid parameter: objective is not valid", 100)
            if p["status"] not in ("ACTIVE", "PAUSED"):
                return meta_err("Invalid parameter: status must be ACTIVE or PAUSED on create", 100)
            try:
                json.loads(p["special_ad_categories"])
            except ValueError:
                return meta_err("Invalid parameter: special_ad_categories must be a JSON array", 100)
            cid = str(nid())
            camps[cid] = {"id": cid, "acct": acct, "name": p["name"], "status": p["status"], "objective": p["objective"]}
            return {"id": cid}
        if path in camps:
            c = camps[path]
            if request.method == "GET":
                return {k: v for k, v in c.items() if k != "acct"}
            if "status" in p:
                if p["status"] not in ("ACTIVE", "PAUSED", "ARCHIVED", "DELETED"):
                    return meta_err("Invalid parameter: status", 100)
                c["status"] = p["status"]
            if "daily_budget" in p:
                if not re.fullmatch(r"\d+", p["daily_budget"]) or int(p["daily_budget"]) < 1:
                    return meta_err("Invalid parameter: daily_budget must be an integer in the account currency's minor unit (cents)", 100)
                c["daily_budget"] = p["daily_budget"]
            return {"success": True}
        if re.fullmatch(r"(act_)?\d+", path):    # account / phone-number probes (?fields=id)
            if request.method == "POST":
                return meta_err(f"Unsupported post request. Object with ID '{path}' does not exist, cannot be loaded due to missing permissions, or does not support this operation.", 100)
            return {"id": path}
        return meta_err(f"Unknown path components: /{path}", 2500)

    # ------------------------------------------------------------------ TikTok Business API
    def tt(code=0, message="OK", data=None):
        return JSONResponse({"code": code, "message": message, "data": data if data is not None else {}, "request_id": "FAKE"})

    def tt_auth(request):
        tok = request.headers.get("access-token", "")
        if not tok or "EXPIRED" in tok:
            return tt(40105, "Access token is incorrect or has been revoked.")
        if "NOPERM" in tok:
            return tt(40001, "No permission to operate this advertiser.")
        if "RATELIMIT" in tok:
            return tt(40100, "Too many requests. Please retry later.")
        return None

    def tt_param_err(msg):
        return tt(40002, f"Parameter error: {msg}")

    @app.api_route("/tiktok/open_api/v1.3/{path:path}", methods=["GET", "POST"])
    async def tiktok(path: str, request: Request):
        if (bad := tt_auth(request)):
            return bad
        camps = state["tiktok"]
        body = {}
        if request.method == "POST":
            try:
                body = await request.json()
            except ValueError:
                return tt_param_err("body must be JSON")
        q = dict(request.query_params)
        if path == "advertiser/info/":
            try:
                ids = json.loads(q.get("advertiser_ids", ""))
            except ValueError:
                return tt_param_err("advertiser_ids must be a JSON array")
            return tt(data={"list": [{"advertiser_id": i, "name": f"Fake advertiser {i}", "currency": "USD"} for i in ids]})
        if path == "campaign/get/":
            if not q.get("advertiser_id"):
                return tt_param_err("advertiser_id is required")
            rows = [{k: v for k, v in c.items() if k != "adv"} for c in camps.values() if c["adv"] == q["advertiser_id"]]
            return tt(data={"list": rows, "page_info": {"page": 1, "page_size": int(q.get("page_size", 10)), "total_number": len(rows)}})
        if request.method != "POST" or not body.get("advertiser_id"):
            return tt_param_err("advertiser_id is required")
        adv = str(body["advertiser_id"])
        if path == "campaign/create/":
            for required in ("campaign_name", "objective_type", "budget_mode", "operation_status"):
                if not body.get(required):
                    return tt_param_err(f"{required} is required")
            if body["objective_type"] not in TIKTOK_OBJECTIVES:
                return tt_param_err("objective_type is invalid")
            if body["budget_mode"] not in ("BUDGET_MODE_DAY", "BUDGET_MODE_TOTAL", "BUDGET_MODE_INFINITE"):
                return tt_param_err("budget_mode is invalid")
            if body["budget_mode"] != "BUDGET_MODE_INFINITE" and not (isinstance(body.get("budget"), (int, float)) and body["budget"] > 0):
                return tt_param_err("budget must be a positive number")
            if body["operation_status"] not in ("ENABLE", "DISABLE"):
                return tt_param_err("operation_status must be ENABLE or DISABLE on create")
            cid = str(nid())
            camps[cid] = {"campaign_id": cid, "adv": adv, "campaign_name": body["campaign_name"], "objective_type": body["objective_type"],
                          "budget_mode": body["budget_mode"], "budget": body.get("budget", 0), "operation_status": body["operation_status"]}
            return tt(data={"campaign_id": cid})
        if path == "campaign/status/update/":
            ids = body.get("campaign_ids")
            if not isinstance(ids, list) or not ids or not all(isinstance(i, str) for i in ids):
                return tt_param_err("campaign_ids must be a non-empty list of strings")
            if body.get("operation_status") not in ("ENABLE", "DISABLE", "DELETE"):
                return tt_param_err("operation_status is invalid")
            missing = [i for i in ids if i not in camps]
            if missing:
                return tt_param_err(f"campaign_ids not found: {missing}")
            for i in ids:
                camps[i]["operation_status"] = body["operation_status"]
            return tt(data={"campaign_ids": ids})
        if path == "campaign/update/":
            c = camps.get(str(body.get("campaign_id")))
            if not c:
                return tt_param_err("campaign_id not found")
            if "budget" in body:
                if not (isinstance(body["budget"], (int, float)) and body["budget"] > 0):
                    return tt_param_err("budget must be a positive number")
                c["budget"] = body["budget"]
            return tt(data={"campaign_id": c["campaign_id"]})
        return tt(40006, f"Invalid path: {path}")

    # ------------------------------------------------------------------ Google Ads REST API
    def g_err(code, status, message):
        return JSONResponse({"error": {"code": code, "message": message, "status": status}}, code)

    def g_auth(request):
        auth = request.headers.get("authorization", "")
        if not auth.startswith("Bearer ") or "EXPIRED" in auth:
            return g_err(401, "UNAUTHENTICATED", "Request had invalid authentication credentials.")
        if not request.headers.get("developer-token"):
            return g_err(401, "UNAUTHENTICATED", "Developer token is required (DEVELOPER_TOKEN_MISSING).")
        if "NOPERM" in auth:
            return g_err(403, "PERMISSION_DENIED", "The caller does not have permission.")
        if "RATELIMIT" in auth:
            return g_err(429, "RESOURCE_EXHAUSTED", "Too many requests.")
        return None

    @app.get("/google/v18/customers:listAccessibleCustomers")
    def g_accessible(request: Request):
        return g_auth(request) or {"resourceNames": ["customers/1234567890"]}

    def camel(name):
        head, *rest = name.split("_")
        return head + "".join(w.title() for w in rest)

    @app.post("/google/v18/customers/{cid}/{op}")
    async def google(cid: str, op: str, request: Request):
        if (bad := g_auth(request)):
            return bad
        if not re.fullmatch(r"\d{10}", cid):
            return g_err(400, "INVALID_ARGUMENT", "Invalid customer ID. Use 10 digits without dashes.")
        body = await request.json()
        camps, budgets = state["google"], state["budgets"]
        if op in ("campaignBudgets:mutate", "campaigns:mutate"):
            store, kind = (budgets, "campaignBudgets") if op.startswith("campaignBudgets") else (camps, "campaigns")
            results = []
            for operation in body.get("operations", []):
                if "create" in operation:
                    c = dict(operation["create"])
                    if not c.get("name"):
                        return g_err(400, "INVALID_ARGUMENT", "name is required (REQUIRED)")
                    if kind == "campaignBudgets":
                        if int(c.get("amountMicros", 0)) <= 0:
                            return g_err(400, "INVALID_ARGUMENT", "amountMicros must be positive")
                        if c.get("deliveryMethod") not in ("STANDARD", "ACCELERATED"):
                            return g_err(400, "INVALID_ARGUMENT", "deliveryMethod is invalid")
                        c["amountMicros"] = str(int(c["amountMicros"]))      # the real API returns int64s as strings
                    else:
                        if c.get("status") not in ("ENABLED", "PAUSED"):
                            return g_err(400, "INVALID_ARGUMENT", "status must be ENABLED or PAUSED on create")
                        if c.get("advertisingChannelType") not in GOOGLE_CHANNELS:
                            return g_err(400, "INVALID_ARGUMENT", "advertisingChannelType is invalid")
                        if c.get("campaignBudget") not in {b["resourceName"] for b in budgets.values()}:
                            return g_err(400, "INVALID_ARGUMENT", "campaignBudget must be the resource name of an existing budget")
                    rid = f"customers/{cid}/{kind}/{nid()}"
                    store[rid] = {**c, "resourceName": rid}
                    results.append({"resourceName": rid})
                elif "update" in operation:
                    u = operation["update"]
                    if u.get("resourceName") not in store:
                        return g_err(400, "INVALID_ARGUMENT", f"resource not found: {u.get('resourceName')}")
                    mask = [m.strip() for m in operation.get("updateMask", "").split(",") if m.strip()]
                    if not mask:
                        return g_err(400, "INVALID_ARGUMENT", "updateMask is required for update operations")
                    for field in mask:
                        if field not in u:
                            return g_err(400, "INVALID_ARGUMENT", f"updateMask field {field} missing from the resource")
                        store[u["resourceName"]][field] = str(int(u[field])) if field == "amountMicros" else u[field]
                    results.append({"resourceName": u["resourceName"]})
                else:
                    return g_err(400, "INVALID_ARGUMENT", "operation must contain create or update")
            return {"results": results}
        if op == "googleAds:search":
            m = re.fullmatch(r"\s*SELECT\s+(.*?)\s+FROM\s+(\w+)(?:\s+WHERE\s+(.*?))?\s*", body.get("query", ""), re.I | re.S)
            if not m or m.group(2).lower() != "campaign":
                return g_err(400, "INVALID_ARGUMENT", "Unsupported query for the fake server (only SELECT ... FROM campaign); extend devtools/fake_ads.py")
            fields = [f.strip() for f in m.group(1).split(",")]
            where = m.group(3) or ""
            rows = [c for c in camps.values() if c["resourceName"].startswith(f"customers/{cid}/")]
            if "campaign.status != 'REMOVED'" in where:
                rows = [c for c in rows if c.get("status") != "REMOVED"]
            eq = re.search(r"campaign\.resource_name\s*=\s*'([^']+)'", where)
            if eq:
                rows = [c for c in rows if c["resourceName"] == eq.group(1)]
            results = []
            for c in rows:
                out = {}
                for f in fields:
                    resource, _, field = f.partition(".")
                    if resource == "campaign":
                        src = {"resource_name": c["resourceName"], "name": c["name"], "status": c["status"], "campaign_budget": c.get("campaignBudget")}
                        if field not in src:
                            return g_err(400, "INVALID_ARGUMENT", f"Unrecognized field in the query: '{f}'")
                        out.setdefault("campaign", {})[camel(field)] = src[field]
                    elif resource == "campaign_budget" and field == "amount_micros":
                        out.setdefault("campaignBudget", {})["amountMicros"] = budgets.get(c.get("campaignBudget"), {}).get("amountMicros", "0")
                    else:
                        return g_err(400, "INVALID_ARGUMENT", f"Unrecognized field in the query: '{f}'")
                results.append(out)
            return {"results": results, "fieldMask": ",".join(camel(f.split(".")[0]) + "." + camel(f.split(".")[1]) for f in fields)}
        return g_err(404, "NOT_FOUND", f"Unknown operation {op}")

    return app


def env_for(base: str) -> dict:
    """The three environment variables that point yourads at a running fake."""
    base = base.rstrip("/")
    return {"META_GRAPH_URL": f"{base}/meta/v26.0", "TIKTOK_API_URL": f"{base}/tiktok/open_api/v1.3",
            "GOOGLE_ADS_API_URL": f"{base}/google/v18"}


def serve_in_thread(port: int = 0):
    """Start the fake on 127.0.0.1 (random free port by default). Returns (base_url, stop_function)."""
    import uvicorn
    sock = socket.socket()
    sock.bind(("127.0.0.1", port))
    server = uvicorn.Server(uvicorn.Config(build_app(), log_level="warning"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.02)
    if not server.started:
        raise RuntimeError("fake ads server did not start")

    def stop():
        server.should_exit = True
        thread.join(5)
        sock.close()
    return f"http://127.0.0.1:{sock.getsockname()[1]}", stop


if __name__ == "__main__":
    import argparse
    import uvicorn
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--port", type=int, default=9100)
    port = ap.parse_args().port
    print("Fake ads APIs on http://127.0.0.1:%d - run the app with:" % port)
    for k, v in env_for(f"http://127.0.0.1:{port}").items():
        print(f"  {k}={v}")
    uvicorn.run(build_app(), host="127.0.0.1", port=port, log_level="warning")
