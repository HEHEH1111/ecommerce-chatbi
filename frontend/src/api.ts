import type {QueryResult} from './resultTypes';
export type Health = {service:string;app_version:string;model:{status:string;configured?:boolean};data:{status:string;query_available?:boolean};resources:{status:string};capabilities:{natural_language_query:boolean}};
export type Dataset = {id:string;name:string;rows:number;columns:number;currency:string;start:string;end:string;license:string;source_url:string;source_sha256:string;limitations:string[];quality:{missing_customer:number;duplicate_extra:number;negative_quantity:number}};
export type Metric = {id:string;name:string;kind:string;warning?:string;formula?:string;field?:string;enabled:boolean};
export type Registry = {version:string;metrics:Metric[];unsupported_without_more_data:string[]};
export type Session = {session_id:string;session_token:string;title:string;created_at:string};
export type Context = {last_execution?:{status:string;execution:string}|null;interpretation?:string;revision:number;conditions:{metric_id:string|null;start:string|null;end_exclusive:string|null;country:string|null;group_by:string|null;accept_partial:boolean};pending:{field:string;message:string}[];confirmed:boolean;effective_range?:{start:string;end_exclusive:string;source_last_timestamp:string}|null};
export type ContextUpdate = {expected_revision:number;changes:Partial<Context['conditions']>;reset?:boolean;confirm?:boolean};
export type History = {messages:Message[];context:Context};
export type Reply = QueryResult & {plan?:{sql:string;parameters:unknown[];metric_id:string;kind:string};message?:string;context?:Context;clarification?:{fields:{field:string;message:string}[]};error?:{code:string;message:string;retryable:boolean};next_step?:string};
export type Message = {request_id:string;question:string;status:string;created_at:string;response:Reply|null};
export async function api<T>(url:string, init:RequestInit={}):Promise<T>{
 const timeout=AbortSignal.timeout(10000);
 const signal=init.signal?AbortSignal.any([init.signal,timeout]):timeout;
 const response=await fetch(url,{...init,signal,headers:{'Content-Type':'application/json',...init.headers}});
 let body; try{body=await response.json()}catch{throw new Error('服务没有返回有效响应，请检查本地服务。')}
 if(!response.ok)throw new Error(body.error?.message||body.detail||'请求未完成，请重试。');
 return body as T;
}
export function auth(session:Session){return {'X-Session-Token':session.session_token};}
const KEY='chatbi.local.sessions.v1';
export function readSessions():Session[]{
 try{const value=JSON.parse(localStorage.getItem(KEY)||'[]');
 return Array.isArray(value)?value.filter((s)=>typeof s.session_id==='string'&&typeof s.session_token==='string'&&typeof s.title==='string'&&typeof s.created_at==='string'):[];}catch{return []}
}
export function saveSessions(sessions:Session[]){localStorage.setItem(KEY,JSON.stringify(sessions));}
