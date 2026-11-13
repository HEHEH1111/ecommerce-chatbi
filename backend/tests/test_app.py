import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import pytest
from fastapi.testclient import TestClient
from backend.app import create_app, ROOT
from backend.store import Store

@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(state_path=tmp_path/"state.sqlite")) as c:
        yield c

def session(client):
    r=client.post("/api/sessions",json={"dataset_id":"uci_online_retail_352"})
    assert r.status_code==201
    return r.json()

def headers(s):
    return {"X-Session-Token":s["session_token"]}

def submit(client,s,key="one",question="2011年11月交易记录金额是多少？"):
    return client.post(f"/api/sessions/{s['session_id']}/messages",
                       headers=headers(s),json={"question":question,"client_message_id":key})

def test_health_never_claims_model_or_queries_ready(client):
    h=client.get("/api/health").json()
    assert h["service"]=="online"
    assert h["capabilities"]["natural_language_query"] is False
    assert h["data"]["query_available"] is False
    assert h["model"]["status"]=="not_connected"

def test_catalog_and_metrics_are_real_registered_metadata(client):
    d=client.get("/api/datasets").json()["datasets"][0]
    assert d["rows"]==541909 and d["currency"]=="GBP"
    assert d["source_sha256"]=="43465a06f2ccf7c8b5bd2892bc7defb52f97487934fe93b16ae4c3936424676d"
    m=client.get("/api/metrics").json()
    assert {x["id"] for x in m["metrics"]}=={"M01","M02","M08"}
    assert m["query_available"] is False

def test_submit_persists_and_returns_explicit_unavailable_without_results(client):
    s=session(client)
    r=submit(client,s)
    assert r.status_code==202
    response=client.get("/api/requests/"+r.json()["request_id"],headers=headers(s)).json()
    assert response["status"]=="needs_clarification"
    assert response["clarification"]["fields"]
    assert response["data"] is None
    assert response["evidence"]["execution"]=="not_executed"
    history=client.get(f"/api/sessions/{s['session_id']}/messages",headers=headers(s)).json()
    assert len(history["messages"])==1
    assert history["messages"][0]["response"]==response

def test_idempotency_retry_and_conflicting_reuse(client):
    s=session(client)
    a,b=submit(client,s),submit(client,s)
    assert a.json()["request_id"]==b.json()["request_id"]
    assert b.json()["created"] is False
    assert submit(client,s,question="不同问题").status_code==409
    assert len(client.get(f"/api/sessions/{s['session_id']}/messages",headers=headers(s)).json()["messages"])==1

def test_other_session_token_cannot_read_history_submit_or_get_request(client):
    a,b=session(client),session(client)
    rid=submit(client,a).json()["request_id"]
    assert client.get("/api/requests/"+rid,headers=headers(b)).status_code==403
    assert client.get(f"/api/sessions/{a['session_id']}/messages",headers=headers(b)).status_code==403
    assert client.post(f"/api/sessions/{a['session_id']}/messages",headers=headers(b),
                       json={"question":"test","client_message_id":"x"}).status_code==403
    assert client.get(f"/api/sessions/{b['session_id']}/messages",headers=headers(b)).json()["messages"]==[]

@pytest.mark.parametrize("question",["","  \n ","中"*2001])
def test_invalid_questions_rejected(client,question):
    s=session(client)
    assert submit(client,s,question=question).status_code==422
    assert client.get(f"/api/sessions/{s['session_id']}/messages",headers=headers(s)).json()["messages"]==[]

def test_foreign_origin_host_and_unknown_dataset(client):
    assert client.post("/api/sessions",headers={"Origin":"https://evil.example"},json={}).status_code==403
    assert client.get("/api/health",headers={"Host":"evil.example"}).status_code==400
    assert client.post("/api/sessions",json={"dataset_id":"other"}).status_code==404

def test_oversized_body(client):
    assert client.post("/api/sessions",content=json.dumps({"dataset_id":"x"*70000}),
                       headers={"Content-Type":"application/json"}).status_code==413

def test_sessions_survive_restart_and_tokens_not_stored_in_plaintext(tmp_path):
    path=tmp_path/"state.sqlite"
    with TestClient(create_app(state_path=path)) as c:
        s=session(c)
        rid=submit(c,s).json()["request_id"]
    with TestClient(create_app(state_path=path)) as c:
        assert c.get("/api/requests/"+rid,headers=headers(s)).json()["status"]=="needs_clarification"
    with sqlite3.connect(path) as con:
        digest=con.execute("SELECT token_hash FROM sessions").fetchone()[0]
    assert digest!=s["session_token"] and len(digest)==64

def test_concurrent_same_message_creates_one_record(tmp_path):
    store=Store(tmp_path/"state.sqlite");store.initialize()
    s=store.create_session("uci_online_retail_352")
    with ThreadPoolExecutor(max_workers=6) as pool:
        results=list(pool.map(lambda _:store.enqueue(s["session_id"],"same","question"),range(6)))
    assert len({r[0] for r in results})==1
    assert sum(r[1] for r in results)==1

def test_project_files_not_exposed(client):
    for url in ["/eval/golden.v0.2.json","/data/raw/Online%20Retail.xlsx","/resources/metrics.v0.2.json","/.env","/AGENTS.md"]:
        assert client.get(url).status_code==404

def test_restart_marks_unfinished_requests_with_original_identity(tmp_path):
    path=tmp_path/"state.sqlite"
    store=Store(path);store.initialize()
    s=store.create_session("uci_online_retail_352")
    rid,_=store.enqueue(s["session_id"],"unfinished","待完成的问题")
    with TestClient(create_app(state_path=path)) as c:
        result=c.get("/api/requests/"+rid,headers=headers(s)).json()
        assert result["request_id"]==rid
        assert result["status"]=="error" and result["data"] is None
        assert result["error"]=={"code":"SERVICE_RESTARTED","message":"服务已重新启动，请重新提交这条问题。","retryable":True}

def test_oversized_invalid_json_is_rejected_before_parsing(client):
    assert client.post("/api/sessions",content="{"+"x"*70000,
                       headers={"Content-Type":"application/json"}).status_code==413
