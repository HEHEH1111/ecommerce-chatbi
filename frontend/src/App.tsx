import { useEffect, useRef, useState } from 'react';
import { ResultPanel } from './ResultPanel';
import { ResultPreview } from './ResultPreview';
import { ConditionsPanel } from './ConditionsPanel';
import { api, auth, readSessions, saveSessions } from './api';
import type { Context, ContextUpdate, History, Dataset, Health, Message, Registry, Reply, Session } from './api';

type Page='chat'|'data'|'metrics'|'status'|'preview';
function Icon({name,size=20}:{name:string;size?:number}){
 const paths:Record<string,React.ReactNode>={
 chat:<><path d="M20 11.5a7.5 7.5 0 0 1-7.5 7.5H5l-3 3V9.5A7.5 7.5 0 0 1 9.5 2H12"/><path d="m17 2 1.5 3.5L22 7l-3.5 1.5L17 12l-1.5-3.5L12 7l3.5-1.5Z"/></>,
 data:<><ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 4 16 4 16 0V5M4 12c0 4 16 4 16 0"/></>,
 book:<><path d="M12 5C8 2 4 3 2 4v15c4-2 7-1 10 1 3-2 6-3 10-1V4c-2-1-6-2-10 1v15"/></>,
 check:<><circle cx="12" cy="12" r="9"/><path d="m8 12 3 3 5-6"/></>,
 plus:<path d="M12 5v14M5 12h14"/>,
 arrow:<><path d="m5 12 14 0m-5-5 5 5-5 5"/></>,
 send:<><path d="m12 19 0-14m-5 5 5-5 5 5"/></>,
 trend:<><path d="M4 4v16h16M7 14l4-4 4 2 5-7"/></>,
 grid:<><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></>,
 globe:<><circle cx="12" cy="12" r="9"/><ellipse cx="12" cy="12" rx="4" ry="9"/><path d="M3 12h18"/></>,
 info:<><circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7v.2"/></>,
 link:<><path d="M14 3h7v7M21 3l-9 9M10 3H4v17h17v-6"/></>,
 lock:<><rect x="5" y="10" width="14" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/></>,
 refresh:<><path d="M20 7v5h-5M4 17v-5h5"/><path d="M5 8a8 8 0 0 1 14-3l1 2M4 17l1 2a8 8 0 0 0 14-3"/></>
 };
 return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]||paths.chat}</svg>
}
const prompts=[
 {icon:'trend',tag:'时间趋势',title:'看看每日的交易变化',text:'展示2011年11月每天的正向交易记录金额'},
 {icon:'globe',tag:'国家分布',title:'了解交易来自哪些国家',text:'查询2011年11月各国家的正向交易记录金额'},
 {icon:'grid',tag:'月度比较',title:'对比两个月的交易表现',text:'比较2011年10月和11月的正向交易记录金额，并计算环比'}
];
const nav=[{id:'chat',name:'对话问数',icon:'chat'},{id:'data',name:'数据来源',icon:'data'},{id:'metrics',name:'指标口径',icon:'book'},{id:'status',name:'连接状态',icon:'check'},{id:'preview',name:'结果预览',icon:'trend'}] as const;
export default function App(){
 const [page,setPage]=useState<Page>('chat');
 const [sessions,setSessions]=useState<Session[]>(readSessions);
 const [active,setActive]=useState<Session|null>(()=>readSessions()[0]||null);
 const [messages,setMessages]=useState<Message[]>([]);
 const [context,setContext]=useState<Context|null>(null);
 const [question,setQuestion]=useState('');
 const [busy,setBusy]=useState(false);
 const [historyLoading,setHistoryLoading]=useState(false);
 const [error,setError]=useState('');
 const [health,setHealth]=useState<Health|null>(null);
 const [dataset,setDataset]=useState<Dataset|null>(null);
 const [registry,setRegistry]=useState<Registry|null>(null);
 const input=useRef<HTMLTextAreaElement>(null);
 const bottom=useRef<HTMLDivElement>(null);
 const [refresh,setRefresh]=useState(0);
 useEffect(()=>{
  const controller=new AbortController();
  Promise.all([
   api<Health>('/api/health',{signal:controller.signal}),
   api<{datasets:Dataset[]}>('/api/datasets',{signal:controller.signal}),
   api<Registry>('/api/metrics',{signal:controller.signal})
  ]).then(([h,d,m])=>{setHealth(h);setDataset(d.datasets[0]||null);setRegistry(m);})
  .catch(e=>{if(!controller.signal.aborted){setHealth(null);setError(e.message)}});
  return ()=>controller.abort();
 },[refresh]);
 useEffect(()=>{
  if(busy)return;
  if(!active){setMessages([]);setContext(null);return;}
  const controller=new AbortController();
  setHistoryLoading(true);
  async function restoreHistory(){
   const session=active!;
   const deadline=Date.now()+60000;
   try{
    while(!controller.signal.aborted){
     const data=await api<History>(`/api/sessions/${session.session_id}/messages`,{headers:auth(session),signal:controller.signal});
     if(controller.signal.aborted)return;
     setMessages(data.messages);setContext(data.context);
     if(!data.messages.some(m=>['queued','processing'].includes(m.status)))return;
     if(Date.now()>=deadline)throw new Error('请求仍在处理中。可点击“重新连接”继续查看进度。');
     await new Promise(r=>setTimeout(r,400));
    }
   }catch(e){if(!controller.signal.aborted)setError(e instanceof Error?e.message:'会话读取失败，请重新连接。')}
   finally{if(!controller.signal.aborted)setHistoryLoading(false)}
  }
  void restoreHistory();
  return ()=>controller.abort();
 },[active?.session_id,busy,refresh]);
 useEffect(()=>{bottom.current?.scrollIntoView({behavior:'smooth',block:'end'});},[messages,busy]);
 function startNew(){if(busy)return;setActive(null);setMessages([]);setContext(null);setQuestion('');setError('');setPage('chat');setHistoryLoading(false)}
 function remember(next:Session[]){
  setSessions(next);try{saveSessions(next)}catch{setError('浏览器未允许保存会话入口。本次会话仍可使用，关闭页面前请保留此窗口。')}
 }
 async function send(update?:ContextUpdate,label?:string){
  if((!question.trim()&&!label)||busy||historyLoading)return;
  const text=label||question.trim();setBusy(true);setError('');
  try{
   let session=active;
   if(!session){
    const created=await api<{session_id:string;session_token:string}>('/api/sessions',{method:'POST',body:JSON.stringify({dataset_id:dataset?.id||'uci_online_retail_352'})});
    session={...created,title:text.slice(0,28),created_at:new Date().toISOString()};
    remember([session,...sessions]);setActive(session);
   }
   const response=await api<{request_id:string}>(`/api/sessions/${session.session_id}/messages`,{
    method:'POST',headers:auth(session),body:JSON.stringify({question:text,client_message_id:crypto.randomUUID(),...(update?{context_update:update}:{})})
   });
   if(!update)setQuestion('');
   let result:Reply|undefined;
   const deadline=Date.now()+60000;
   while(Date.now()<deadline){
    result=await api<Reply>(`/api/requests/${response.request_id}`,{headers:auth(session)});
    if(!['queued','processing'].includes(result.status))break;
    await new Promise(r=>setTimeout(r,400));
   }
   if(!result||['queued','processing'].includes(result.status))throw new Error('等待响应超时。问题可能已保存，请重新打开本会话查看状态。');
   const history=await api<History>(`/api/sessions/${session.session_id}/messages`,{headers:auth(session)});
   setMessages(history.messages);setContext(history.context);
  }catch(e){setError(e instanceof Error?e.message:'请求未完成，请检查本地服务。')}
  finally{setBusy(false)}
 }
 function pick(text:string){setQuestion(text);input.current?.focus();}
 const title=nav.find(n=>n.id===page)!.name;
 return <div className="app">
  <aside className="sidebar">
   <a className="brand" href="#" onClick={e=>{e.preventDefault();if(!busy)setPage('chat')}} aria-label="ChatBI 首页"><span className="brandmark"><i/><i/><i/></span><span>Chat<span className="brand-light">BI</span><small>让数据回答问题</small></span></a>
   <button className="new-chat" onClick={startNew} disabled={busy}><Icon name="plus" size={18}/> 开始新对话 <span>＋</span></button>
   <div className="nav-label">工作空间</div>
   <nav aria-label="主要导航">{nav.map(n=><button key={n.id} className={page===n.id?'nav-item active':'nav-item'} onClick={()=>{setPage(n.id);setError('')}}><Icon name={n.icon}/>{n.name}{n.id==='chat'&&<span className="nav-dot"/>}</button>)}</nav>
   <div className="history"><div className="nav-label">最近对话 <span>{sessions.length?String(sessions.length).padStart(2,'0'):''}</span></div>
    {sessions.length?sessions.map(s=><button disabled={busy} title={s.title} className={'history-item '+(active?.session_id===s.session_id?'selected':'')} key={s.session_id} onClick={()=>{setActive(s);setPage('chat');setError('')}}><Icon name="chat" size={15}/><span>{s.title}</span></button>):<p className="empty-history">你的提问会保存在这里</p>}
   </div>
   <div className="sidebar-note"><span className="small-square"><Icon name="lock" size={16}/></span><div>本地工作空间<small>公开数据 · 仅在本机使用</small></div></div>
   <div className="profile"><span className="avatar">BI</span><div>本地用户<small>AI 产品作品集</small></div><span className="version">v0.9</span></div>
  </aside>
  <main>
   <header className="topbar"><div className="breadcrumb">工作空间 <span>/</span> <strong>{title}</strong></div><div className="top-status"><span className={'connection-dot '+(health?'on':'')}/>{health?'本地服务已连接':'连接服务中'}<span className="separator"/><button onClick={()=>setPage('status')} className="model-pill">{health?.model.configured?'AI 已配置':'AI 待配置'} <span>↗</span></button></div></header>
   <div className="mobile-tools">
    <nav aria-label="手机导航">{nav.map(n=><button key={n.id} className={page===n.id?'active':''} onClick={()=>setPage(n.id)}>{n.name}</button>)}</nav>
    <div><button disabled={busy} onClick={startNew}>＋ 新对话</button><select aria-label="选择历史会话" disabled={busy} value={active?.session_id||''} onChange={e=>{setActive(sessions.find(s=>s.session_id===e.target.value)||null);setPage('chat')}}><option value="">当前新对话</option>{sessions.map(s=><option key={s.session_id} value={s.session_id}>{s.title}</option>)}</select></div>
   </div>
   {error&&<div className="error-banner" role="alert"><Icon name="info" size={18}/><span>{error}</span><button onClick={()=>{setError('');setRefresh(r=>r+1)}}>重新连接</button></div>}
   {page==='chat'?<div className="chat-page">
    <div className="dataset-strip"><span className="data-icon"><Icon name="data" size={19}/></span><div><strong>{dataset?.name||'正在读取数据说明'}</strong><span>公开电商交易数据 <b>·</b> GBP</span></div><button onClick={()=>setPage('data')}>查看数据范围 <Icon name="arrow" size={15}/></button></div>
    <div className={'chat-content '+(messages.length?'has-messages':'')}>
     {historyLoading&&!busy&&!messages.length?<div className="loading-state">正在读取会话…</div>:!messages.length?<section className="welcome">
      <div className="eyebrow"><span/> YOUR DATA, IN CONVERSATION</div>
      <h1>用一句话，<br/><span>读懂你的数据。</span></h1>
      <p className="lead">从一个明确的问题开始。<br className="mobile-break"/>让数据范围、统计口径和答案依据，都清晰可见。</p>
      <div className="capabilities"><span><Icon name="data" size={15}/> 数据可追溯</span><span><Icon name="book" size={15}/> 口径有依据</span><span><Icon name="chat" size={15}/> 过程可核对</span></div>
      <div className="suggestion-heading">可以从这些问题开始<span>选择一个，填入提问框</span></div>
      <div className="suggestions">{prompts.map(p=><button key={p.tag} onClick={()=>pick(p.text)} className="suggestion"><div className="suggestion-top"><Icon name={p.icon} size={20}/><span>{p.tag}</span></div><h3>{p.title}</h3><p>{p.text}</p><span className="card-arrow"><Icon name="arrow" size={17}/></span></button>)}</div>
     </section>:<section className="messages" aria-label="会话内容" aria-live="polite">{messages.map(m=><article className="exchange" key={m.request_id}>
      <div className="user-message"><span className="avatar">BI</span><p>{m.question}</p></div>
      <div className="assistant-message"><span className="assistant-icon"><Icon name="chat" size={20}/></span><div className="reply"><strong>{m.response?.status==='success'?'查询结果':m.response?.status==='no_data'?'没有符合条件的记录':m.response?.status==='not_computable'?'暂不能计算环比':m.response?.status==='needs_clarification'?'请补充或确认条件':m.response?.status==='plan_ready'?'查询计划已就绪':m.response?.status==='unsupported'?'当前不支持此问题':m.response?.status==='error'?'暂未开始分析':'请求处理中'}</strong><p>{m.response?.error?.message||m.response?.message||'正在保存并检查请求状态。'}</p>{m.response?.next_step&&<p className="reply-next">{m.response.next_step}</p>}<details><summary>查看本次请求记录</summary><dl>{m.response?.evidence?.retrieval&&<><dt>本次参考资源</dt><dd>{m.response.evidence.retrieval.hits.map(h=>h.id+" · "+h.title+" · v"+h.version).join("；")}</dd></>}<dt>请求编号</dt><dd>{m.request_id}</dd><dt>状态</dt><dd>{m.response?.error?.code||m.status}</dd><dt>数据执行</dt><dd>{m.response?.evidence?.execution||"未执行查询"}</dd>{m.response?.plan&&<><dt>待执行计划</dt><dd className="snapshot">{JSON.stringify(m.response.plan,null,2)}</dd></>}{m.response?.context&&<><dt>当时的条件</dt><dd className="snapshot">{JSON.stringify(m.response.context.conditions,null,2)}</dd><dt>条件版本</dt><dd>{m.response.context.revision}</dd></>}</dl></details>{m.response?.data&&<ResultPanel result={m.response}/>}</div></div>
     </article>)}</section>}
     {active&&context&&messages.length>0&&<ConditionsPanel key={active.session_id+':'+context.revision} context={context} registry={registry} disabled={busy||historyLoading} onSave={(u,label)=>void send(u,label)}/>}
     {(busy||(historyLoading&&messages.some(m=>["queued","processing"].includes(m.status))))&&<div className="pending" role="status"><span className="spinner"/> 正在处理问题，完成后会自动显示结果…</div>}<div ref={bottom}/>
    </div>
    <div className="composer-wrap">
     <div className="setup-notice"><Icon name="info" size={16}/><span>{health?.data.query_available?'正式只读数据已连接；核对并确认条件后查询。':health?.model.configured?'AI可整理候选条件；正式数据尚未连接。':'AI尚未配置，可手动确认条件；当前不执行查询。'}</span><button onClick={()=>setPage('status')}>查看进度</button></div>
     <form className="composer" onSubmit={e=>{e.preventDefault();void send()}}>
      <textarea ref={input} aria-label="输入数据问题" placeholder="想了解什么？例如：2011年11月各国家的交易金额…" value={question} maxLength={2000} disabled={busy||historyLoading} onChange={e=>setQuestion(e.target.value)} onKeyDown={e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.nativeEvent.isComposing){e.preventDefault();void send()}}}/>
      <div className="composer-bottom"><span><Icon name="data" size={14}/> UCI Online Retail <i>·</i> 公开数据验证</span><div><small>{question.length}/2000</small><button type="submit" className="send" aria-label="发送问题" disabled={!question.trim()||busy||historyLoading||!health}><Icon name="send" size={18}/></button></div></div>
     </form><p className="composer-foot">Enter 发送 · Shift + Enter 换行 <span>数据是历史交易记录，不代表当前经营情况</span></p>
    </div>
   </div>:page==='preview'?<ResultPreview/>:<div className="detail-page">
    <div className="section-eyebrow">WORKSPACE / {page.toUpperCase()}</div>
    <h1>{page==='data'?'先了解数据，再提出问题。':page==='metrics'?'同一个问题，同一套口径。':'每一步，都有清楚的状态。'}</h1>
    <p className="detail-lead">{page==='data'?(health?.data.query_available?'正式只读数据库已连接，原始记录保持不变。':'数据已完成离线核验，正式查询连接未就绪。'):page==='metrics'?'以下是已确定的首版指标定义。确认条件后可执行只读查询。':'本地工作台已启动；AI分析能力仍在按项目流程接入。'}</p>
    {page==='data'&&dataset&&<>
     <div className="source-heading"><div className="source-logo"><Icon name="data" size={27}/></div><div><h2>{dataset.name}</h2><p>{dataset.license} · <a href={dataset.source_url} target="_blank" rel="noreferrer">UCI 官方来源 ↗</a></p></div><span className="badge">{health?.data.query_available?'正式数据已连接':'离线核验完成'}</span></div>
     <div className="facts"><div><small>已核验原始记录</small><strong>{dataset.rows.toLocaleString()}</strong><span>行交易明细</span></div><div><small>源字段</small><strong>{dataset.columns}</strong><span>保留原始结构</span></div><div><small>数据覆盖</small><strong className="date-fact">{dataset.start.slice(0,10)}<br/>— {dataset.end.slice(0,10)}</strong></div><div><small>原始币种</small><strong>{dataset.currency}</strong><span>英镑 · 未做汇率换算</span></div></div>
     <div className="detail-columns"><section className="panel"><h2>使用边界</h2>{dataset.limitations.map((s,i)=><div className="limit-line" key={s}><span>0{i+1}</span><p>{s}</p></div>)}</section><section className="panel"><h2>数据质量提示</h2><dl className="quality-list"><dt>缺少客户编号</dt><dd>{dataset.quality.missing_customer.toLocaleString()} 行</dd><dt>额外重复记录</dt><dd>{dataset.quality.duplicate_extra.toLocaleString()} 行</dd><dt>负数量记录</dt><dd>{dataset.quality.negative_quantity.toLocaleString()} 行</dd></dl><p className="muted">保留原始记录，通过显式口径处理，不擅自清洗后覆盖源文件。</p></section></div>
     <details className="source-details"><summary>查看数据快照标识</summary><code>{dataset.source_sha256}</code></details>
    </>}
    {page==='metrics'&&registry&&<><div className="metric-list">{registry.metrics.map((m,i)=><section className="metric-card" key={m.id}><div className="metric-index">0{i+1}</div><div><div className="metric-title"><h2>{m.name}</h2><span>{m.id} · v{registry.version}</span></div><p>{m.id==='M01'?'非取消记录，数量大于0、单价大于0；汇总数量 × 单价。':m.id==='M02'?'在正向交易合格记录中，按发票编号去重计数。':'（本完整月金额 − 前完整月金额）÷ 前完整月金额。'}</p><div className="metric-warning"><Icon name="info" size={15}/>{m.warning||(m.id==='M02'?'发票数不是交易行数，也不等于已支付订单数。':'前月为0时不可计算；不完整月份需要先澄清。')}</div></div></section>)}</div><section className="panel unsupported"><h2>当前数据无法直接回答</h2><div className="tags">{registry.unsupported_without_more_data.map(s=><span key={s}>{s}</span>)}</div><p>需要相应的支付、成本、流量或分类资料；不能用其他数字替代。</p></section></>}
    {page==='status'&&<><div className="status-grid">{[
     {title:'本地服务',state:health?'已连接':'未连接',done:!!health,detail:'页面与服务接口之间的本地连接。'},
     {title:'会话与条件',state:health?'已就绪':'待连接',done:!!health,detail:'问题与手动确认条件保存在本机，可分次补充、替换或清除。'},
     {title:'数据与口径说明',state:dataset&&registry?'已载入':'待载入',done:!!(dataset&&registry),detail:'展示已核验的来源、范围与指标定义。'},
     {title:'AI 模型',state:health?.model.status==='verified'?'调用已验证':health?.model.configured?'已配置 · 待验证':'待配置',done:health?.model.status==='verified',detail:'用于问题解析；配置完成不等于调用成功。状态：'+(health?.model.status||'未知')},
     {title:'结果表格与图表',state:health?.data.query_available?'真实数据已连接':'数据待接入',done:!!health?.data.query_available,detail:'确认条件后展示真实查询结果；结果预览仍为独立合成数据。'},
     {title:'效果评测',state:'尚未执行',done:false,detail:'已有回归样本和基准；实际问数效果将在功能完成后评测。'}
    ].map(s=><section className="status-card" key={s.title}><div><span className={'status-dot '+(s.done?'done':'')}/><span className={s.done?'status-good':'status-pending'}>{s.state}</span></div><h2>{s.title}</h2><p>{s.detail}</p></section>)}</div><button className="secondary" onClick={()=>setRefresh(r=>r+1)}><Icon name="refresh" size={17}/>重新检查连接</button><p className="muted status-note">发送问题后，会将问题和指标说明交给已配置的国内模型处理；当前不发送交易明细。</p></>}
   </div>}
  </main>
 </div>
}
