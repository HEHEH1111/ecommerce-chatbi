import {useEffect,useRef,useState} from 'react';
import * as echarts from 'echarts/core';
import {BarChart,LineChart} from 'echarts/charts';
import {DatasetComponent,GridComponent,TooltipComponent,DataZoomComponent,AriaComponent} from 'echarts/components';
import {SVGRenderer} from 'echarts/renderers';
import type {QueryResult,ResultData} from './resultTypes';
import {makeChartOption} from './chartOptions';
import './results.css';
echarts.use([BarChart,LineChart,DatasetComponent,GridComponent,TooltipComponent,DataZoomComponent,AriaComponent,SVGRenderer]);
function Chart({data}:{data:ResultData}){
 const host=useRef<HTMLDivElement>(null);
 useEffect(()=>{
  if(!host.current)return;
  const instance=echarts.init(host.current,undefined,{renderer:'svg'});
  instance.setOption(makeChartOption(data));
  const observer=new ResizeObserver(()=>instance.resize());observer.observe(host.current);
  return()=>{observer.disconnect();instance.dispose()};
 },[data]);
 return <div className="result-chart" ref={host} role="img" aria-label={data.chart?.kind==='line'?'时间趋势折线图，精确数据见下方表格':'分类条形图，精确数据见下方表格'}/>;
}
export function ResultPanel({result}:{result:QueryResult}){
 const [page,setPage]=useState(0),[exact,setExact]=useState(false);
 useEffect(()=>{setPage(0);setExact(false)},[result.request_id]);
 const data=result.data,e=result.evidence;
 if(!data)return null;
 const unit=data.columns.find(c=>c.key==='value')?.unit||'';
 const pages=Math.max(1,Math.ceil(data.rows.length/25)),safePage=Math.min(page,pages-1),visible=data.rows.slice(safePage*25,safePage*25+25);
 return <section className="result-panel" aria-label="查询结果">
  {result.synthetic&&<div className="fixture-label">合成测试数据 · 用于验证展示组件，非真实经营结果</div>}
  <div className="result-heading"><div><span className="result-eyebrow">RESULT / {e?.metric_id}</span><h2>{data.summary?.label||data.columns.find(c=>c.key==='value')?.label}</h2></div><span className="result-unit">{data.summary?.unit||unit}</span></div>
  {e?.conditions&&<p className="result-range">{e.conditions.start} 至 {e.conditions.end_exclusive}（结束日不含） · {e.conditions.country||'全部客户国家'}</p>}
  {data.summary&&<div className={'result-number '+(data.summary.value===null?'unavailable':'')} title={data.summary.value||undefined}>{data.summary.display}<small>{data.summary.unit==='%'?'':data.summary.unit}</small></div>}
  <p className={result.status==='no_data'||result.status==='not_computable'?'result-empty':'result-message'}>{result.message}</p>
  {!!result.warnings?.length&&<ul className="result-warnings">{result.warnings.map(w=><li key={w}>{w}</li>)}</ul>}
  {data.chart&&data.rows.length>0&&<><Chart data={data}/><p className="result-caption">图表与表格同源，共 {data.rows.length} 组。图形用于比较，精确值请查看表格。{data.rows.length>20?'可拖动图表缩放条查看其他分组。':''}</p></>}
  {data.rows.length>0&&<>
   <div className="table-toolbar"><strong>结果明细 <span>{data.rows.length} 行{data.truncated?' · 已截断':''}</span></strong><label><input type="checkbox" checked={exact} onChange={e=>setExact(e.target.checked)}/>显示精确值</label></div>
   <div className="result-table-wrap" tabIndex={0} aria-label="可横向滚动的结果表格"><table className="result-table"><caption>查询结果明细 · {unit}</caption><thead><tr>{data.columns.map(c=><th scope="col" key={c.key}>{c.label}{c.unit?'（'+c.unit+'）':''}</th>)}</tr></thead><tbody>{visible.map(row=><tr key={row.dimension}><th scope="row">{row.dimension}</th><td title={'精确值：'+(row.value??'无')}>{exact?(row.value??'—'):row.display}</td></tr>)}</tbody></table></div>
   <div className="result-pagination"><span>{safePage*25+1}–{Math.min((safePage+1)*25,data.rows.length)} / {data.rows.length}</span><div><button disabled={safePage===0} onClick={()=>setPage(safePage-1)}>上一页</button><span>{safePage+1} / {pages}</span><button disabled={safePage+1>=pages} onClick={()=>setPage(safePage+1)}>下一页</button></div></div>
  </>}
  <details className="result-evidence"><summary>查看查询依据与追踪记录</summary><dl>
   {e?.retrieval&&<><dt>参考资源</dt><dd>{e.retrieval.hits.map(h=><div key={h.id}>{h.id} · {h.title} · v{h.version}<small>（{h.source}）</small></div>)}<small>资源版本 {e.retrieval.version} · {e.retrieval.method}</small></dd></>}
   <dt>请求编号</dt><dd>{result.request_id}</dd><dt>数据来源</dt><dd>{e?.dataset_name} · {result.synthetic?'合成夹具':'公开数据'}</dd>
   <dt>执行状态</dt><dd>{e?.execution} · {e?.elapsed_ms} ms</dd><dt>口径版本</dt><dd>{e?.metric_id} / v{e?.metric_version}</dd>
   <dt>数据快照</dt><dd>{e?.dataset_sha256}</dd><dt>实际覆盖</dt><dd>{JSON.stringify(e?.effective_range)}</dd>
   <dt>查询条件</dt><dd><pre>{JSON.stringify(e?.conditions,null,2)}</pre></dd>
   {data.ratio&&<><dt>环比计算依据</dt><dd><pre>{JSON.stringify(data.ratio,null,2)}</pre><small>分子、分母均为百万分之一英镑；百分比保留6位小数，显示2位。</small></dd></>}
   <dt>只读查询</dt><dd><pre>{e?.sql}</pre></dd><dt>绑定参数</dt><dd><pre>{JSON.stringify(e?.parameters)}</pre></dd>
   <dt>生成时间</dt><dd>{e?.generated_at}</dd>
  </dl></details>
 </section>;
}
