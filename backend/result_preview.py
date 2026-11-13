"""Explicit synthetic examples, separate from conversations and real source data."""
from pathlib import Path
import hashlib,json,sqlite3,tempfile,time
from copy import deepcopy
from contextlib import closing
from .dialogue import ContextUpdate,empty_context,transition
from .query_plan import compile_plan,execute_readonly
from .results import build_result

def make_previews(registry):
    source=[
        ("P01","2011-10-02",1,8000000,8000000,"Alpha","X"),
        ("P02","2011-11-01",2,1250000,2500000,"Alpha","X"),
        ("P02","2011-11-01",1,5000,5000,"Alpha","Y"),
        ("P03","2011-11-02",1,3500000,3500000,"Beta","X"),
        ("P04","2011-11-04",2,2000000,4000000,"Alpha","Z"),
        ("c05","2011-11-04",-1,4000000,-4000000,"Beta","Z"),
        ("P06","2011-11-04",1,0,0,"Beta","Y"),
    ]
    # Separate large-cardinality scenario to exercise the real 1001-row limit.
    source.extend((f"L{i}","2011-09-01",1,1000000,1000000,f"Group{i:04d}","L") for i in range(1002))
    reg=deepcopy(registry)
    reg["source_sha256"]=hashlib.sha256(json.dumps(source,ensure_ascii=False).encode()).hexdigest()
    coverage={"start":"2011-01-01T00:00:00","end":"2011-12-31T23:59:59"}
    scenarios=[
        ("trend","每日金额",{"group_by":"date"}),
        ("country","客户国家",{"group_by":"Country"}),
        ("ratio","月环比",{"metric_id":"M08"}),
        ("empty","无匹配记录",{"country":"NoSuchCountry"}),
        ("missing_month","环比缺少前月",{"metric_id":"M08","country":"Beta"}),
        ("invoices","发票计数",{"metric_id":"M02"}),
        ("truncated","超出展示上限",{"start":"2011-09-01","end_exclusive":"2011-10-01","group_by":"Country"}),
    ]
    results=[]
    with tempfile.TemporaryDirectory(prefix="chatbi-result-preview-") as folder:
        path=Path(folder)/"fixture.sqlite"
        with closing(sqlite3.connect(path)) as db:
            db.execute("CREATE TABLE retail(InvoiceNo TEXT,InvoiceDate TEXT,Quantity INTEGER,price_micros INTEGER,amount_micros INTEGER,Country TEXT,StockCode TEXT)")
            db.executemany("INSERT INTO retail VALUES(?,?,?,?,?,?,?)",source)
            db.commit()
        for name,title,changes in scenarios:
            ctx,_=transition(empty_context(),ContextUpdate(expected_revision=0,confirm=True,changes={"metric_id":"M01","start":"2011-11-01","end_exclusive":"2011-12-01",**changes}),reg,coverage)
            plan=compile_plan(ctx);start=time.perf_counter();raw=execute_readonly(path,plan)
            result=build_result(raw,plan,ctx,reg,request_id="preview-"+name,dataset_name="I-04合成组件数据",source_kind="synthetic",elapsed_ms=(time.perf_counter()-start)*1000)
            results.append({"id":name,"title":title,"result":result})
    return {"mode":"synthetic_component_preview","model_calls":0,"items":results}
