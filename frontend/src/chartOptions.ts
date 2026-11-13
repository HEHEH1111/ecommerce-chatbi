import type {ResultData} from './resultTypes';
export function makeChartOption(data:ResultData){
 const line=data.chart?.kind==='line',unit=data.columns.find(c=>c.key==='value')?.unit||'';
 return {
  animation:false,color:['#167963'],aria:{enabled:true,label:{description:line?'时间趋势折线图，精确数据见下方表格':'分类条形图，精确数据见下方表格'}},
  dataset:{dimensions:['dimension','value','display'],source:data.rows},
  tooltip:{trigger:'axis',renderMode:'richText',confine:true},
  grid:{left:line?68:100,right:24,top:32,bottom:55,containLabel:false},
  xAxis:line?{type:'category',boundaryGap:false,axisLabel:{hideOverlap:true}}:{type:'value',name:unit,splitNumber:3,axisLabel:{hideOverlap:true,formatter:(v:number)=>Math.abs(v)>=1000000?(v/1000000).toFixed(1)+'M':Math.abs(v)>=1000?(v/1000).toFixed(0)+'k':String(v)}},
  yAxis:line?{type:'value',name:unit,splitNumber:3,axisLabel:{hideOverlap:true,formatter:(v:number)=>Math.abs(v)>=1000000?(v/1000000).toFixed(1)+'M':Math.abs(v)>=1000?(v/1000).toFixed(0)+'k':String(v)}}:{type:'category',inverse:true,axisLabel:{width:88,overflow:'truncate'}},
  dataZoom:data.rows.length>20?[{type:'slider',...(line?{xAxisIndex:0}:{yAxisIndex:0,right:0,width:12}),start:0,end:Math.min(100,20/data.rows.length*100),filterMode:'none'}]:[],
  series:[{name:data.columns.find(c=>c.key==='value')?.label||'',type:line?'line':'bar',
    encode:line?{x:'dimension',y:'value',tooltip:['dimension','display']}:{x:'value',y:'dimension',tooltip:['dimension','display']},
    connectNulls:false,symbolSize:7,lineStyle:{width:2},barMaxWidth:32,itemStyle:{borderRadius:line?0:[0,4,4,0]}}]
 };
}
