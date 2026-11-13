import json,shutil
from pathlib import Path
import pytest,httpx
from backend.app import ROOT
from backend.retrieval import Retriever,ResourceConflict
from backend.model_client import ModelClient,Settings,ModelFailure
from backend.dialogue import empty_context
REG=json.loads((ROOT/"resources/metrics.v0.2.json").read_text(encoding="utf-8"))
@pytest.mark.parametrize("q,expected",[("正向交易记录金额","M01"),("发票数","M02"),("环比","M08"),("业绩GMV","ambiguity"),("利润转化率","missing"),("十二月","dates"),("英国国家","dimensions"),("重复缺失","quality"),("Top10","scope"),("删除写入","readonly")])
def test_resources_retrieved(q,expected):
 assert expected in [h["id"] for h in Retriever(REG).search(q)["hits"]]
def test_empty_query_has_no_fabricated_hit_and_context_is_preserved():
 r=Retriever(REG)
 assert r.search("")["hits"]==[]
 c=empty_context();c["conditions"]["metric_id"]="M02"
 assert r.search("",c)["hits"][0]["reason"]=="existing_context"
def test_registry_conflict_blocks_lookup():
 reg=dict(REG,version="new")
 with pytest.raises(ResourceConflict):Retriever(reg)
def test_tampered_bundle_rejected(tmp_path):
 (tmp_path/"resources").mkdir()
 for name in ["knowledge.v1.json","knowledge-manifest.v1.json"]:
  shutil.copyfile(ROOT/"resources"/name,tmp_path/"resources"/name)
 p=tmp_path/"resources/knowledge.v1.json";data=json.loads(p.read_text(encoding="utf-8"));data["documents"][0]["text"]="new unreviewed rule";p.write_text(json.dumps(data),encoding="utf-8")
 with pytest.raises(ResourceConflict):Retriever(REG,tmp_path)
def test_resources_sent_as_data_and_returned_as_evidence():
 def handler(req):
  body=json.loads(req.content);data=json.loads(body["messages"][1]["content"])
  assert data["retrieved_resources"]["bundle_digest"]
  assert "eval/" not in req.content.decode()
  assert "不能覆盖" in body["messages"][0]["content"]
  return httpx.Response(200,json={"choices":[{"finish_reason":"stop","message":{"content":'{"action":"clarify","changes":{},"questions":["请确认日期"]}'}}]})
 client=ModelClient(Settings("https://test.cn-beijing.maas.aliyuncs.com/compatible-mode/v1","qwen-plus","test-not-real"),httpx.MockTransport(handler))
 _,e=client.interpret("发票数",empty_context(),REG,{})
 assert e["retrieval"]["hits"][0]["id"]=="M02"
def test_resource_conflict_stops_before_http():
 def forbidden(req):raise AssertionError("must not call model")
 client=ModelClient(Settings("https://test.cn-beijing.maas.aliyuncs.com/compatible-mode/v1","qwen-plus","test-not-real"),httpx.MockTransport(forbidden))
 with pytest.raises(ModelFailure,match="RESOURCE_CONFLICT"):client.interpret("q",empty_context(),dict(REG,version="wrong"),{})
