"""Import the immutable workbook into a separate, reproducible application database."""
from pathlib import Path
from contextlib import closing
from collections import Counter,defaultdict
from datetime import datetime,timezone
from decimal import Decimal
import hashlib,json,sqlite3,time,uuid
from openpyxl import load_workbook

COLUMNS=["InvoiceNo","StockCode","Description","Quantity","InvoiceDate","UnitPrice","CustomerID","Country"]
SCHEMA_VERSION="1"
LIMIT=9223372036854775807

def sha(path):
    with path.open("rb") as stream:return hashlib.file_digest(stream,"sha256").hexdigest()

def row_bytes(values):
    return (json.dumps(list(values),ensure_ascii=False,separators=(",",":"),allow_nan=False)+"\n").encode("utf-8")

def normalize(values):
    inv,stock,desc,qty,when,price,customer,country=values
    if inv is None or stock is None or not isinstance(when,datetime):raise ValueError("INVALID_REQUIRED_FIELD")
    if type(qty) not in (int,float) or int(qty)!=qty:raise ValueError("INVALID_QUANTITY")
    if type(price) not in (int,float):raise ValueError("INVALID_PRICE")
    price=Decimal(str(price))
    if not price.is_finite():raise ValueError("INVALID_PRICE")
    scaled=price*1000000
    if scaled!=scaled.to_integral_value():raise ValueError("PRICE_PRECISION_EXCEEDS_MICROS")
    micros=int(scaled);amount=micros*int(qty)
    if any(abs(v)>LIMIT for v in (int(qty),micros,amount)):raise ValueError("INTEGER_OVERFLOW")
    if customer is not None and (type(customer) not in (int,float) or int(customer)!=customer):raise ValueError("INVALID_CUSTOMER")
    if country is not None and not isinstance(country,str):raise ValueError("INVALID_COUNTRY")
    if desc is not None and type(desc) not in (str,int,float):raise ValueError("INVALID_DESCRIPTION_TYPE")
    normalized=[str(inv),str(stock),desc,int(qty),when.isoformat(),format(price.normalize(),"f"),None if customer is None else int(customer),country]
    return normalized,micros,amount

