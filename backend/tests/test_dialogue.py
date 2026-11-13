import json
from concurrent.futures import ThreadPoolExecutor
import pytest
from fastapi.testclient import TestClient
from backend.app import create_app,ROOT
from backend.dialogue import ContextUpdate
from backend.dialogue_store import install,submit_turn,read_context
from backend.store import Store

@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(state_path=tmp_path/"state.sqlite")) as c:
        yield c

def new(c):
    s=c.post("/api/sessions",json={}).json()
    return s["session_id"],{"X-Session-Token":s["session_token"]}

def turn(c,s,key,changes=None,rev=None,confirm=False,reset=False,text="核对条件"):
    sid,h=s
    body={"question":text,"client_message_id":key}
    if changes is not None or reset:
        body["context_update"]={"expected_revision":rev,"changes":changes or {},"confirm":confirm,"reset":reset}
    result=c.post(f"/api/sessions/{sid}/messages",json=body,headers=h)
    if result.status_code!=202:return result
    return c.get("/api/requests/"+result.json()["request_id"],headers=h)

def context(c,s):
    return c.get(f"/api/sessions/{s[0]}/context",headers=s[1]).json()

BASE={"metric_id":"M01","start":"2011-11-01","end_exclusive":"2011-12-01","country":"英国"}

def test_free_text_requests_explicit_conditions_without_guessing_year(client):
    s=new(client)
    r=turn(client,s,"a",text="上个月业绩怎么样").json()
    assert r["status"]=="needs_clarification" and r["data"] is None
    assert {f["field"] for f in r["clarification"]["fields"]}=={"metric_id","dates"}
    assert r["context"]["conditions"]["start"] is None

def test_incremental_clarification_keeps_confirmed_slots(client):
    s=new(client)
    a=turn(client,s,"a",{"metric_id":"M02"},0).json()
    assert [f["field"] for f in a["clarification"]["fields"]]==["dates"]
    b=turn(client,s,"b",{"start":"2011-10-01","end_exclusive":"2011-11-01"},1,True).json()
    assert b["context"]["conditions"]["metric_id"]=="M02"
    assert b["context"]["confirmed"] is True and b["error"]["code"]=="MODEL_NOT_CONNECTED"
    assert b["data"] is None and b["evidence"]["execution"]=="not_executed"

def test_replace_month_inherit_country_clear_country_and_reset(client):
    s=new(client)
    a=turn(client,s,"a",BASE,0,True).json()
    assert a["context"]["conditions"]["country"]=="United Kingdom"
    b=turn(client,s,"b",{"start":"2011-10-01","end_exclusive":"2011-11-01"},1,True).json()
    assert b["context"]["conditions"]["country"]=="United Kingdom"
    c=turn(client,s,"c",{"country":None},2,True).json()
    assert c["context"]["conditions"]["country"] is None
    d=turn(client,s,"d",{},3,reset=True).json()
    assert d["context"]["conditions"]["metric_id"] is None
    assert d["context"]["conditions"]["start"] is None
    assert d["context"]["confirmed"] is False

def test_new_natural_language_turn_requires_reconfirmation(client):
    s=new(client);turn(client,s,"a",BASE,0,True)
    b=turn(client,s,"b",text="改成十月").json()
    assert b["status"]=="needs_clarification"
    assert b["context"]["conditions"]["start"]=="2011-11-01"
    assert b["context"]["confirmed"] is False
    assert b["clarification"]["fields"][0]["field"]=="confirmation"

def test_history_snapshots_do_not_change_and_parent_is_traced(client):
    s=new(client)
    a=turn(client,s,"a",BASE,0,True).json()
    b=turn(client,s,"b",{"country":None},1,True).json()
    history=client.get(f"/api/sessions/{s[0]}/messages",headers=s[1]).json()
    assert history["messages"][0]["response"]["context"]["conditions"]["country"]=="United Kingdom"
    assert b["parent_request_id"]==a["request_id"]

def test_new_session_has_no_inherited_slots_or_access(client):
    a,b=new(client),new(client);turn(client,a,"a",BASE,0,True)
    assert context(client,b)["revision"]==0
    assert context(client,b)["conditions"]["metric_id"] is None
    assert client.get(f"/api/sessions/{a[0]}/context",headers=b[1]).status_code==403

def test_stale_revision_rejected_without_history_or_state_mutation(client):
    s=new(client);turn(client,s,"a",BASE,0,True)
    assert turn(client,s,"b",{"country":None},0,True).status_code==409
    assert context(client,s)["revision"]==1
    assert len(client.get(f"/api/sessions/{s[0]}/messages",headers=s[1]).json()["messages"])==1

