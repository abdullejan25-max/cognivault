/** DSH 0.2.1-rc.3 native-mode pre-step adapter. Never opens its own MCP client. */
import { spawn } from 'node:child_process';
import { createHash, randomUUID } from 'node:crypto';
import { isAbsolute } from 'node:path';

export const name = 'cognivault-automatic-recall';
export const inject = ['tools'];
const ERROR_CODES = new Set(['CANCELLED','DEADLINE_EXCEEDED','RETRIEVE_FAILED','BRIDGE_UNAVAILABLE',
  'BRIDGE_EXITED','INVALID_BRIDGE_MESSAGE','INVALID_BRIDGE_REQUEST','INVALID_RESULT','INVALID_RESPONSE']);
const toolName = config=>`mcp__${config.gatewayServer??'cognivault'}__retrieve_evidence`;

function validConfig(config) {
  return config && Object.keys(config).every(k=>['pythonExecutable','allowed_domains','context_delivery_acknowledged','gatewayServer'].includes(k))
    && typeof config.pythonExecutable==='string' && isAbsolute(config.pythonExecutable)
    && config.context_delivery_acknowledged===true && Array.isArray(config.allowed_domains)
    && config.allowed_domains.length>0 && new Set(config.allowed_domains).size===config.allowed_domains.length
    && config.allowed_domains.every(d=>['memory','history','study'].includes(d))
    && /^[a-z][a-z0-9_]{0,31}$/.test(config.gatewayServer??'cognivault');
}
function missing(code) {
  return JSON.stringify({schema_version:1,status:'UNAVAILABLE',error_code:code,
    instructions:{missing_evidence_is_not_negative_fact:true,disclose_missing_evidence:true},untrusted_data:{evidence:[]}});
}
function textOf(message) {
  return Array.isArray(message.content) ? message.content.filter(p=>p?.type==='text'&&typeof p.text==='string').map(p=>p.text).join('\n') : '';
}
function unwrap(result) {
  if (!result || result.isError || !result.value || result.value.isError) throw new Error('RETRIEVE_FAILED');
  const value=result.value;
  if (value.structuredContent && typeof value.structuredContent==='object') return value.structuredContent;
  const part=value.content?.find(p=>p.type==='text'&&typeof p.text==='string');
  if (!part) throw new Error('INVALID_RESPONSE');
  return JSON.parse(part.text);
}

export function runBridge(request, config, retrieve, signal) {
  return new Promise((resolve,reject)=>{
    if (!validConfig(config) || signal.aborted) {reject(new Error('UNAVAILABLE'));return;}
    const child=spawn(config.pythonExecutable,['-X','utf8','-m','cognivault.recall.bridge'],
      {stdio:['pipe','pipe','ignore'],windowsHide:true,shell:false});
    let settled=false, buffer='', sequence=0, pending=false;
    const finish=(error,result)=>{
      if(settled)return; settled=true;clearTimeout(timer);signal.removeEventListener('abort',onAbort);
      child.stdin.destroy();child.stdout.destroy();child.kill();
      error?reject(error):resolve(result);
    };
    const onAbort=()=>finish(new Error(signal.reason==='DEADLINE_EXCEEDED'?'DEADLINE_EXCEEDED':'CANCELLED'));
    const timer=setTimeout(()=>finish(new Error('DEADLINE_EXCEEDED')),3000);
    signal.addEventListener('abort',onAbort,{once:true});
    child.on('error',()=>finish(new Error('BRIDGE_UNAVAILABLE')));
    child.stdin.on('error',()=>finish(new Error('BRIDGE_UNAVAILABLE')));
    child.on('close',()=>{if(!settled)finish(new Error('BRIDGE_EXITED'));});
    child.stdout.setEncoding('utf8');
    child.stdout.on('data',chunk=>{
      buffer+=chunk;
      if(buffer.length>262144){finish(new Error('INVALID_BRIDGE_MESSAGE'));return;}
      let end;
      while(!settled && (end=buffer.indexOf('\n'))>=0){
        const line=buffer.slice(0,end);buffer=buffer.slice(end+1);
        let msg;
        try {msg=JSON.parse(line);} catch {finish(new Error('INVALID_BRIDGE_MESSAGE'));return;}
        if(msg.type==='result'){
          if(pending || typeof msg.injection!=='string' || msg.injection.length>8000){finish(new Error('INVALID_RESULT'));return;}
          finish(null,msg);return;
        }
        if(msg.type!=='retrieve'||pending||msg.tool!=='retrieve_evidence'||msg.id!==sequence+1
            ||sequence>=3||!msg.arguments||Object.keys(msg.arguments).sort().join(',')!=='limit,queries'
            ||msg.arguments.limit!==3||!msg.arguments.queries||typeof msg.arguments.queries!=='object'
            ||Object.keys(msg.arguments.queries).some(d=>!config.allowed_domains.includes(d))){
          finish(new Error('INVALID_BRIDGE_REQUEST'));return;
        }
        sequence=msg.id;pending=true;
        Promise.resolve().then(()=>retrieve(msg)).then(result=>{
          pending=false;
          if(settled)return;
          const reply=JSON.stringify({id:msg.id,result});
          if(reply.length>262000){finish(new Error('INVALID_RESPONSE'));return;}
          child.stdin.write(reply+'\n');
        },()=>finish(new Error('RETRIEVE_FAILED')));
      }
    });
    child.stdin.write(JSON.stringify({request,allowed_domains:config.allowed_domains,context_delivery_acknowledged:true})+'\n');
  });
}

