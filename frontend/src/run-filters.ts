export type RunFilters = {kind:string; name:string; status:string; trigger:string; startedFrom:string; startedTo:string};
export const emptyRunFilters:RunFilters = {kind:'',name:'',status:'',trigger:'',startedFrom:'',startedTo:''};
export const runPageSize = 100;

export function runSearchParams(filters:RunFilters, page=0):string {
  const query = new URLSearchParams({limit:String(runPageSize+1),offset:String(page*runPageSize)});
  for (const key of ['kind','name','status','trigger'] as const) {
    const value=filters[key].trim();
    if(value)query.set(key,value);
  }
  const parseTime=(value:string)=>{
    if(!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(value))throw new Error('请填写有效的开始时间');
    const time=new Date(value+':00+08:00');
    if(Number.isNaN(time.getTime()))throw new Error('请填写有效的开始时间');
    return time;
  };
  const from=filters.startedFrom?parseTime(filters.startedFrom):null;
  const to=filters.startedTo?parseTime(filters.startedTo):null;
  if(from&&to&&from>to)throw new Error('开始时间范围的结束值不能早于起始值');
  if(from)query.set('started_from',from.toISOString());
  // The end minute is included, using an exclusive upper bound in the API.
  if(to)query.set('started_to',new Date(to.getTime()+60000).toISOString());
  return query.toString();
}
