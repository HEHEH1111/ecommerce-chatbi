export type ResultRow={dimension:string;value:string|null;display:string};
export type ResultData={
 columns:{key:'dimension'|'value';label:string;type:string;unit?:string}[];
 rows:ResultRow[];
 chart:{kind:'line'|'bar';x_key:'dimension';y_key:'value'}|null;
 summary:{label:string;value:string|null;display:string;unit:string}|null;
 ratio:{previous_month:string;target_month:string;numerator_micros:string|null;denominator_micros:string|null}|null;
 truncated:boolean;
};
export type QueryResult={
 request_id:string;status:string;message?:string;synthetic?:boolean;data:ResultData|null;warnings?:string[];
 evidence?:{retrieval?:{version:string;bundle_digest:string;method:string;hits:{id:string;title:string;version:string;source:string;sha256:string}[]};execution:string;source_kind?:string;dataset_name?:string;dataset_sha256?:string;metric_id?:string;metric_version:string;
 conditions?:{start:string|null;end_exclusive:string|null;country:string|null;group_by:string|null;accept_partial:boolean};
 effective_range?:{start:string;end_exclusive:string;source_last_timestamp:string}|null;
 sql?:string;parameters?:unknown[];elapsed_ms?:number;returned_rows?:number;truncated?:boolean;generated_at?:string};
};
