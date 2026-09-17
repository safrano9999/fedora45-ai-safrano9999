'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const { createHash } = require('node:crypto');
const { inventory, IMAGE_REPO } = require('../n8n-sources/source-inventory');
const { makeSnapshot, snapshotId } = require('../n8n-sources/source-snapshot');
const { upgradePlan, publishedImage } = require('../n8n-sources/source-upgrade');
const root = path.resolve(__dirname, '../..');
const current = 'a'.repeat(40), old = 'b'.repeat(40), fresh = 'c'.repeat(40);
const digest = n => 'sha256:' + String(n).repeat(64);
const versions = {openclaw:{current:'2026.9.4',latest:'2026.9.4'},hermes:{current:'0.21.3',latest:'0.21.3'}};
async function fixture(target='fedora45-ai-base') {
  const files = {};
  for (const layer of await fs.readdir(root)) if (layer.startsWith('fedora45-ai-')) {
    for (const suffix of ['Containerfile','build.conf','build/prepare-build-context.sh']) {
      try { files[layer+'/'+suffix] = await fs.readFile(path.join(root,layer,suffix),'utf8'); } catch(e) {if(e.code!=='ENOENT')throw e;}
    }
  }
  const state = {snapshot:null, freshSnapshot:null, trees:{}, images:{}};
  const encoded = text => ({encoding:'base64',size:Buffer.byteLength(text),content:Buffer.from(text).toString('base64')});
  const get = async url => {
    if (url.includes('/commits/')) return {sha:url.endsWith('/main')?current:url.split('/').at(-1)};
    if (url.includes('/git/trees/')) return {tree:state.trees[url.split('/git/trees/')[1].split('?')[0]] || Object.entries(files).map(([p,t])=>({path:p,type:'blob',sha:createHash('sha1').update(t).digest('hex')}))};
    const name=url.split('/contents/')[1]?.split('?')[0];
    if(name==='upgrade-loop/prepared-sources.json') return encoded(JSON.stringify(url.endsWith(fresh)?state.freshSnapshot:state.snapshot));
    assert.ok(Object.hasOwn(files,name),url);return encoded(files[name]);
  };
  const manifest=await inventory(get,{target});
  manifest.repositories=manifest.repositories.map(e=>({...e,commit:e.repository===IMAGE_REPO?current:'d'.repeat(40)}));
  manifest.resolved_at='2026-09-17T00:00:00Z';
  state.snapshot=makeSnapshot(manifest,versions);state.snapshot.source_commit=old;
  const readImage=async layer=>state.images[layer]||({image:layer,revision:old,digest:digest(1),layers:manifest.chain.slice(0,manifest.chain.indexOf(layer)+1).map((_,i)=>digest(i+1))});
  return {get,manifest,readImage,state};
}

