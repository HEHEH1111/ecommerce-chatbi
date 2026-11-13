from pathlib import Path
import hashlib,json,math,re
from collections import Counter
ROOT=Path(__file__).resolve().parents[1]
class ResourceConflict(ValueError):pass
def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",",":")).encode()).hexdigest()
def tokens(text):
    text=text.casefold();words=re.findall(r"[a-z0-9_]+",text)
    for segment in re.findall(r"[\u4e00-\u9fff]+",text):
        words.extend(segment);words.extend(segment[i:i+2] for i in range(len(segment)-1))
    return Counter(words)
class Retriever:
    def __init__(self,registry,root=ROOT):
        try:
            self.bundle=json.loads((root/"resources/knowledge.v1.json").read_text(encoding="utf-8"))
            manifest=json.loads((root/"resources/knowledge-manifest.v1.json").read_text(encoding="utf-8"))
            if digest(self.bundle)!=manifest["bundle_digest"] or digest(registry)!=self.bundle["registry_digest"]:raise ResourceConflict()
            if self.bundle["dataset_sha256"]!=registry["source_sha256"] or self.bundle["metric_version"]!=registry["version"]:raise ResourceConflict()
            self.documents=self.bundle["documents"];ids=[d["id"] for d in self.documents]
            if len(ids)!=len(set(ids)) or len(ids)!=10:raise ResourceConflict()
            self.counters=[tokens(self.text(d)) for d in self.documents]
            self.idf={t:math.log((1+len(ids))/(1+sum(t in c for c in self.counters)))+1 for t in set().union(*self.counters)}
            self.bundle_digest=manifest["bundle_digest"]
        except (OSError,ValueError,KeyError,TypeError):raise ResourceConflict("RESOURCE_CONFLICT") from None
    @staticmethod
    def text(d):return d["title"]+" "+" ".join(d["aliases"])+" "+d["text"]
    def search(self,question,context=None,k=3):
        q=tokens(question)
        def score(index):
            c=self.counters[index];w=self.idf
            dot=sum(q[t]*c[t]*w[t]**2 for t in q.keys()&c.keys())
            norm=math.sqrt(sum((v*w.get(t,1))**2 for t,v in q.items())*sum((v*w[t])**2 for t,v in c.items()))
            return (dot/norm if norm else 0)+sum(0.15 for a in self.documents[index]["aliases"] if a.casefold() in question.casefold())
        ranked=sorted(((score(i),d) for i,d in enumerate(self.documents)),key=lambda p:(-p[0],p[1]["id"]))
        hits=[dict(d,score=round(s,6),sha256=digest(d)) for s,d in ranked[:k] if s>0]
        current=(context or {}).get("conditions",{}).get("metric_id")
        if current and current not in {d["id"] for d in hits}:
            d=next((d for d in self.documents if d["id"]==current),None)
            if d:hits.append(dict(d,score=0,sha256=digest(d),reason="existing_context"))
        return {"method":"lexical_tfidf_alias","version":self.bundle["version"],"bundle_digest":self.bundle_digest,"hits":hits}
