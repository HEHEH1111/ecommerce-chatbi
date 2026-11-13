"""Build the application warehouse from the immutable source; never from audit/eval."""
from pathlib import Path
import json,sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend.import_retail import import_workbook
if __name__=="__main__":
    print(json.dumps(import_workbook(ROOT),ensure_ascii=False,indent=2))
