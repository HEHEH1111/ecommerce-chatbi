"""Dialogue state for explicit user-confirmed conditions; no language model or SQL."""
from copy import deepcopy
import re
from datetime import date, datetime, timedelta, timezone
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator

class Conditions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    metric_id: Literal["M01","M02","M08"] | None = None
    start: date | None = None
    end_exclusive: date | None = None
    country: str | None = Field(default=None,max_length=80)
    group_by: Literal["date","month","Country","StockCode"] | None = None
    accept_partial: bool = False

    @field_validator("start","end_exclusive",mode="before")
    @classmethod
    def date_only(cls,value):
        if value is not None and (not isinstance(value,str) or len(value)!=10):
            raise ValueError("Use YYYY-MM-DD dates")
        return value

class ContextUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0,strict=True)
    changes: Conditions = Field(default_factory=Conditions)
    reset: bool = False
    confirm: bool = False


def empty_context():
    return {"revision":0,"conditions":Conditions().model_dump(mode="json"),
            "pending":[],"confirmed":False,"last_request_id":None}

def next_month(d):
    return date(d.year+(d.month==12),1 if d.month==12 else d.month+1,1)

# Only standalone references are handled locally. Compound requests need interpretation.
COUNTRY_REFERENCE = re.compile(
    r"(?:请)?(?:继续|还是|仍然)?(?:沿用|只看|看|查|用)?"
    r"(?:刚才|刚刚|之前|上次|前面|上一轮|上一个|那个|该|这个)"
    r"(?:说过|提到|选过|选择)?(?:的)?(?:那个|这个)?(?:客户)?国家(?:呢|吧)?"
)
COUNTRY_PROMPT = "你想查看哪个国家？请告诉我国家名称，或回复“国家不限”。"
UNRESTRICTED = {"不限", "国家不限", "全部国家", "所有国家"}


def waiting_for_country(state):
    return any(item["field"] == "country" for item in state.get("pending", []))


def local_country_turn(previous, question, registry, coverage):
    text = " ".join(question.strip().rstrip("？?。！!").split())
    country = previous["conditions"].get("country")
    if COUNTRY_REFERENCE.fullmatch(text):
        changes = {}
        message = f"沿用本会话的客户国家：{country}。" if country else COUNTRY_PROMPT
        require_country = not country
    elif waiting_for_country(previous):
        aliases = registry.get("country_aliases", {})
        known = {k.casefold(): v for k, v in aliases.items()}
        known.update({v.casefold(): v for v in aliases.values()})
        country = known.get(text.casefold())
        if not country and text not in UNRESTRICTED:
            return None
        changes = {"country": country or "国家不限"}
        message = f"已选择客户国家：{country}。" if country else "已选择全部国家。"
        require_country = False
    else:
        return None
    update = ContextUpdate(expected_revision=previous["revision"], changes=changes)
    state, response = transition(previous, update, registry, coverage, require_country=require_country)
    state["interpretation"] = "session_reference"
    response["evidence"]["interpretation"] = "session_reference"
    response["message"] = message
    return state, response


def transition(previous, update, registry, coverage, *, require_country=False):
    state=deepcopy(previous)
    conditions=Conditions().model_dump(mode="json") if update and update.reset else deepcopy(state["conditions"])
    changes=update.changes.model_dump(mode="json",exclude_unset=True) if update else {}
    dates_changed=any(k in changes and changes[k]!=conditions[k] for k in ("start","end_exclusive"))
    if dates_changed:
        conditions["accept_partial"]=False
    conditions.update(changes)
    if conditions["country"]:
        country=conditions["country"].strip()
        conditions["country"]=registry.get("country_aliases",{}).get(country,country) or None
    awaiting_country = require_country or (waiting_for_country(previous) and not (update and update.reset))
    unrestricted = conditions["country"] in UNRESTRICTED and awaiting_country
    if unrestricted:
        conditions["country"] = None
    fields=[]
    def missing(field,message):
        fields.append({"field":field,"message":message})
    if awaiting_country and not conditions["country"] and not unrestricted:
        missing("country", COUNTRY_PROMPT)
    enabled={m["id"] for m in registry["metrics"] if m["enabled"]}
    if not conditions["metric_id"]:
        missing("metric_id","请选择要看的指标；销售额、业绩或GMV不能自动替换为交易记录金额。")
    elif conditions["metric_id"] not in enabled:
        missing("metric_id","所选指标当前未启用，请重新选择。")
    if not conditions["start"] or not conditions["end_exclusive"]:
        missing("dates","请填写明确的起止日期。历史数据不会自动把“上个月”改成2011年。")
    start=date.fromisoformat(conditions["start"]) if conditions["start"] else None
    end=date.fromisoformat(conditions["end_exclusive"]) if conditions["end_exclusive"] else None
    low=date.fromisoformat(coverage["start"][:10])
    last=date.fromisoformat(coverage["end"][:10])
    high=last+timedelta(days=1)
    effective=None
    if start and end:
        if start>=end:
            missing("dates","结束日期不能早于开始日期。")
        elif end<=low or start>=high:
            missing("dates",f"所选日期完全超出数据覆盖：{low}至{last}，请修改日期。")
        else:
            effective={"start":str(max(start,low)),"end_exclusive":str(min(end,high)),
                       "source_last_timestamp":coverage["end"]}
            partial=start<low or end>last
            if conditions["metric_id"]=="M08":
                first=date(start.year,start.month,1)
                previous_month=(first-timedelta(days=1)).replace(day=1)
                if start!=first or end!=next_month(first) or partial or previous_month<low:
                    missing("dates","月环比需要目标月及前月均完整；请选择有完整覆盖及完整前月的一个目标月。")
                if conditions["group_by"] is not None:
                    missing("group_by","首版月环比仅支持总额，请将分组改为“不分组”。")
            elif partial and not conditions["accept_partial"]:
                missing("accept_partial",f"覆盖仅到{coverage['end']}。请确认只采用有覆盖的区间；不能视为完整月份。")
    confirmed=bool(update and update.confirm and not fields)
    if not fields and not confirmed:
        missing("confirmation","请核对本轮问题与下方条件，再点击“确认条件”。新问题不会自动沿用旧条件执行。")
    state.update({"revision":previous["revision"]+1,"conditions":conditions,
                  "pending":fields,"confirmed":confirmed,"effective_range":effective,
                  "metric_version":registry["version"],"source_sha256":registry["source_sha256"],
                  "updated_at":datetime.now(timezone(timedelta(hours=8))).isoformat(),
                  "interpretation":"explicit_form_only","last_execution":None})
    response={"data":None,"context":state,
              "evidence":{"execution":"not_executed","metric_version":registry["version"],
                          "source_sha256":registry["source_sha256"],"interpretation":"explicit_form_only"},
              "message":"请补充或确认本轮条件。当前使用手动条件确认，AI尚未解析问题。"}
    if fields:
        response.update({"status":"needs_clarification","clarification":{"fields":fields}})
    else:
        response.update({"status":"error",
                         "message":"条件已确认，等待接入AI与查询链路。",
                         "error":{"code":"MODEL_NOT_CONNECTED","message":"条件已保存。AI模型和查询链路尚未接入，当前不能返回分析结果。","retryable":False}})
    return state,response
