'use strict';
const {parseTime}=require('../browser-companion/parser.js');
const NOTICE_DAYS=30;
function withinNoticeWindow(timestamp,now=Date.now()) {
  const stamp=typeof timestamp==='string'?Date.parse(timestamp):NaN;
  // Unknown dates need explicit review, never reinterpret scan time as publication.
  return !Number.isFinite(stamp)||stamp>=now-NOTICE_DAYS*86400000;
}
function noticeListInWindow(label,now=Date.now()) {
  const parsed=parseTime(label,new Date(now));
  if(parsed)return withinNoticeWindow(parsed,now);
  const match=String(label||'').trim().match(/^(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})日?$/);
  if(!match)return true;
  const [year,month,day]=match.slice(1).map(Number);
  const checked=new Date(Date.UTC(year,month-1,day));
  if(checked.getUTCFullYear()!==year||checked.getUTCMonth()+1!==month||checked.getUTCDate()!==day)return true;
  // Date-only publication labels include the boundary day; never invent a task time.
  const endOfDay=Date.UTC(year,month-1,day,23,59,59,999)-8*3600000;
  return endOfDay>=now-NOTICE_DAYS*86400000;
}
module.exports={NOTICE_DAYS,withinNoticeWindow,noticeListInWindow};