export function createHandler(ctx, config, createUserMessage) {
  const handled=new WeakMap();
  return async (payload,next)=>{
    const decision=await next();
    const {agent,signal}=payload;
    if(decision.kind==='reject'||signal.aborted)return decision;
    const messages=(payload.messages??[]).filter(m=>m?.source?.kind==='user'&&typeof m.id==='string'&&textOf(m));
    if(!messages.length)return decision;
    const injectContext=context=>({...decision,messages:[...decision.messages,createUserMessage({
      content:[{type:'text',text:context}],source:{kind:name}})]});
    if(!agent?.session?.id)return injectContext(missing('MISSING_HOST_IDENTITY'));
    const identity=createHash('sha256').update(JSON.stringify(messages.map(m=>m.id))).digest('hex');
    let seen=handled.get(agent);
    if(!seen){seen=new Set();handled.set(agent,seen);}
    if(seen.has(identity))return decision;
    seen.add(identity);if(seen.size>1024)seen.delete(seen.values().next().value);
    if(!validConfig(config))return injectContext(missing('CONFIGURATION_REQUIRED'));
    if(typeof ctx.tools.modeFor!=='function'||ctx.tools.modeFor(agent)!=='native')return injectContext(missing('DSH_PTC_UNSUPPORTED'));
    const tool=toolName(config);
    const definition=ctx.tools.get(tool,agent);
    if(!definition?.parameters?.properties?.queries||!definition.parameters.properties.limit
        ||!definition.parameters.required?.includes('queries'))return injectContext(missing('NATIVE_TOOL_UNAVAILABLE'));
    const controller=new AbortController();
    const cancel=()=>controller.abort('CANCELLED');
    signal.addEventListener('abort',cancel,{once:true});
    const timeout=setTimeout(()=>controller.abort('DEADLINE_EXCEEDED'),3000);
    try {
      const current=messages.at(-1);
      const result=await runBridge({session_id:agent.session.id,request_id:identity,message:textOf(current),
        recent_user_messages:messages.slice(-3,-1).map(textOf)},config,async msg=>{
          // Keep native policy, tool guards, agent scope and cancellation.
          return unwrap(await ctx.tools.execute({callId:randomUUID(),name:tool,arguments:msg.arguments,
            agent,signal:controller.signal}));
        },controller.signal);
      if(signal.aborted)return decision;
      return result.injection ? injectContext(result.injection) : decision;
    } catch (error) {
      return signal.aborted ? decision : injectContext(missing(ERROR_CODES.has(error?.message)?error.message:'RECALL_UNAVAILABLE'));
    } finally {
      clearTimeout(timeout);signal.removeEventListener('abort',cancel);controller.abort();
    }
  };
}

export async function apply(ctx, config) {
  const { createUserMessage }=await import('@deepseek-ai/dsh-llm');
  ctx.on('agent/pre-step',createHandler(ctx,config,createUserMessage),{prepend:true});
}
