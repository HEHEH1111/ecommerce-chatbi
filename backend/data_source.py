"""Verified application warehouse. Runtime never imports audit or evaluation data."""
from pathlib import Path
from contextlib import closing
import hashlib,json,sqlite3,time
from .query_plan import compile_plan,execute_readonly
from .results import build_result,ResultValidationError

class DataSourceFailure(Exception):
    def __init__(self,code):self.code=code;super().__init__(code)

def file_hash(path):
    with path.open("rb") as stream:return hashlib.file_digest(stream,"sha256").hexdigest()

class PublicDataSource:
    def __init__(self,root):
        self.root=Path(root);self.path=self.root/"data/warehouse/online-retail.sqlite"
        self.manifest_path=self.path.parent/"manifest.json";self.status="not_imported";self.manifest={};self.signature=None
        try:
            if not self.path.is_file() or not self.manifest_path.is_file():return
            manifest=json.loads(self.manifest_path.read_text(encoding="utf-8"))
            registry=json.loads((self.root/"resources/metrics.v0.2.json").read_text(encoding="utf-8"))
            profile=json.loads((self.root/"evidence/data-profile/profile.json").read_text(encoding="utf-8"))
            if manifest["source_sha256"]!=registry["source_sha256"] or manifest["metric_version"]!=registry["version"] or manifest["schema_version"]!="1":raise ValueError()
            if manifest["row_count"]!=profile["rows"] or file_hash(self.path)!=manifest["database_sha256"]:raise ValueError()
            if manifest["date_min"]!=profile["date_min"] or manifest["date_max"]!=profile["date_max"]:raise ValueError()
            with closing(sqlite3.connect(self.path.resolve().as_uri()+"?mode=ro",uri=True)) as db:
                if db.execute("PRAGMA quick_check").fetchone()[0]!="ok":raise ValueError()
                meta=dict(db.execute("SELECT key,value FROM metadata"))
                for key in ("schema_version","source_sha256","canonical_rows_sha256","row_count"):
                    if meta[key]!=str(manifest[key]):raise ValueError()
                count,low,high=db.execute("SELECT COUNT(*),MIN(source_row),MAX(source_row) FROM retail").fetchone()
                if count!=manifest["row_count"] or low!=2 or high!=count+1:raise ValueError()
            self.manifest=manifest;self.signature=self._signature();self.status="connected_readonly"
        except (OSError,ValueError,KeyError,TypeError,sqlite3.Error):self.status="verification_failed"

    def _signature(self):
        a=self.path.stat();b=self.manifest_path.stat()
        return (a.st_size,a.st_mtime_ns,a.st_ino,b.st_size,b.st_mtime_ns)
    @property
    def ready(self):
        if self.status!="connected_readonly":return False
        try:
            if self._signature()!=self.signature:self.status="source_changed"
        except OSError:self.status="source_missing"
        return self.status=="connected_readonly"

    def run(self,context,registry,request_id):
        start=time.perf_counter()
        try:
            if not self.ready:raise DataSourceFailure("DATA_SOURCE_NOT_READY")
            if context["source_sha256"]!=self.manifest["source_sha256"]:raise DataSourceFailure("RESOURCE_CONFLICT")
            plan=compile_plan(context);raw=execute_readonly(self.path,plan)
            if not self.ready:raise DataSourceFailure("DATA_SOURCE_CHANGED")
            result=build_result(raw,plan,context,registry,request_id=request_id,dataset_name="UCI Online Retail",
                                source_kind="public_dataset",elapsed_ms=(time.perf_counter()-start)*1000)
            result["evidence"].update(database_sha256=self.manifest["database_sha256"],schema_version=self.manifest["schema_version"],
                                      source_rows=self.manifest["row_count"])
            return result
        except DataSourceFailure as e:code=e.code
        except ResultValidationError:code="RESULT_VALIDATION_FAILED"
        except sqlite3.Error as e:code="QUERY_TIMEOUT" if getattr(e,"sqlite_errorcode",None)==sqlite3.SQLITE_INTERRUPT else "DATA_SOURCE_ERROR"
        except (OSError,ValueError,KeyError):code="DATA_SOURCE_ERROR"
        messages={"DATA_SOURCE_NOT_READY":"正式数据未就绪或已变化，请检查数据接入并重启工作台。",
                  "DATA_SOURCE_CHANGED":"数据在查询期间发生变化，请重启并重新核对数据。",
                  "RESOURCE_CONFLICT":"当前口径与数据快照不一致，未执行查询。",
                  "RESULT_VALIDATION_FAILED":"查询结果未通过校验，当前不展示数值。",
                  "QUERY_TIMEOUT":"数据查询超过时间限制，请缩小查询范围后重试。",
                  "DATA_SOURCE_ERROR":"数据查询未完成，请检查本地数据文件后重试。"}
        return {"request_id":request_id,"status":"error","data":None,
                "error":{"code":code,"message":messages[code],"retryable":True},
                "evidence":{"execution":"failed","metric_version":registry["version"],"source_sha256":context["source_sha256"]}}
