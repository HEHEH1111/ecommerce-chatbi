import json,sqlite3
from copy import deepcopy
import pytest
from fastapi.testclient import TestClient
from backend.app import ROOT,create_app
from backend.dialogue import ContextUpdate,empty_context,transition
from backend.query_plan import compile_plan
from backend.results import build_result,ResultValidationError
from backend.result_preview import make_previews

REG=json.loads((ROOT/"resources/metrics.v0.2.json").read_text(encoding="utf-8"))
def context(**changes):
    return transition(empty_context(),ContextUpdate(expected_revision=0,confirm=True,changes={"metric_id":"M01","start":"2011-11-01","end_exclusive":"2011-12-01",**changes}),REG,{"start":"2010-12-01T08:26:00","end":"2011-12-09T12:50:00"})[0]
def result(rows,ctx=None,columns=None,truncated=False):
    ctx=ctx or context();p=compile_plan(ctx)
    return build_result({"columns":columns or (["dimension","value"] if ctx["conditions"]["group_by"] or p.kind=="monthly_components" else ["value"]),"rows":rows,"truncated":truncated},p,ctx,REG,request_id="test-result",dataset_name="synthetic unit fixture",source_kind="synthetic",elapsed_ms=1.25)

def test_money_exactness_and_half_up_display():
    r=result([(1005000,)])
    assert r["data"]["rows"][0]["value"]=="1.005"
    assert r["data"]["summary"]["display"]=="1.01"
    assert r["evidence"]["execution"]=="fixture_query"
    assert r["evidence"]["sql"].startswith("SELECT")
    assert r["request_id"]=="test-result" and r["synthetic"]

def test_large_integer_keeps_exact_decimal_without_javascript_rounding():
    r=result([(9007199254740993,)])
    assert r["data"]["rows"][0]["value"]=="9007199254.740993"

@pytest.mark.parametrize("rows,changes",[
 ([(None,)],{}), ([(0,)],{"metric_id":"M02"}),([],{"group_by":"Country"})
])
def test_no_matching_records_are_not_errors_or_zero_amount(rows,changes):
    r=result(rows,context(**changes))
    assert r["status"]=="no_data" and r["data"]["rows"]==[] and r["data"]["summary"] is None

def test_observed_zero_amount_is_distinct_from_no_rows():
    assert result([(0,)])["status"]=="success"

@pytest.mark.parametrize("previous,current,display",[(8000000,10005000,"25.06%"),(3000000,2000000,"-33.33%"),(1000000,0,"-100.00%")])
def test_ratio_uses_exact_numerator_and_denominator(previous,current,display):
    r=result([("2011-10",previous),("2011-11",current)],context(metric_id="M08"))
    assert r["data"]["summary"]["display"]==display
    assert r["data"]["ratio"]["numerator_micros"]==str(current-previous)
    assert r["data"]["ratio"]["denominator_micros"]==str(previous)
    assert r["data"]["columns"][1]["unit"]=="GBP"
    assert r["data"]["summary"]["unit"]=="%"

@pytest.mark.parametrize("rows",[[("2011-10",0),("2011-11",1000000)],[("2011-11",1000000)],[]])
def test_undefined_ratio_never_becomes_infinity_zero_or_success(rows):
    r=result(rows,context(metric_id="M08"))
    assert r["status"]=="not_computable"
    assert r["data"]["summary"]["value"] is None
    assert r["data"]["ratio"]["denominator_micros"] in {"0",None}

@pytest.mark.parametrize("change",[
 {"columns":["other"]},{"rows":[(1.25,)]},{"rows":[(True,)]},{"rows":[(-1,)]},
 {"rows":[(9223372036854775808,)]},{"rows":[("NaN",)]},{"rows":[]},{"rows":[(1,2)]},{"truncated":True},
 {"rows":[(float("inf"),)]}
])
def test_corrupted_executor_results_are_rejected(change):
    ctx=context();raw={"columns":["value"],"rows":[(1000000,)],"truncated":False};raw.update(change)
    with pytest.raises(ResultValidationError,match="RESULT_VALIDATION_FAILED"):
        build_result(raw,compile_plan(ctx),ctx,REG,request_id="x",dataset_name="test",source_kind="synthetic",elapsed_ms=0)

def test_duplicate_groups_and_mismatched_months_rejected():
    with pytest.raises(ResultValidationError):result([("A",1),("A",2)],context(group_by="Country"))
    with pytest.raises(ResultValidationError):result([("2011-09",1)],context(metric_id="M08"))
    with pytest.raises(ResultValidationError):result([("invalid",1)],context(group_by="date"))