def test_exact_retry_is_idempotent_but_payload_reuse_is_rejected(client):
    s=new(client)
    a=turn(client,s,"same",BASE,0,True).json()
    b=turn(client,s,"same",BASE,0,True).json()
    assert a==b and context(client,s)["revision"]==1
    assert turn(client,s,"same",{**BASE,"country":None},0,True).status_code==409

@pytest.mark.parametrize("changes",[
    {"start":"2011-11-10","end_exclusive":"2011-11-01"},
    {"start":"2026-09-01","end_exclusive":"2026-10-01"},
    {"start":"2010-01-01","end_exclusive":"2010-02-01"}
])
def test_invalid_or_outside_dates_need_clarification(client,changes):
    s=new(client)
    r=turn(client,s,"a",{**BASE,**changes},0,True).json()
    assert r["status"]=="needs_clarification" and r["context"]["confirmed"] is False
    assert r["data"] is None

def test_partial_december_requires_explicit_consent_and_dates_reset_it(client):
    s=new(client)
    a=turn(client,s,"a",{**BASE,"start":"2011-12-01","end_exclusive":"2012-01-01"},0,True).json()
    assert "accept_partial" in [x["field"] for x in a["clarification"]["fields"]]
    b=turn(client,s,"b",{"accept_partial":True},1,True).json()
    assert b["context"]["confirmed"] and b["context"]["effective_range"]["end_exclusive"]=="2011-12-10"
    c=turn(client,s,"c",{"start":"2011-12-02"},2,True).json()
    assert not c["context"]["conditions"]["accept_partial"]

@pytest.mark.parametrize("changes",[
 {"start":"2011-12-01","end_exclusive":"2012-01-01","accept_partial":True},
 {"start":"2010-12-01","end_exclusive":"2011-01-01"},
 {"start":"2011-11-02","end_exclusive":"2011-12-01"},
 {"group_by":"Country"}
])
def test_month_ratio_disallows_partial_missing_previous_and_grouping(client,changes):
    s=new(client)
    r=turn(client,s,"a",{**BASE,"metric_id":"M08",**changes},0,True).json()
    assert r["status"]=="needs_clarification"

def test_complete_month_ratio_keeps_model_unavailable(client):
    s=new(client)
    r=turn(client,s,"a",{**BASE,"metric_id":"M08"},0,True).json()
    assert r["context"]["confirmed"] and r["error"]["code"]=="MODEL_NOT_CONNECTED"

@pytest.mark.parametrize("changes",[
 {"metric_id":"profit"},{"group_by":"password"},{"start":"not-a-date"},{"sql":"DROP TABLE"},{"start":0},{"start":"2011-11-01T00:00:00"}
])
def test_unrecognized_structured_fields_rejected(client,changes):
    s=new(client)
    assert turn(client,s,"a",changes,0,True).status_code==422
    assert context(client,s)["revision"]==0

def test_context_survives_restart_and_old_sessions_are_preserved(tmp_path):
    path=tmp_path/"state.sqlite"
    old=Store(path);old.initialize()
    x=old.create_session("uci_online_retail_352");rid,_=old.enqueue(x["session_id"],"old","旧提问")
    old.finish(rid,{"status":"error","data":None,"error":{"code":"MODEL_NOT_CONNECTED"}})
    s=(x["session_id"],{"X-Session-Token":x["session_token"]})
    with TestClient(create_app(state_path=path)) as c:
        assert context(c,s)["revision"]==0
        turn(c,s,"a",BASE,0,True)
    with TestClient(create_app(state_path=path)) as c:
        assert context(c,s)["conditions"]["country"]=="United Kingdom"
        assert len(c.get(f"/api/sessions/{s[0]}/messages",headers=s[1]).json()["messages"])==2

def test_concurrent_confirms_only_one_wins(tmp_path):
    store=Store(tmp_path/"state.sqlite");store.initialize();install(store)
    sid=store.create_session("uci_online_retail_352")["session_id"]
    registry=json.loads((ROOT/"resources/metrics.v0.2.json").read_text(encoding="utf-8"))
    coverage={"start":"2010-12-01T08:26:00","end":"2011-12-09T12:50:00"}
    update=ContextUpdate(expected_revision=0,changes=BASE,confirm=True)
    def submit(i):
        try:return submit_turn(store,sid,str(i),"confirm",update,registry,coverage)[1]
        except ValueError as exc:return str(exc)
    with ThreadPoolExecutor(max_workers=6) as pool:results=list(pool.map(submit,range(6)))
    assert results.count(True)==1 and results.count("CONTEXT_CONFLICT")==5
    assert read_context(store,sid)["revision"]==1
