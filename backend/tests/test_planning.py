import json,sqlite3
from pathlib import Path
import httpx,pytest
from fastapi.testclient import TestClient
from backend.app import ROOT,create_app
from backend.model_client import Settings,load_settings,ModelClient,ModelFailure
from backend.dialogue import ContextUpdate,empty_context,transition
from backend.query_plan import Compiled,compile_plan,execute_readonly
from backend.planning_service import reserve,process
from backend.dialogue_store import install,read_context
from backend.store import Store

REG=json.loads((ROOT/"resources/metrics.v0.2.json").read_text(encoding="utf-8"))
COV={"start":"2010-12-01T08:26:00","end":"2011-12-09T12:50:00"}
BASE={"metric_id":"M01","start":"2011-11-01","end_exclusive":"2011-12-01"}
def model(handler):
    return ModelClient(Settings("https://test.cn-beijing.maas.aliyuncs.com/compatible-mode/v1","qwen-plus","test-key-not-real"),httpx.MockTransport(handler))
def ok(changes=BASE,action="plan"):
    return httpx.Response(200,json={"choices":[{"finish_reason":"stop","message":{"content":json.dumps({"action":action,"changes":changes})}}],"usage":{"total_tokens":100}})
def proposal(client):
    return client.interpret("test",empty_context(),REG,COV)

def test_loader_bom_quotes_and_no_secret_repr(tmp_path,monkeypatch):
    for k in ["CHATBI_MODEL_BASE_URL","CHATBI_MODEL_NAME","CHATBI_MODEL_API_KEY"]:monkeypatch.delenv(k,raising=False)
    (tmp_path/".env").write_text('CHATBI_MODEL_BASE_URL=https://test.cn-beijing.maas.aliyuncs.com/compatible-mode/v1\nCHATBI_MODEL_API_KEY="private-value"\n',encoding="utf-8-sig")
    s=load_settings(tmp_path)
    assert s.api_key=="private-value" and "private-value" not in repr(s)
    monkeypatch.setenv("CHATBI_MODEL_BASE_URL","https://untrusted.example/")
    with pytest.raises(ModelFailure):load_settings(tmp_path)

@pytest.mark.parametrize("status,code",[(401,"MODEL_AUTH_FAILED"),(403,"MODEL_PERMISSION_DENIED"),(429,"MODEL_RATE_LIMITED"),(503,"MODEL_UNAVAILABLE"),(302,"MODEL_UNAVAILABLE")])
def test_upstream_errors_do_not_expose_body(status,code):
    c=model(lambda r:httpx.Response(status,text="sensitive-upstream-detail",headers={"Location":"https://evil.example"}))
    with pytest.raises(ModelFailure) as e:proposal(c)
    assert str(e.value)==code and c.last_status==code

@pytest.mark.parametrize("body",[
 {"choices":[]},
 {"choices":[{"finish_reason":"length","message":{"content":"{}"}}]},
 {"choices":[{"finish_reason":"stop","message":{"content":'{"action":"plan","sql":"DELETE FROM retail"}'}}]},
 {"choices":[{"finish_reason":"stop","message":{"content":'{"action":"plan","changes":{"metric_id":"profit"}}'}}]},
 {"choices":[{"finish_reason":"stop","message":{"content":'{"action":"plan","changes":{"accept_partial":true}}'}}]},
])
def test_invalid_outputs_rejected(body):
    with pytest.raises(ModelFailure):proposal(model(lambda r:httpx.Response(200,json=body)))

def test_response_size_limited():
    with pytest.raises(ModelFailure):proposal(model(lambda r:httpx.Response(200,content=b"x"*270000)))

def test_timeout_has_no_retry():
    calls=[]
    def h(request):calls.append(1);raise httpx.ReadTimeout("private-value")
    with pytest.raises(ModelFailure) as e:proposal(model(h))
    assert str(e.value)=="MODEL_TIMEOUT" and len(calls)==1

def test_payload_has_json_mode_bounded_tokens_and_no_key_or_eval():
    seen=[]
    def h(r):
        p=json.loads(r.content);seen.append(p)
        assert p["response_format"]=={"type":"json_object"} and p["max_tokens"]==900
        assert p["enable_thinking"] is False
        assert "test-key-not-real" not in r.content.decode()
        assert "ground_truth" not in r.content.decode() and "golden.v" not in r.content.decode()
        return ok()
    d,e=proposal(model(h));assert d.changes.metric_id=="M01" and e["usage"]=={"total_tokens":100}
    assert len(seen)==1

def confirmed(**kw):
    c=ContextUpdate(expected_revision=0,changes={**BASE,**kw},confirm=True)
    state,_=transition(empty_context(),c,REG,COV)
    return state