def test_truncated_groups_have_warning_and_no_misleading_total():
    r=result([(f"G{i}",1000000) for i in range(1000)],context(group_by="Country"),truncated=True)
    assert r["data"]["summary"] is None and r["evidence"]["truncated"]
    assert any("不代表完整结果" in w for w in r["warnings"])
    assert len(r["data"]["rows"])==1000

def test_count_group_warning_and_partial_range_evidence():
    r=result([("A",2)],context(metric_id="M02",group_by="Country"))
    assert any("不能直接相加" in w for w in r["warnings"])
    r=result([(1,)],context(start="2011-12-01",end_exclusive="2012-01-01",accept_partial=True))
    assert r["evidence"]["effective_range"]["end_exclusive"]=="2011-12-10"
    assert any("部分覆盖" in w for w in r["warnings"])

def test_evidence_versions_and_query_cannot_be_mixed():
    c=context();p=compile_plan(c);c["source_sha256"]="0"*64
    with pytest.raises(ResultValidationError):
        build_result({"columns":["value"],"rows":[(1,)],"truncated":False},p,c,REG,request_id="x",dataset_name="test",source_kind="synthetic",elapsed_ms=0)

def test_result_snapshot_survives_later_context_changes():
    c=context();r=result([(1,)],c);c["conditions"]["country"]="new value"
    assert r["evidence"]["conditions"]["country"] is None and r["context"]["conditions"]["country"] is None

def test_synthetic_preview_executes_real_queries_and_truncates():
    p=make_previews(REG);items={s["id"]:s["result"] for s in p["items"]}
    assert p["model_calls"]==0 and len(items)==7
    assert [r["value"] for r in items["trend"]["data"]["rows"]]==["2.505","3.5","4"]
    assert items["ratio"]["data"]["summary"]["display"]=="25.06%"
    assert items["invoices"]["data"]["summary"]["value"]=="3"
    assert items["empty"]["status"]=="no_data"
    assert items["missing_month"]["status"]=="not_computable"
    assert items["truncated"]["data"]["truncated"] and len(items["truncated"]["data"]["rows"])==1000
    assert all(v["synthetic"] and v["evidence"]["dataset_sha256"]!=REG["source_sha256"] for v in items.values())

def test_preview_does_not_call_model_or_write_session_history(tmp_path,monkeypatch):
    from backend.model_client import ModelClient
    def forbidden(*a,**k):raise AssertionError("unexpected model call")
    monkeypatch.setattr(ModelClient,"interpret",forbidden)
    app=create_app(state_path=tmp_path/"s.sqlite")
    with TestClient(app) as client:
        response=client.get("/api/result-previews")
        assert response.status_code==200 and response.json()["mode"]=="synthetic_component_preview"
        with app.state.store.connect() as c:
            assert c.execute("SELECT count(*) FROM requests").fetchone()[0]==0
        assert client.get("/api/health").json()["data"]["query_available"] is False

def test_preview_failure_is_explicit_not_replaced_with_numbers(tmp_path,monkeypatch):
    def fail(*args):raise ResultValidationError()
    monkeypatch.setattr("backend.app.make_previews",fail)
    with TestClient(create_app(state_path=tmp_path/"s.sqlite")) as c:
        r=c.get("/api/result-previews")
        assert r.status_code==503 and r.json()["error"]["code"]=="RESULT_PREVIEW_FAILED"
        assert "data" not in r.json()

def test_result_history_persists_exact_values_and_checks_session_access(tmp_path):
    from backend.store import now
    app=create_app(state_path=tmp_path/"s.sqlite")
    with TestClient(app) as c:
        owner=c.post("/api/sessions",json={}).json();other=c.post("/api/sessions",json={}).json();r=result([(1005000,)])
        with app.state.store.connect() as db:
            db.execute("INSERT INTO requests(id,session_id,client_message_id,question,status,response,created_at) VALUES(?,?,?,?,?,?,?)",("test-result",owner["session_id"],"test","fixture only","success",json.dumps(r),now()))
        headers={"X-Session-Token":owner["session_token"]}
        assert c.get("/api/requests/test-result",headers=headers).json()==r
        h=c.get("/api/sessions/"+owner["session_id"]+"/messages",headers=headers).json()
        assert h["messages"][0]["response"]==r
        assert c.get("/api/requests/test-result",headers={"X-Session-Token":other["session_token"]}).status_code==403
