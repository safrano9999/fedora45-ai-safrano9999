'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path'),http=require('node:http'),crypto=require('node:crypto');
const root=fs.mkdtempSync(path.join(os.tmpdir(),'n8n-feedback-test-'));
process.env.FEDORA45_FEEDBACK_ROOT=root;process.env.FEDORA45_FEEDBACK_NO_WORKER='1';
const f=require('../n8n-sources/completion-feedback');
test.after(()=>fs.rmSync(root,{recursive:true,force:true}));
test('opt-in, private persisted endpoint, every terminal outcome, no payload details',async()=>{
 let delivered=[];
 assert.equal(f.register('1',{callback_url:'http://example.test',feedback:false}),false);
 assert.equal(f.register('2',{}),false);
 assert.equal(f.register('3',{callback_url:'http://example.test'}),true);
 for(const [index,status] of ['success','error','canceled','crashed'].entries()){
  const id=String(index+10);f.register(id,{feedback:true,callback_url:'http://example.test',callback_secret:'secret'});
  assert.equal(fs.statSync(path.join(root,'jobs',id+'.json')).mode&0o777,0o600);
  await f.tick(async actual=>actual===id?{status,workflowId:'fedora45LoopDraft'}:null,async job=>delivered.push(job.id));
  assert.equal(JSON.parse(fs.readFileSync(path.join(root,'jobs',id+'.json'))).state,'delivered');
 }
 assert.equal(delivered.length,4);
 f.register('20',{feedback:true,callback_url:'http://example.test'});
 await f.tick(async()=>({status:'running',workflowId:'fedora45LoopDraft'}),async()=>assert.fail('early delivery'));
 await f.tick(async()=>({status:'success',workflowId:'other'}),async()=>assert.fail('wrong workflow'));
});
test('failed delivery stays pending, retry keeps idempotency key',async()=>{
 f.register('30',{feedback:true,callback_url:'http://example.test'});
 let delivery;
 await f.tick(async id=>id==='30'?{status:'error',workflowId:'fedora45LoopDraft'}:null,async job=>{delivery=job.delivery;throw new Error('unavailable')});
 const file=path.join(root,'jobs/30.json'),job=JSON.parse(fs.readFileSync(file));assert.equal(job.state,'waiting');job.retry_after=0;f.atomic(file,job);
 await f.tick(async id=>id==='30'?{status:'error',workflowId:'fedora45LoopDraft'}:null,async job=>assert.equal(job.delivery,delivery));
 assert.equal(JSON.parse(fs.readFileSync(file)).state,'delivered');
});
test('actual HTTP gets only signed {}',async()=>{
 let request;
 const server=http.createServer((req,res)=>{let body='';req.on('data',d=>body+=d);req.on('end',()=>{request={body,headers:req.headers};res.writeHead(202);res.end()})});
 await new Promise(r=>server.listen(0,'127.0.0.1',r));
 try{
  await f.send({delivery:'id',callback:{url:`http://127.0.0.1:${server.address().port}/hook`,secret:'test'}});
  assert.equal(request.body,'{}');assert.equal(request.headers['x-hub-signature-256'],'sha256='+crypto.createHmac('sha256','test').update('{}').digest('hex'));
 }finally{await new Promise(r=>server.close(r))}
});
