"""Validate executed aggregates and build one canonical table/chart result."""
from datetime import date,timedelta,datetime,timezone
from decimal import Decimal,ROUND_HALF_UP,localcontext
import math,re
from copy import deepcopy
from .query_plan import compile_plan

class ResultValidationError(ValueError):
    def __init__(self):super().__init__("RESULT_VALIDATION_FAILED")

def exact_amount(micros):
    with localcontext() as ctx:
        ctx.prec=50
        return format(Decimal(micros)/Decimal(1000000),"f")

def display_number(value,places=2):
    with localcontext() as ctx:
        ctx.prec=50
        return format(Decimal(value).quantize(Decimal(1).scaleb(-places),rounding=ROUND_HALF_UP),f",.{places}f")

def build_result(raw,plan,context,registry,*,request_id,dataset_name,source_kind,elapsed_ms):
    """Called only with executor output, never model-generated numeric answers."""
    try:
        return _build(raw,plan,deepcopy(context),registry,request_id,dataset_name,source_kind,elapsed_ms)
    except (ValueError,TypeError,KeyError,IndexError,ArithmeticError,AttributeError,StopIteration):
        raise ResultValidationError() from None

def _build(raw,plan,context,registry,request_id,dataset_name,source_kind,elapsed_ms):
    if source_kind not in {"synthetic","public_dataset"}:raise ValueError()
    if not request_id or type(elapsed_ms) not in (float,int) or not math.isfinite(elapsed_ms) or elapsed_ms<0:raise ValueError()
    if compile_plan(context)!=plan:raise ValueError()
    if registry["version"]!=context["metric_version"] or registry["source_sha256"]!=context["source_sha256"]:raise ValueError()
    if not re.fullmatch(r"[0-9a-f]{64}",context["source_sha256"]):raise ValueError()
    metric=next(m for m in registry["metrics"] if m["id"]==plan.metric_id and m["enabled"])
    grouped=context["conditions"]["group_by"] is not None or plan.kind=="monthly_components"
    expected=["dimension","value"] if grouped else ["value"]
    if raw["columns"]!=expected or type(raw["truncated"]) is not bool or not isinstance(raw["rows"],list) or len(raw["rows"])>1000:raise ValueError()
    if not grouped and (len(raw["rows"])!=1 or raw["truncated"]):raise ValueError()
    rows=[];seen=set()
    for values in raw["rows"]:
        if not isinstance(values,(list,tuple)) or len(values)!=len(expected):raise ValueError()
        value=values[-1]
        if value is not None and (type(value) is not int or value<0 or value>9223372036854775807):raise ValueError()
        dimension=values[0] if grouped else metric["name"]
        if not isinstance(dimension,str) or not dimension or len(dimension)>256 or dimension in seen:raise ValueError()
        seen.add(dimension)
        if grouped and value is None:raise ValueError()
        if plan.kind=="monthly_components":
            if not re.fullmatch(r"\d{4}-\d{2}",dimension):raise ValueError()
        if context["conditions"]["group_by"] in {"date","month"}:
            parsed=date.fromisoformat(dimension+("-01" if context["conditions"]["group_by"]=="month" else ""))
            if str(parsed)[:len(dimension)]!=dimension:raise ValueError()
        text=None if value is None else (str(value) if plan.metric_id=="M02" else exact_amount(value))
        rows.append({"dimension":dimension,"value":text,"display":"—" if text is None else display_number(text,0 if plan.metric_id=="M02" else 2)})
    c=context["conditions"];unit="张" if plan.metric_id=="M02" else "GBP"
    warnings=[]
    if plan.metric_id in {"M01","M08"}:warnings.append("保留费用、调整及原始重复记录；不是支付GMV或纯商品收入。")
    else:warnings.append("按合格记录中的发票编号去重；不是交易行数或已支付订单数。")
    if c["accept_partial"]:warnings.append("用户已接受部分覆盖；结果不代表完整请求期间。")
    if raw["truncated"]:warnings.append("结果超过1000行，仅展示按分组键排序的前1000行；不代表完整结果或全量排名。")
    if c["group_by"] in {"date","month"}:warnings.append("仅展示有匹配记录的日期，未返回日期不补零。")
    if c["group_by"] and plan.metric_id=="M02":warnings.append("各组发票数不能直接相加当作全量去重发票数。")
    status="success";summary=None;ratio=None
    if plan.kind=="monthly_components":
        if raw["truncated"]:raise ValueError()
        target=c["start"][:7];previous=str((date.fromisoformat(c["start"])-timedelta(days=1)).replace(day=1))[:7]
        amounts={v[0]:v[1] for v in raw["rows"]}
        if set(amounts)-{previous,target}:raise ValueError()
        a=amounts.get(previous);b=amounts.get(target)
        ratio={"previous_month":previous,"target_month":target,"numerator_micros":str(b-a) if a is not None and b is not None else None,"denominator_micros":str(a) if a is not None else None}
        if a is None or b is None:
            status="not_computable";message="目标月或前月没有匹配记录，不能把缺失月份当成零计算环比。"
        elif a==0:
            status="not_computable";message="前月金额为零，环比不可计算。"
        else:
            with localcontext() as ctx:
                ctx.prec=50
                percentage=Decimal(b-a)*100/Decimal(a)
                value=format(percentage.quantize(Decimal("0.000001"),rounding=ROUND_HALF_UP),"f")
            summary={"label":metric["name"],"value":value,"display":display_number(percentage)+"%","unit":"%"}
            message="已按完整目标月与前月的金额计算环比。"
        if status!="success":summary={"label":metric["name"],"value":None,"display":"不可计算","unit":"%"}
    elif not rows or (not grouped and (rows[0]["value"] is None or (plan.metric_id=="M02" and rows[0]["value"]=="0"))):
        status="no_data";rows=[];message="已查询所选范围，没有符合条件的记录。请调整日期或筛选条件。"
    elif not grouped:
        summary={"label":metric["name"],"value":rows[0]["value"],"display":rows[0]["display"],"unit":unit};message="结果已计算，口径与查询依据见下方。"
    else:message=f"已返回{len(rows)}组记录；图表与表格使用同一份结果。"
    chart=None
    if rows and grouped:
        chart={"kind":"line" if c["group_by"] in {"date","month"} else "bar","x_key":"dimension","y_key":"value"}
    label={"date":"日期","month":"月份","Country":"客户国家","StockCode":"交易代码"}.get(c["group_by"],"月份" if plan.kind=="monthly_components" else "指标")
    return {"request_id":request_id,"status":status,"message":message,"synthetic":source_kind=="synthetic",
        "data":{"columns":[{"key":"dimension","label":label,"type":"text"},{"key":"value","label":"正向交易记录金额" if plan.metric_id=="M08" else metric["name"],"type":"decimal","unit":unit}],
                "rows":rows,"chart":chart,"summary":summary,"ratio":ratio,"truncated":raw["truncated"]},
        "warnings":warnings,"context":context,
        "evidence":{"execution":"fixture_query" if source_kind=="synthetic" else "query","source_kind":source_kind,
            "dataset_name":dataset_name,"dataset_sha256":context["source_sha256"],"metric_id":plan.metric_id,"metric_version":context["metric_version"],
            "conditions":c,"effective_range":context.get("effective_range"),"sql":plan.sql,"parameters":list(plan.parameters),
            "elapsed_ms":round(elapsed_ms,3),"returned_rows":len(rows),"truncated":raw["truncated"],
            "generated_at":datetime.now(timezone.utc).isoformat()}}
