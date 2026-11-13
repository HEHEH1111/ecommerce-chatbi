"""Asynchronous language planning; commits only against the reserved context revision."""
import json,uuid
from .store import now
from .dialogue import ContextUpdate,transition,empty_context,local_country_turn
from .model_client import ModelFailure
from .query_plan import compile_plan

ERRORS={
 "RESOURCE_CONFLICT":"业务资源版本或内容不一致，已停止解析，请检查资源后重试。",
 "MODEL_AUTH_FAILED":"模型凭据未通过验证，请检查本机API Key。",
 "MODEL_PERMISSION_DENIED":"账号暂不能调用该模型，请检查模型权限或服务状态。",
 "MODEL_RATE_LIMITED":"模型调用被限流，请稍后重试。",
 "MODEL_TIMEOUT":"模型响应超时，未修改已保存条件。",
 "MODEL_UNAVAILABLE":"模型服务暂不可用，请检查账号服务或网络。",
 "MODEL_RESPONSE_INVALID":"模型未返回有效的条件结构，请重试或使用手动条件。",
 "PLAN_INVALID":"模型给出的条件未通过校验，请使用手动条件。",
 "CONTEXT_CONFLICT":"会话条件已变化，本次解析未覆盖新条件。"
}
def reserve(store,sid,client_id,question):
    payload=json.dumps({"question":question,"context_update":None},sort_keys=True,ensure_ascii=False)
    with store.connect() as con:
        con.execute("BEGIN IMMEDIATE")
        old=con.execute("""SELECT r.id,p.payload FROM requests r LEFT JOIN dialogue_payload p ON p.request_id=r.id
                           WHERE r.session_id=? AND r.client_message_id=?""",(sid,client_id)).fetchone()
        if old:
            if old["payload"]!=payload:raise ValueError("IDEMPOTENCY_CONFLICT")
            return old["id"],False,None
        if con.execute("SELECT 1 FROM requests WHERE session_id=? AND status IN ('queued','processing')",(sid,)).fetchone():
            raise ValueError("SESSION_BUSY")
        row=con.execute("SELECT state FROM dialogue_context WHERE session_id=?",(sid,)).fetchone()
        context=json.loads(row["state"]) if row else empty_context()
        rid=str(uuid.uuid4())
        con.execute("""INSERT INTO requests(id,session_id,client_message_id,question,status,created_at)
                       VALUES(?,?,?,?,'processing',?)""",(rid,sid,client_id,question,now()))
        con.execute("INSERT INTO dialogue_payload VALUES(?,?)",(rid,payload))
    return rid,True,context

def add_compilation(response,context):
    if not context["confirmed"]:return
    plan=compile_plan(context)
    response.pop("error",None)
    response.update({"status":"plan_ready","data":None,"message":"条件与查询计划已就绪，正式数据连接尚未接入。",
                     "plan":{"metric_id":plan.metric_id,"kind":plan.kind,"sql":plan.sql,"parameters":list(plan.parameters)},
                     "next_step":"后续接入正式数据后，才能执行查询并展示结果。"})

def model_turn(question,previous,client,registry,coverage):
    context=None
    decision,model_evidence=client.interpret(question,previous,registry,coverage)
    if decision.action=="unsupported":
        messages={"write_operation":"当前仅支持已声明范围的只读分析，不能修改或删除数据。",
                  "missing_data":"当前数据缺少所需字段，不能据此计算该指标。",
                  "unsupported_scope":"问题超出当前可支持的分析范围，请调整问题。","none":"当前无法按该要求生成查询计划。"}
        response={"status":"unsupported","reason_code":decision.reason,"message":messages[decision.reason],"data":None,
                  "evidence":{"execution":"not_executed",**model_evidence}}
    else:
        update=ContextUpdate(expected_revision=previous["revision"],changes=decision.changes,reset=decision.reset,confirm=False)
        context,response=transition(previous,update,registry,coverage)
        context["interpretation"]="model_proposed"
        for i,q in enumerate(decision.questions):
            context["pending"].append({"field":"model_question_"+str(i),"message":q})
        if decision.action=="clarify" and not decision.questions:
            context["pending"].append({"field":"model_question","message":"请补充更明确的问题条件，并核对所选指标。"})
        response["clarification"]={"fields":context["pending"]}
        response["message"]="AI已整理候选条件。请检查指标、日期和范围，再确认本轮条件。"
        response["evidence"].update(model_evidence)
        response["evidence"]["interpretation"]="model_proposed"
    return context,response

def process(store,sid,rid,question,previous,client,registry,coverage):
    context=None
    try:
        local = local_country_turn(previous,question,registry,coverage)
        context,response = local or model_turn(question,previous,client,registry,coverage)
        response["evidence"].update({"metric_version":registry["version"],"source_sha256":registry["source_sha256"]})
    except ModelFailure as exc:
        response={"status":"error","data":None,"error":{"code":exc.code,"message":ERRORS.get(exc.code,ERRORS["PLAN_INVALID"]),"retryable":True},
                  "evidence":{"execution":"not_executed"}}
        if exc.category:
            response["evidence"]["validation_category"] = exc.category
    except Exception:
        # Never expose upstream bodies, configuration, or exception text to the client.
        response={"status":"error","data":None,"error":{"code":"PLAN_INVALID","message":ERRORS["PLAN_INVALID"],"retryable":False},
                  "evidence":{"execution":"not_executed"}}
    with store.connect() as con:
        con.execute("BEGIN IMMEDIATE")
        row=con.execute("SELECT state FROM dialogue_context WHERE session_id=?",(sid,)).fetchone()
        current=json.loads(row["state"]) if row else empty_context()
        request=con.execute("SELECT status FROM requests WHERE id=?",(rid,)).fetchone()
        if not request or request["status"]!="processing":return
        if current["revision"]!=previous["revision"]:
            context=None
            response={"status":"error","data":None,"error":{"code":"CONTEXT_CONFLICT","message":ERRORS["CONTEXT_CONFLICT"],"retryable":True}}
        if context:
            context["last_request_id"]=rid
            con.execute("INSERT INTO dialogue_context VALUES(?,?) ON CONFLICT(session_id) DO UPDATE SET state=excluded.state",
                        (sid,json.dumps(context,ensure_ascii=False)))
        response.update({"request_id":rid,"parent_request_id":previous.get("last_request_id")})
        con.execute("UPDATE requests SET status=?,response=? WHERE id=?",(response["status"],json.dumps(response,ensure_ascii=False),rid))
