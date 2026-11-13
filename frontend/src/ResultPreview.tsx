import {useEffect,useState} from 'react';
import {api} from './api';
import type {QueryResult} from './resultTypes';
import {ResultPanel} from './ResultPanel';
type Sample={id:string;title:string;result:QueryResult};
export function ResultPreview(){
 const [items,setItems]=useState<Sample[]>([]),[active,setActive]=useState('trend'),[error,setError]=useState(''),[attempt,setAttempt]=useState(0);
 useEffect(()=>{
  const abort=new AbortController();setError('');
  api<{items:Sample[]}>('/api/result-previews',{signal:abort.signal}).then(r=>setItems(r.items)).catch(e=>{if(!abort.signal.aborted)setError(e.message)});
  return()=>abort.abort();
 },[attempt]);
 const selected=items.find(i=>i.id===active);
 return <div className="detail-page result-preview"><div className="section-eyebrow">WORKSPACE / PREVIEW</div><h1>让每一个结果，都有依据。</h1>
 <p className="detail-lead">结果组件预览：以下为合成测试数据，通过本地只读查询生成。不会调用 AI，也不代表真实电商分析。</p>
 {error?<div className="result-empty" role="alert">{error}<button className="secondary" onClick={()=>setAttempt(n=>n+1)}>重新加载预览</button></div>:!items.length?<p role="status">正在生成测试预览…</p>:<>
 <div className="preview-tabs" role="group" aria-label="选择测试场景">{items.map(i=><button key={i.id} aria-pressed={i.id===active} onClick={()=>setActive(i.id)}>{i.title}</button>)}</div>
 {selected&&<ResultPanel key={selected.id} result={selected.result}/>}</>}
 </div>;
}