@pytest.fixture
def db(tmp_path):
    p=tmp_path/"fixture.sqlite"
    with sqlite3.connect(p) as c:
        c.execute("CREATE TABLE retail(InvoiceNo TEXT,InvoiceDate TEXT,Quantity INTEGER,price_micros INTEGER,amount_micros INTEGER,Country TEXT,StockCode TEXT)")
        c.executemany("INSERT INTO retail VALUES(?,?,?,?,?,?,?)",[
          ("A","2011-11-01",2,1000000,2000000,"United Kingdom","X"),
          ("A","2011-11-01",3,1000000,3000000,"United Kingdom","Y"),
          ("cB","2011-11-02",-1,1000000,-1000000,"United Kingdom","X"),
          ("D","2011-11-03",1,0,0,"France","Z"),
          ("E","2011-10-01",1,1000000,1000000,"France","X")])
        c.execute("CREATE TABLE secrets(value TEXT)")
    return p

def test_aggregates_and_ratio_components_use_filtered_rows(db):
    assert execute_readonly(db,compile_plan(confirmed()))["rows"]==[(5000000,)]
    assert execute_readonly(db,compile_plan(confirmed(metric_id="M02")))["rows"]==[(1,)]
    assert execute_readonly(db,compile_plan(confirmed(metric_id="M08")))["rows"]==[("2011-10",1000000),("2011-11",5000000)]

def test_country_is_bound_not_interpolated(db):
    value="x' OR 1=1 --"
    p=compile_plan(confirmed(country=value))
    assert value not in p.sql and value in p.parameters
    assert execute_readonly(db,p)["rows"]==[(None,)]

@pytest.mark.parametrize("sql",["DELETE FROM retail","UPDATE retail SET Quantity=0","SELECT * FROM secrets","ATTACH DATABASE ':memory:' AS other","SELECT load_extension('bad')","PRAGMA table_info(retail)"])
def test_database_guard_denies_write_other_tables_and_extensions(db,sql):
    with pytest.raises(sqlite3.DatabaseError):execute_readonly(db,Compiled(sql,(),"M01","test"))

def test_unconfirmed_and_unknown_dimensions_cannot_compile():
    with pytest.raises(ValueError):compile_plan(empty_context())
    c=confirmed();c["conditions"]["group_by"]="sqlite_master"
    with pytest.raises(ValueError):compile_plan(c)

def test_integrated_ai_proposal_manual_confirmation_and_idempotency(tmp_path):
    calls=[]
    def h(r):calls.append(1);return ok()
    with TestClient(create_app(state_path=tmp_path/"state.sqlite",model_client=model(h))) as c:
        s=c.post("/api/sessions",json={}).json();url=f"/api/sessions/{s['session_id']}/messages";headers={"X-Session-Token":s["session_token"]}
        body={"question":"明确问题","client_message_id":"a"}
        first=c.post(url,json=body,headers=headers).json();second=c.post(url,json=body,headers=headers).json()
        assert first["request_id"]==second["request_id"] and len(calls)==1
        reply=c.get("/api/requests/"+first["request_id"],headers=headers).json()
        assert reply["status"]=="needs_clarification" and reply["context"]["interpretation"]=="model_proposed"
        r=c.post(url,json={"question":"确认","client_message_id":"b","context_update":{"expected_revision":1,"changes":{},"confirm":True}},headers=headers).json()
        result=c.get("/api/requests/"+r["request_id"],headers=headers).json()
        assert result["status"]=="plan_ready" and result["data"] is None and "SELECT" in result["plan"]["sql"]
        assert len(calls)==1
        assert c.get("/api/health").json()["model"]["status"]=="verified"

def test_pending_turn_blocks_other_updates(tmp_path):
    store=Store(tmp_path/"s.sqlite");store.initialize();install(store)
    s=store.create_session("uci_online_retail_352")["session_id"]
    reserve(store,s,"a","q")
    with pytest.raises(ValueError,match="SESSION_BUSY"):reserve(store,s,"b","q2")
    from backend.dialogue_store import submit_turn
    with pytest.raises(ValueError,match="SESSION_BUSY"):submit_turn(store,s,"c","q3",None,REG,COV)

def test_invalid_model_preserves_saved_conditions(tmp_path):
    store=Store(tmp_path/"s.sqlite");store.initialize();install(store)
    s=store.create_session("uci_online_retail_352")["session_id"]
    rid,_,previous=reserve(store,s,"a","q")
    process(store,s,rid,"q",previous,model(lambda r:httpx.Response(401,text="secret")),REG,COV)
    assert read_context(store,s)["revision"]==0
    assert json.loads(store.request(rid)["response"])["error"]["code"]=="MODEL_AUTH_FAILED"