def import_workbook(root):
    root=Path(root);source=root/"data/raw/Online Retail.xlsx"
    registered=json.loads((root/"evidence/data-profile/source-manifest.json").read_text(encoding="utf-8"))
    profile=json.loads((root/"evidence/data-profile/profile.json").read_text(encoding="utf-8"))
    source_hash=sha(source)
    if source_hash!=registered["source_sha256"]:raise ValueError("SOURCE_HASH_MISMATCH")
    out=root/"data/warehouse";out.mkdir(parents=True,exist_ok=True)
    target=out/"online-retail.sqlite";manifest_path=out/"manifest.json"
    if target.exists() or manifest_path.exists():
        if not target.exists() or not manifest_path.exists():raise ValueError("INCOMPLETE_EXISTING_IMPORT")
        m=json.loads(manifest_path.read_text(encoding="utf-8"))
        if m["source_sha256"]!=source_hash or m["schema_version"]!=SCHEMA_VERSION or sha(target)!=m["database_sha256"]:raise ValueError("EXISTING_IMPORT_MISMATCH")
        return {"status":"already_verified","rows":m["row_count"],"database_sha256":m["database_sha256"]}
    staged=out/("building-"+uuid.uuid4().hex+".sqlite")
    start=time.perf_counter();book=None
    try:
        book=load_workbook(source,read_only=True,data_only=True)
        if book.sheetnames!=["Online Retail"]:raise ValueError("SHEET_MISMATCH")
        rows=book["Online Retail"].iter_rows(values_only=True)
        if list(next(rows))!=COLUMNS:raise ValueError("COLUMN_MISMATCH")
        source_digest=hashlib.sha256();missing=Counter();description_types=Counter();monthly=defaultdict(int);countries=defaultdict(int);invoices=defaultdict(set)
        negative_qty=0;positive_rows=0;absolute_sum=0;total_rows=0;min_date=None;max_date=None
        with closing(sqlite3.connect(staged)) as db:
            db.execute("PRAGMA journal_mode=DELETE")
            db.executescript("""
                CREATE TABLE retail(
                  InvoiceNo TEXT NOT NULL, StockCode TEXT NOT NULL, Description,
                  Quantity INTEGER NOT NULL, InvoiceDate TEXT NOT NULL, UnitPrice TEXT NOT NULL,
                  CustomerID INTEGER, Country TEXT, source_row INTEGER PRIMARY KEY,
                  price_micros INTEGER NOT NULL, amount_micros INTEGER NOT NULL);
                CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            """)
            batch=[]
            for source_row,values in enumerate(rows,start=2):
                n,price,amount=normalize(values);source_digest.update(row_bytes(n));description_types[type(n[2]).__name__]+=1
                for col,value in zip(COLUMNS,n):
                    if value is None:missing[col]+=1
                total_rows+=1;negative_qty+=int(n[3]<0);absolute_sum+=abs(amount)
                if absolute_sum>LIMIT:raise ValueError("AGGREGATE_OVERFLOW")
                min_date=n[4] if min_date is None else min(min_date,n[4]);max_date=n[4] if max_date is None else max(max_date,n[4])
                if not n[0].upper().startswith("C") and n[3]>0 and price>0:
                    positive_rows+=1;month=n[4][:7];monthly[month]+=amount;countries[n[7]]+=amount;invoices[month].add(n[0])
                batch.append((*n,source_row,price,amount))
                if len(batch)>=5000:
                    db.executemany("INSERT INTO retail VALUES(?,?,?,?,?,?,?,?,?,?,?)",batch);batch=[]
                if total_rows%100000==0:print(f"Imported {total_rows} source rows",flush=True)
            if batch:db.executemany("INSERT INTO retail VALUES(?,?,?,?,?,?,?,?,?,?,?)",batch)
            book.close();book=None
            if total_rows!=profile["rows"]:raise ValueError("ROW_COUNT_MISMATCH")
            if min_date!=profile["date_min"] or max_date!=profile["date_max"]:raise ValueError("DATE_RANGE_MISMATCH")
            if any(missing[c]!=profile["missing"][c]["count"] for c in COLUMNS):raise ValueError("NULL_COUNT_MISMATCH")
            if negative_qty!=profile["negative_quantity_rows"]:raise ValueError("NEGATIVE_QUANTITY_MISMATCH")
            db.execute("CREATE INDEX retail_date ON retail(InvoiceDate)")
            db.execute("CREATE INDEX retail_country_date ON retail(Country,InvoiceDate)")
            db.commit()
            stored_digest=hashlib.sha256()
            for row in db.execute("SELECT "+",".join(COLUMNS)+" FROM retail ORDER BY source_row"):stored_digest.update(row_bytes(row))
            if stored_digest.hexdigest()!=source_digest.hexdigest():raise ValueError("FULL_ROW_ROUNDTRIP_MISMATCH")
            duplicate_extra=db.execute("SELECT COALESCE(SUM(n-1),0) FROM (SELECT COUNT(*) n FROM retail GROUP BY "+",".join(COLUMNS)+" HAVING COUNT(*)>1)").fetchone()[0]
            if duplicate_extra!=profile["exact_duplicates_extra"]:raise ValueError("DUPLICATE_COUNT_MISMATCH")
            where=" WHERE upper(substr(InvoiceNo,1,1)) <> 'C' AND Quantity>0 AND price_micros>0"
            monthly_sql=dict(db.execute("SELECT substr(InvoiceDate,1,7),SUM(amount_micros) FROM retail"+where+" GROUP BY substr(InvoiceDate,1,7)"))
            country_sql=dict(db.execute("SELECT Country,SUM(amount_micros) FROM retail"+where+" GROUP BY Country"))
            count_sql=dict(db.execute("SELECT substr(InvoiceDate,1,7),COUNT(DISTINCT InvoiceNo) FROM retail"+where+" GROUP BY substr(InvoiceDate,1,7)"))
            if monthly_sql!=dict(monthly) or country_sql!=dict(countries) or count_sql!={k:len(v) for k,v in invoices.items()}:raise ValueError("AGGREGATE_RECONCILIATION_FAILED")
            if db.execute("PRAGMA integrity_check").fetchone()[0]!="ok":raise ValueError("INTEGRITY_CHECK_FAILED")
            metadata={"schema_version":SCHEMA_VERSION,"source_sha256":source_hash,"canonical_rows_sha256":source_digest.hexdigest(),"row_count":str(total_rows)}
            db.executemany("INSERT INTO metadata VALUES(?,?)",metadata.items());db.commit()
        if sha(source)!=source_hash:raise ValueError("SOURCE_CHANGED_DURING_IMPORT")
        manifest={**metadata,"row_count":total_rows,"database_sha256":sha(staged),"database_file":"online-retail.sqlite",
                  "date_min":min_date,"date_max":max_date,"created_at":datetime.now(timezone.utc).isoformat(),"source_columns":COLUMNS,
                  "importer_sha256":sha(Path(__file__)),"metric_version":"0.2"}
        # Rename only the new staging file within this exact warehouse directory.
        if staged.resolve().parent!=out.resolve() or target.resolve().parent!=out.resolve() or target.exists():raise ValueError("INVALID_TARGET")
        staged.rename(target)
        manifest_path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
        report={"status":"passed","rows":total_rows,"source_sha256":source_hash,"database_sha256":manifest["database_sha256"],
            "schema_version":SCHEMA_VERSION,"canonical_rows_sha256":source_digest.hexdigest(),"full_row_roundtrip":True,
            "retained_duplicate_extra":duplicate_extra,"retained_negative_quantity":negative_qty,"null_counts":dict(missing),
            "description_cell_types":dict(description_types),"positive_rows":positive_rows,"source_min":min_date,"source_max":max_date,"integrity_check":"ok",
            "independent_month_amount_micros":dict(sorted(monthly.items())),
            "independent_month_invoice_counts":{k:len(v) for k,v in sorted(invoices.items())},
            "monthly_and_country_reconciliation":True,"source_unchanged":True,"elapsed_seconds":round(time.perf_counter()-start,2),
            "formal_eval_executed":0,"model_calls":0}
        evidence=root/"evidence/i05";evidence.mkdir(parents=True,exist_ok=True)
        (evidence/"import-validation.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
        return {k:report[k] for k in ("status","rows","full_row_roundtrip","monthly_and_country_reconciliation","elapsed_seconds")}
    finally:
        if book is not None:book.close()
        if staged.exists():
            if staged.resolve().parent!=out.resolve():raise ValueError("INVALID_STAGING_PATH")
            staged.unlink()
