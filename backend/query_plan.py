"""Whitelist-only SQL compilation and read-only fixture execution. Not a production data connection."""
from dataclasses import dataclass
from datetime import date,timedelta
from pathlib import Path
import sqlite3,time

@dataclass(frozen=True)
class Compiled:
    sql:str
    parameters:tuple
    metric_id:str
    kind:str

def compile_plan(context):
    if not context["confirmed"] or context["pending"]:raise ValueError("UNCONFIRMED_PLAN")
    c=context["conditions"];metric=c["metric_id"]
    if metric not in {"M01","M02","M08"}:raise ValueError("UNKNOWN_METRIC")
    dims={None:None,"date":"substr(InvoiceDate,1,10)","month":"substr(InvoiceDate,1,7)","Country":"Country","StockCode":"StockCode"}
    if c["group_by"] not in dims:raise ValueError("UNKNOWN_DIMENSION")
    start=c["start"];end=c["end_exclusive"];kind="aggregate"
    if metric=="M08":
        first=date.fromisoformat(start)
        if first.day!=1 or c["group_by"] is not None:raise ValueError("INVALID_RATIO")
        start=str((first-timedelta(days=1)).replace(day=1));dim=dims["month"];kind="monthly_components"
    else:dim=dims[c["group_by"]]
    params=[start,end]
    where="InvoiceDate >= ? AND InvoiceDate < ? AND upper(substr(InvoiceNo,1,1)) <> 'C' AND Quantity > 0 AND price_micros > 0"
    if c["country"] is not None:where+=" AND Country = ?";params.append(c["country"])
    amount="COUNT(DISTINCT InvoiceNo)" if metric=="M02" else "SUM(amount_micros)"
    sql="SELECT "+(dim+" AS dimension, " if dim else "")+amount+" AS value FROM retail WHERE "+where
    if dim:sql+=" GROUP BY "+dim+" ORDER BY dimension COLLATE BINARY ASC"
    sql+=" LIMIT 1001"
    return Compiled(sql,tuple(params),metric,kind)

def execute_readonly(path:Path,plan:Compiled,seconds=5):
    deadline=time.monotonic()+seconds
    con=sqlite3.connect(path.resolve().as_uri()+"?mode=ro",uri=True)
    try:
        con.enable_load_extension(False);con.execute("PRAGMA query_only=ON")
        def allow(action,arg1,arg2,db,source):
            if action==sqlite3.SQLITE_SELECT:return sqlite3.SQLITE_OK
            if action==sqlite3.SQLITE_READ and arg1=="retail" and db=="main":return sqlite3.SQLITE_OK
            if action==sqlite3.SQLITE_FUNCTION and (arg2 or "").lower() in {"sum","count","upper","substr"}:return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY
        con.set_authorizer(allow)
        con.set_progress_handler(lambda:int(time.monotonic()>deadline),100)
        cursor=con.execute(plan.sql,plan.parameters);rows=cursor.fetchmany(1001)
        return {"columns":[d[0] for d in cursor.description],"rows":rows[:1000],"truncated":len(rows)>1000}
    finally:con.close()