test('no change means no preparation; compares published snapshots, not main prepared pins',async()=>{
  const f=await fixture();const p=await upgradePlan(f.get,f.manifest,versions,f.readImage);
  assert.equal(p.required,false);assert.equal(p.start_key,null);assert.deepEqual(p.changes,[]);
});
test('Hermes-only source update starts at Core; multiple changes choose earliest stage',async()=>{
  const f=await fixture();f.manifest.repositories.find(e=>e.repository.endsWith('/hermes-ephemeral')).commit='e'.repeat(40);
  f.manifest.repositories.find(e=>e.repository.endsWith('/CODEANALYST')).commit='f'.repeat(40);
  const p=await upgradePlan(f.get,f.manifest,versions,f.readImage);
  assert.equal(p.start_key,'fedora45_core');assert.equal(p.start_image,'fedora45-ai-core');
  const v=structuredClone(versions);v.hermes.latest='0.21.4';
  assert.equal((await upgradePlan(f.get,f.manifest,v,f.readImage)).start_key,'fedora45_core_pre');
});
test('already rebuilt Core still leaves stale descendants eligible, and asset changes count',async()=>{
  const f=await fixture();f.manifest.repositories.find(e=>e.repository.endsWith('/hermes-ephemeral')).commit='e'.repeat(40);
  f.state.freshSnapshot=makeSnapshot(f.manifest,versions);
  f.state.images['fedora45-ai-core']={image:'fedora45-ai-core',revision:fresh,digest:digest(9),layers:[digest(1),digest(2)]};
  assert.equal((await upgradePlan(f.get,f.manifest,versions,f.readImage)).start_key,'fedora45_base');
  const g=await fixture();g.manifest.repositories.find(e=>e.repository.endsWith('/NOTE')).release={ref:'latest',asset:'note.zip',sha256:'a'.repeat(64)};
  assert.equal((await upgradePlan(g.get,g.manifest,versions,g.readImage)).start_key,'fedora45_core');
});
test('changed parent content, removed repositories and layer definitions are detected',async()=>{
  const f=await fixture();f.state.images['fedora45-ai-core-pre']={image:'fedora45-ai-core-pre',revision:old,digest:digest(9),layers:[digest(9)]};
  assert.equal((await upgradePlan(f.get,f.manifest,versions,f.readImage)).start_key,'fedora45_core');
  const g=await fixture();g.manifest.repositories=g.manifest.repositories.filter(e=>!e.repository.endsWith('/hermes-ephemeral'));
  assert.equal((await upgradePlan(g.get,g.manifest,versions,g.readImage)).start_key,'fedora45_core');
  const h=await fixture();h.state.trees[current]=(await h.get('/repos/'+IMAGE_REPO+'/git/trees/'+old+'?recursive=1')).tree.map(e=>e.path==='fedora45-ai-base/Containerfile'?{...e,sha:'f'.repeat(40)}:e);
  assert.equal((await upgradePlan(h.get,h.manifest,versions,h.readImage)).start_key,'fedora45_base');
});
test('missing published evidence fails instead of assuming latest was built',async()=>{
  const f=await fixture();f.state.snapshot.repositories=[];
  await assert.rejects(upgradePlan(f.get,f.manifest,versions,f.readImage),/snapshot/);
});
test('registry resolves latest, verifies bytes and reads revision without image pulls',async()=>{
  const config=JSON.stringify({config:{Labels:{'org.opencontainers.image.revision':old}},rootfs:{diff_ids:[digest(1)]}});
  const sha=raw=>'sha256:'+createHash('sha256').update(raw).digest('hex');
  const manifest=JSON.stringify({config:{digest:sha(config)}});
  const index=JSON.stringify({manifests:[{platform:{os:'linux',architecture:'amd64'},digest:sha(manifest)}]});
  const calls=[];
  const request=async(url,headers)=>{calls.push(url);if(url.includes('/token?'))return JSON.stringify({token:'fixture'});assert.equal(headers.Authorization,'Bearer fixture');return url.endsWith('/latest')?index:url.includes('/blobs/')?config:manifest;};
  const result=await publishedImage('fedora45-ai-core','',request);
  assert.equal(result.revision,old);assert.equal(result.digest,sha(index));assert.equal(calls.length,4);
  await assert.rejects(publishedImage('fedora45-ai-core','',async(u,h)=>u.includes('/blobs/')?'{}':request(u,h)),/digest mismatch/);
});
test('request accepts explicit option and default gate remains closed without release changes',async()=>{
  const w=JSON.parse(await fs.readFile(path.join(root,'upgrade-loop/n8n-fedora45-workflow.json')));
  const nodes=Object.fromEntries(w.nodes.map(n=>[n.name,n]));
  const AsyncFunction=Object.getPrototypeOf(async function(){}).constructor;
  const parse=new AsyncFunction('$input',nodes['Read request'].parameters.jsCode);
  for(const body of ['upgrade-safrano9999','--upgrade-safrano9999',{mode:'upgrade-safrano9999'},{'upgrade-safrano9999':true}]) {
    const result=await parse({first:()=>({json:{body,headers:{}}})});assert.equal(result[0].json.upgrade_safrano9999,true);
  }
  assert.equal((await parse({first:()=>({json:{body:{},headers:{}}})}))[0].json.upgrade_safrano9999,false);
  await assert.rejects(parse({first:()=>({json:{body:{mode:'--check','upgrade-safrano9999':true},headers:{}}})}));
  assert.equal(w.connections['Sources require preparation?'].main[1][0].node,'No update');
  assert.ok(nodes['Upstream update available?'].parameters.conditions.conditions[0].leftValue.includes('upgrade_safrano9999'));
});
test('a final-stage-only change dispatches that image without a nonexistent cascade',async()=>{
  const f=await fixture('fedora45-ai-safrano9999-full');
  f.manifest.repositories.find(e=>e.repository.endsWith('/VikAI')).commit='e'.repeat(40);
  const p=await upgradePlan(f.get,f.manifest,versions,f.readImage);
  assert.equal(p.start_key,'fedora45_safrano_full');assert.equal(p.cascade,false);
});

test('dependency baseline handles first migration, binds published bytes and rejects unavailable evidence',async()=>{
  const {dependencyBaseline}=require('../n8n-sources/source-upgrade');
  const image={revision:old,digest:digest(2)};
  const read=async()=>({encoding:'base64',size:2,content:Buffer.from('{}').toString('base64')});
  assert.equal((await dependencyBaseline(read,image)).policy_sha256,createHash('sha256').update('{}').digest('hex'));
  assert.equal((await dependencyBaseline(async()=>{throw Object.assign(new Error('missing'),{status:404})},image)).policy_sha256,null);
  for(const status of [401,403,500])await assert.rejects(dependencyBaseline(async()=>{throw Object.assign(new Error('failed'),{status})},image));
  await assert.rejects(dependencyBaseline(async()=>({encoding:'base64',size:200000,content:''}),image));
});
