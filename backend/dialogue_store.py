"""Transactional dialogue updates, with revision checks and immutable request snapshots."""
import json
import uuid
from .store import now
from .dialogue import empty_context, transition, local_country_turn

def install(store):
    with store.connect() as con:
        con.executescript("""
          CREATE TABLE IF NOT EXISTS dialogue_context (
            session_id TEXT PRIMARY KEY REFERENCES sessions(id), state TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS dialogue_payload (
            request_id TEXT PRIMARY KEY REFERENCES requests(id), payload TEXT NOT NULL);
        """)

def read_context(store,sid):
    with store.connect() as con:
        row=con.execute("SELECT state FROM dialogue_context WHERE session_id=?",(sid,)).fetchone()
    return json.loads(row["state"]) if row else empty_context()

def submit_turn(store,sid,client_id,question,update,registry,coverage,compiled=False,data_source=None):
    payload=json.dumps({"question":question,"context_update":update.model_dump(mode="json",exclude_unset=True) if update else None},sort_keys=True,ensure_ascii=False)
    with store.connect() as con:
        con.execute("BEGIN IMMEDIATE")
        old=con.execute("""SELECT r.id,r.question,p.payload FROM requests r LEFT JOIN dialogue_payload p ON p.request_id=r.id
                           WHERE r.session_id=? AND r.client_message_id=?""",(sid,client_id)).fetchone()
        if old:
            if old["payload"]!=payload and not (old["payload"] is None and update is None and old["question"]==question):
                raise ValueError("IDEMPOTENCY_CONFLICT")
            return old["id"],False
        if con.execute("SELECT 1 FROM requests WHERE session_id=? AND status IN ('queued','processing')",(sid,)).fetchone():
            raise ValueError("SESSION_BUSY")
        row=con.execute("SELECT state FROM dialogue_context WHERE session_id=?",(sid,)).fetchone()
        previous=json.loads(row["state"]) if row else empty_context()
        if update and update.expected_revision!=previous["revision"]:
            raise ValueError("CONTEXT_CONFLICT")
        rid=str(uuid.uuid4())
        parent=con.execute("SELECT id FROM requests WHERE session_id=? ORDER BY seq DESC LIMIT 1",(sid,)).fetchone()
        local = local_country_turn(previous,question,registry,coverage) if update is None else None
        state,response=local or transition(previous,update,registry,coverage)
        if compiled:
            from .planning_service import add_compilation
            add_compilation(response,state)
        state["last_request_id"]=rid
        if data_source is not None and state["confirmed"]:
            from .retrieval import Retriever,ResourceConflict
            try:
                retriever=Retriever(registry)
                metric=next(m for m in registry["metrics"] if m["id"]==state["conditions"]["metric_id"])
                trace=retriever.search(metric["name"],state)
                response=data_source.run(state,registry,rid)
                response["evidence"]["retrieval"]=dict(trace,scope="confirmed_conditions")
            except ResourceConflict:
                response={"request_id":rid,"status":"error","data":None,"error":{"code":"RESOURCE_CONFLICT","message":"业务资源版本不一致，未执行查询。","retryable":False},"evidence":{"execution":"not_executed","metric_version":registry["version"]}}
            state["last_execution"]={"status":response["status"],"execution":response["evidence"]["execution"]}
            response["context"]=state
        response["parent_request_id"]=parent["id"] if parent else None
        state["last_request_id"]=rid
        response["request_id"]=rid
        con.execute("""INSERT INTO requests(id,session_id,client_message_id,question,status,response,created_at)
                       VALUES(?,?,?,?,?,?,?)""",(rid,sid,client_id,question,response["status"],json.dumps(response,ensure_ascii=False),now()))
        con.execute("INSERT INTO dialogue_payload VALUES(?,?)",(rid,payload))
        con.execute("INSERT INTO dialogue_context VALUES(?,?) ON CONFLICT(session_id) DO UPDATE SET state=excluded.state",
                    (sid,json.dumps(state,ensure_ascii=False)))
    return rid,True
