import { useState } from 'react';
import type { Context, ContextUpdate, Registry } from './api';

function inclusive(exclusive:string|null){if(!exclusive)return '';const d=new Date(exclusive+'T00:00:00Z');d.setUTCDate(d.getUTCDate()-1);return d.toISOString().slice(0,10)}
function exclusive(inclusive:string){if(!inclusive)return null;const d=new Date(inclusive+'T00:00:00Z');d.setUTCDate(d.getUTCDate()+1);return d.toISOString().slice(0,10)}

export function ConditionsPanel({context,registry,disabled,onSave}:{context:Context;registry:Registry|null;disabled:boolean;onSave:(update:ContextUpdate,label:string)=>void}){
 const c=context.conditions;
 const [metric,setMetric]=useState(c.metric_id||'');
 const [start,setStart]=useState(c.start||'');
 const [end,setEnd]=useState(inclusive(c.end_exclusive));
 const [country,setCountry]=useState(c.country||'');
 const [group,setGroup]=useState(c.group_by||'');
 const [partial,setPartial]=useState(c.accept_partial);
 const dirty=metric!==(c.metric_id||'')||start!==(c.start||'')||end!==inclusive(c.end_exclusive)||country!==(c.country||'')||group!==(c.group_by||'')||partial!==c.accept_partial;
 function submit(confirm:boolean){
  onSave({expected_revision:context.revision,confirm,changes:{metric_id:metric||null,start:start||null,end_exclusive:exclusive(end),country:country.trim()||null,group_by:group||null,accept_partial:partial}},confirm?'确认本轮分析条件':'保存已补充的条件');
 }
 return <section className="conditions-panel" aria-label="本轮分析条件">
  <div className="conditions-heading"><h2>本轮分析条件</h2><span>{dirty?'有修改 · 尚未保存':context.confirmed?(context.last_execution?.execution==='query'?'查询已完成':context.last_execution?.status==='error'?'查询未完成':'已确认 · 尚未执行'):'待补充或确认'}</span></div>
  <p className="conditions-intro">{context.interpretation==='model_proposed'?'AI已整理候选条件，请核对是否符合你的问题。':context.interpretation==='session_reference'?'请核对当前会话的国家条件。':'当前为手动填写的条件。'} 同一会话保留已填内容；确认前请核对指标和范围。</p>
  {context.pending.length>0&&<ul className="condition-questions">{context.pending.map((f)=><li key={f.field}>{f.message}</li>)}</ul>}
  <form onSubmit={e=>{e.preventDefault();submit(true)}}>
   <fieldset disabled={disabled}>
    <div className="condition-grid">
     <label className="wide">统计指标<select aria-label="统计指标" value={metric} onChange={e=>setMetric(e.target.value)}><option value="">请选择明确的指标</option>{registry?.metrics.map(m=><option value={m.id} key={m.id}>{m.name}</option>)}</select></label>
     <label>开始日期<input aria-label="开始日期" type="date" value={start} onChange={e=>{setStart(e.target.value);setPartial(false)}}/></label>
     <label>结束日期（含当天）<input aria-label="结束日期" type="date" value={end} onChange={e=>{setEnd(e.target.value);setPartial(false)}}/></label>
     <label>客户国家<input aria-label="客户国家" value={country} maxLength={80} placeholder={context.pending.some(p=>p.field==="country")?"请输入国家名称，或填“国家不限”":"留空表示国家不限；例如英国"} onChange={e=>setCountry(e.target.value)}/></label>
     <label>分组方式<select aria-label="分组方式" value={group} onChange={e=>setGroup(e.target.value)}><option value="">不分组</option><option value="date">按日期</option><option value="month">按月份</option><option value="Country">按客户国家</option><option value="StockCode">按全部交易代码</option></select></label>
    </div>
    <p className="condition-boundary">记录范围：2010-12-01 至 2011-12-09 12:50。金额含费用、调整及重复记录；不是支付GMV或纯商品收入。</p>
    <label className="partial-check"><input type="checkbox" checked={partial} onChange={e=>setPartial(e.target.checked)}/> 若覆盖不完整，仅采用实际覆盖区间（不适用于月环比）</label>
    <div className="condition-actions"><button className="secondary" type="button" onClick={()=>submit(false)}>保存待补条件</button><button className="confirm-conditions" type="submit">确认条件</button><button className="clear-conditions" type="button" onClick={()=>onSave({expected_revision:context.revision,reset:true,confirm:false,changes:{}},'清除本会话的全部分析条件')}>清除全部条件</button></div>
   </fieldset>
  </form>
  {context.confirmed&&!dirty&&<p className="confirmed-note">条件已保存；执行结果请查看上方回复。可继续修改条件，历史记录会保留当时的版本。</p>}
  {context.confirmed&&!dirty&&context.effective_range&&<p className="confirmed-note">实际可用日期：{context.effective_range.start} 至 {inclusive(context.effective_range.end_exclusive)}；数据最晚记录：{context.effective_range.source_last_timestamp.replace('T',' ')}。</p>}
 </section>
}
