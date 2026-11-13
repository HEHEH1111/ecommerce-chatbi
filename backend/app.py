"""I-01 local service: resource metadata, durable sessions and explicit readiness."""
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator
from .store import Store
from .data_source import PublicDataSource
from .model_client import load_settings,ModelClient,ModelFailure
from .planning_service import reserve,process
from .dialogue import ContextUpdate
from .result_preview import make_previews
from .results import ResultValidationError
import sqlite3
from .dialogue_store import install, read_context, submit_turn

ROOT = Path(__file__).resolve().parents[1]
ALLOWED_ORIGINS = {"http://127.0.0.1:8765","http://localhost:8765",
                   "http://127.0.0.1:5173","http://localhost:5173"}
DATASET_ID = "uci_online_retail_352"

class SessionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset_id: str = DATASET_ID

class MessageInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1,max_length=2000)
    client_message_id: str = Field(min_length=1,max_length=100,pattern=r"^[a-zA-Z0-9_-]+$")

    context_update: ContextUpdate | None = None

    @field_validator("question")
    @classmethod
    def nonempty(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("EMPTY_QUESTION")
        return value

def create_app(root: Path = ROOT, state_path: Path | None = None, model_client=None, load_model=False, data_source=None, load_data=False):
    if load_data:data_source=PublicDataSource(root)
    config_status="not_connected"
    if load_model:
        try:
            settings=load_settings(root)
            model_client=ModelClient(settings) if settings else None
        except (ModelFailure,OSError,UnicodeError):
            config_status="MODEL_CONFIG_INVALID"
    store = Store(state_path or root/"data/runtime/conversations.sqlite")
    @asynccontextmanager
    async def lifespan(app):
        store.initialize()
        install(store)
        yield

    app = FastAPI(title="ChatBI local workbench", version="0.9.0", lifespan=lifespan,
                  docs_url="/api/docs",openapi_url="/api/openapi.json",redoc_url=None)
    app.state.store = store
    app.add_middleware(CORSMiddleware,allow_origins=list(ALLOWED_ORIGINS),
                       allow_methods=["GET","POST"],allow_headers=["Content-Type","X-Session-Token"])

    @app.middleware("http")
    async def local_boundary(request: Request, call_next):
        host = (request.headers.get("host") or "").split(":")[0].lower()
        if host not in {"127.0.0.1","localhost","testserver"}:
            return JSONResponse({"error":{"code":"INVALID_HOST","message":"仅支持本地访问。"}},400)
        origin = request.headers.get("origin")
        if origin and origin not in ALLOWED_ORIGINS:
            return JSONResponse({"error":{"code":"INVALID_ORIGIN","message":"请求来源未被允许。"}},403)
        if request.method == "POST" and request.url.path.startswith("/api/"):
            body = bytearray()
            async for chunk in request.stream():
                if len(body) + len(chunk) > 65536:
                    return JSONResponse({"error":{"code":"BODY_TOO_LARGE","message":"请求内容过长。"}},413)
                body.extend(chunk)
            request._body = bytes(body)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return JSONResponse({"error":{"code":"INVALID_INPUT",
            "message":"请检查输入：问题为1–2000字，日期、指标和条件版本必须有效。"}},422)

    def resource(name):
        try:
            return json.loads((root/name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise HTTPException(503,"项目资料无法读取，请检查本地文件。")

    def verify(sid, token):
        if not store.authorized(sid, token):
            raise HTTPException(403,"无法访问此会话，请重新创建会话。")

    @app.get("/api/health")
    def health():
        ready=bool(data_source and data_source.ready)
        resources_present = (root/"resources/metrics.v0.2.json").is_file()
        source_present = (root/"data/raw/Online Retail.xlsx").is_file()
        return {"status":"ready" if ready else "setup_required","app_version":"0.9.0","phase":"I-07",
                "service":"online","sessions":"persistent",
                "resources":{"status":"available" if resources_present else "missing","version":"0.2"},
                "data":{"status":data_source.status if data_source else ("profiled_not_connected" if source_present else "source_missing"),
                        "query_available":ready},
                "model":{"status":model_client.last_status if model_client else config_status,"configured":model_client is not None,"query_available":ready and model_client is not None},
                "capabilities":{"sessions":True,"data_catalog":True,"metrics":True,
                                "natural_language_query":ready and model_client is not None,"charts":ready,"result_preview":True,"manual_clarification":True,"plan_generation":model_client is not None}}

    @app.get("/api/datasets")
    def datasets():
        ready=bool(data_source and data_source.ready)
        manifest = resource("evidence/data-profile/source-manifest.json")
        profile = resource("evidence/data-profile/profile.json")
        return {"datasets":[{"id":DATASET_ID,"name":"UCI Online Retail","category":"电商交易 · 公开数据",
            "rows":profile["rows"],"columns":len(profile["columns"]),"currency":"GBP",
            "start":profile["date_min"],"end":profile["date_max"],"source_sha256":manifest["source_sha256"],
            "source_url":"https://archive.ics.uci.edu/dataset/352/online+retail","license":"CC BY 4.0",
            "status":data_source.status if data_source else "profiled_not_connected","query_available":ready,
            "limitations":["历史数据截至2011年12月9日；12月不是完整月份。",
                           "含费用、调整和重复记录；不能直接等同支付GMV。",
                           "缺少支付状态、成本和访客数据，不能计算利润或转化率。"],
            "quality":{"missing_customer":profile["missing"]["CustomerID"]["count"],
                       "duplicate_extra":profile["exact_duplicates_extra"],
                       "negative_quantity":profile["negative_quantity_rows"]}}]}

    @app.get("/api/metrics")
    def metrics():
        data = resource("resources/metrics.v0.2.json")
        return {**data,"query_available":bool(data_source and data_source.ready),"implementation_status":"query_enabled" if data_source and data_source.ready else "definitions_only"}

    @app.get("/api/result-previews")
    def result_previews():
        try:
            return make_previews(resource("resources/metrics.v0.2.json"))
        except (ResultValidationError,sqlite3.Error):
            return JSONResponse({"error":{"code":"RESULT_PREVIEW_FAILED","message":"结果预览生成失败，请重试。"}},503)

    @app.post("/api/sessions",status_code=201)
    def new_session(body:SessionInput):
        if body.dataset_id != DATASET_ID:
            raise HTTPException(404,"未找到该数据集。")
        return store.create_session(body.dataset_id)

    @app.post("/api/sessions/{sid}/messages",status_code=202)
    def submit(sid:str,body:MessageInput,bg:BackgroundTasks,
               x_session_token: Annotated[str | None,Header()] = None):
        verify(sid,x_session_token)
        registry=resource("resources/metrics.v0.2.json")
        profile=resource("evidence/data-profile/profile.json")
        coverage={"start":profile["date_min"],"end":profile["date_max"]}
        try:
            if model_client is not None and body.context_update is None:
                rid,created,previous=reserve(store,sid,body.client_message_id,body.question)
                if created:bg.add_task(process,store,sid,rid,body.question,previous,model_client,registry,coverage)
            else:
                rid,created=submit_turn(store,sid,body.client_message_id,body.question,body.context_update,registry,coverage,compiled=model_client is not None or data_source is not None,data_source=data_source)
        except ValueError as exc:
            messages={"CONTEXT_CONFLICT":"条件已在其他窗口更新，请重新打开本会话。",
                      "SESSION_BUSY":"本会话正在解析上一条问题，请稍后再提交。"}
            raise HTTPException(409,messages.get(str(exc),"消息编号不能用于不同的问题或条件。"))
        return {"request_id":rid,"status":store.request(rid)["status"],"created":created}

    @app.get("/api/sessions/{sid}/context")
    def get_context(sid:str,x_session_token: Annotated[str | None,Header()] = None):
        verify(sid,x_session_token)
        return read_context(store,sid)

    @app.get("/api/requests/{rid}")
    def get_request(rid:str,x_session_token: Annotated[str | None,Header()] = None):
        row = store.request(rid)
        if not row:
            raise HTTPException(404,"未找到请求。")
        verify(row["session_id"],x_session_token)
        return (json.loads(row["response"]) if row["response"] else
                {"request_id":rid,"status":row["status"],"data":None})

    @app.get("/api/sessions/{sid}/messages")
    def get_messages(sid:str,x_session_token: Annotated[str | None,Header()] = None):
        verify(sid,x_session_token)
        return {"messages":store.messages(sid),"context":read_context(store,sid)}

    # Mount only compiled UI assets, never project data, eval answers, or docs.
    dist = root/"frontend/dist"
    if dist.is_dir():
        app.mount("/",StaticFiles(directory=dist,html=True),name="frontend")
    return app

app = create_app(load_model=True,load_data=True)
