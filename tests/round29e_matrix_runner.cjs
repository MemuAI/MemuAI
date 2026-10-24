// Round 29 phase 2 (r29e-matrix): stuck.js in node — MemuCode window tool-round pause (label, badge, stale card).
const fs=require("fs"),path=require("path"),vm=require("vm");
const root=process.argv[2];
const sb={console,Math,JSON,Date,Set,Map,Array,Object,String,Number,Promise,RegExp,Error,
 setTimeout:()=>0,setInterval:()=>0,clearInterval:()=>0,fetch:async()=>({ok:true,json:async()=>({})}),
 CustomEvent:class{constructor(t,o){this.type=t;this.detail=o&&o.detail;}},
 document:{documentElement:{lang:"ru"},hidden:false,addEventListener:()=>{},dispatchEvent:()=>true,querySelector:()=>null,getElementById:()=>null}};
sb.S={preferences:{stuck_sensitivity:"normal"},currentId:"c1",messages:[],mode:"code"};
sb.window=sb;sb.addEventListener=()=>{};vm.createContext(sb);
vm.runInContext(fs.readFileSync(path.join(root,"app/pgstatic/stuck.js"),"utf8"),sb,{filename:"stuck.js"});
const M=sb.MemuStuck,out={};
let a;
for(let i=0;i<8;i++){a={role:"assistant",toolCalls:{status:"pending",calls:[{tool:"code_run",args:{command:"cat status.txt"}}]}};sb.S.messages.push(a);if(M.workGate(a,{phase:"tools",chatId:"c1"})){out.pausedAt=i;break;}}
out.steps=a.toolCalls.stuckPause.steps.map(s=>s.label);
let html="";try{html=M.cardHtml(a.toolCalls.stuckPause,{area:"tools_code"});}catch(e){html=String(e)}
out.badge=/Инструменты MemuCode/.test(html);
sb.S.messages.push({role:"user",content:"хватит",createdAt:Date.now()+1000});
try{M.refresh();}catch(e){out.err=String(e)}
out.cleared=!a.toolCalls.stuckPause; out.status=a.toolCalls.status;
console.log(JSON.stringify(out));
