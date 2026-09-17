#!/usr/bin/env python3
"""Generate the GitHub-only n8n container preparation graph and retire host graphs."""
import json
from pathlib import Path
import uuid

ROOT = Path(__file__).resolve().parent
nodes, connections = [], {}


def node(name, kind, parameters, x, y=0, version=1, **extra):
    value = {"id": str(uuid.uuid5(uuid.NAMESPACE_URL, "fedora45-preparation/" + name)),
             "name": name, "type": "n8n-nodes-base." + kind, "typeVersion": version,
             "position": [x, y], "parameters": parameters, **extra}
    nodes.append(value)
    return value


def code(name, text, x, y=0):
    return node(name, "code", {"jsCode": text.strip()}, x, y, 2)


def link(source, target, output=0):
    outputs = connections.setdefault(source, {"main": []})["main"]
    while len(outputs) <= output:
        outputs.append([])
    outputs[output].append({"node": target, "type": "main", "index": 0})


def branch(name, expression, x, y=0):
    return node(name, "if", {"conditions": {"options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict", "version": 2},
                "combinator": "and", "conditions": [{"id": name, "leftValue": expression, "rightValue": "",
                "operator": {"type": "boolean", "operation": "true", "singleValue": True}}]}, "options": {}}, x, y, 2.2)


def http(name, url, x, y=0, **parameters):
    params = {"url": url, "authentication": "genericCredentialType", "genericAuthType": "httpHeaderAuth",
              "sendHeaders": True, "headerParameters": {"parameters": [{"name": "Accept", "value": "application/vnd.github+json"},
               {"name": "X-GitHub-Api-Version", "value": "2022-11-28"}]}, "options": {"timeout": 30000}, **parameters}
    return node(name, "httpRequest", params, x, y, 4.2,
                credentials={"httpHeaderAuth": {"id": "fedora45GitHubPreparation", "name": "Fedora45 GitHub preparation"}}, retryOnFail=False)


base = "https://api.github.com/repos/safrano9999/fedora45-ai-safrano9999"
node("Scope", "stickyNote", {"content": "## Container preparation only\nGitHub API + GitHub Actions. No host access. No image build, pull, tagging, restart or live-container tests.\nA new OpenClaw OR Hermes release opens the gate. Always capture and test BOTH latest Ephemeral commits.\nReturn READY_FOR_BUILD and its commit to Hermes. Further steps belong to the user/Hermes. Compatibility failures stop; source repairs require explicit Go.", "width": 1600, "height": 260}, 0, -430)
node("Manual preparation", "manualTrigger", {}, -440, -120)
node("Webhook - preparation or --check", "webhook", {"httpMethod": "POST", "path": "fedora45-update-loop", "authentication": "headerAuth", "responseMode": "responseNode", "options": {"rawBody": True}}, -440, 120, 2,
     webhookId="851b669a-1ae5-4167-bac0-59e3d2e6555a", credentials={"httpHeaderAuth": {"id": "fedora45WebhookBearer", "name": "Fedora45 webhook bearer"}})
code("Read request", """
const item=$input.first();
const webhook=Object.prototype.hasOwnProperty.call(item.json,'headers');
let raw='';
if(item.binary?.data) raw=(await this.helpers.getBinaryDataBuffer(0,'data')).toString('utf8').trim();
else if(typeof item.json.body==='string') raw=item.json.body.trim();
if(!['','--check','--validate-only','--sources'].includes(raw)) throw new Error('Expected an empty body, --check, --validate-only or --sources');
const query=item.json.query??{};
const check=Object.prototype.hasOwnProperty.call(query,'--check') || raw==='--check';
const validate_only=Object.prototype.hasOwnProperty.call(query,'--validate-only') || raw==='--validate-only';
const sources=Object.prototype.hasOwnProperty.call(query,'--sources') || raw==='--sources';
if([check,validate_only,sources].filter(Boolean).length>1)throw new Error('Choose one check mode');
const target=query.target||'fedora45-ai-safrano9999',source_ref=query.ref||'main';
if(!/^fedora45-ai-[a-z0-9-]+$/.test(target))throw new Error('Invalid target image');
if(!sources&&query.ref)throw new Error('The ref parameter belongs to --sources');
return [{json:{webhook,check,validate_only,sources,target,source_ref}}];
""", -200)
branch("Sources preview?", "={{ $json.sources }}", -200, -180)
source_credentials={"httpHeaderAuth": {"id": "fedora45GitHubPreparation", "name": "Fedora45 GitHub preparation"}}
preview=node("Discover source repositories", "code", {"operation":"inventory"}, 20, -180,
             credentials=source_credentials)
preview["type"]="CUSTOM.fedora45Sources"
node("Return source inventory", "respondToWebhook", {"respondWith":"json","responseBody":"={{ $json }}","options":{"responseCode":200}}, 240, -180, 1.4)
http("Published version pins", base + "/contents/fedora45-ai-core-pre/Containerfile?ref=main", 20)
http("Latest OpenClaw release", "https://api.github.com/repos/openclaw/openclaw/releases/latest", 240)
http("Latest Hermes release", "https://api.github.com/repos/NousResearch/hermes-agent/releases/latest", 460)
code("Validate versions", """
const pin=$('Published version pins').first().json;
if(pin.encoding!=='base64') throw new Error('Unexpected GitHub content encoding');
const text=Buffer.from(pin.content,'base64').toString('utf8');
const release={openclaw:$('Latest OpenClaw release').first().json,hermes:$json};
const versions={};
const parse=v=>{if(!/^\\d+\\.\\d+\\.\\d+$/.test(v))throw new Error('Invalid release version');return v.split('.').map(Number);};
for(const name of ['openclaw','hermes']){
 const matches=[...text.matchAll(new RegExp('^ARG '+name.toUpperCase()+'_VERSION=(\\\\S+)$','gm'))];
 if(matches.length!==1) throw new Error('Missing or duplicate version pin: '+name);
 const r=release[name];if(r.draft||r.prerelease)throw new Error('Expected stable release');
 const latest=name==='openclaw'?r.tag_name.replace(/^v/,''):r.name.match(/^Hermes Agent v(\\d+\\.\\d+\\.\\d+)(?:\\s|$)/)?.[1];
 const current=matches[0][1],a=parse(current),b=parse(latest??'');
 const first=a.findIndex((v,i)=>v!==b[i]);if(first>=0&&b[first]<a[first])throw new Error('Upstream release older than pin');
 versions[name]={current,latest};
}
const update=Object.values(versions).some(v=>v.current!==v.latest);
const stdout=['openclaw','hermes'].map(n=>{const v=versions[n],label=n==='openclaw'?'OpenClaw':'Hermes';return v.current===v.latest?`✅ ${label} version ${v.current} is actual`:`🟡 ${label}: New version available! actual ${v.current}, latest ${v.latest}`;}).join('\\n');
return [{json:{...$('Read request').first().json,versions,update,stdout,baseline:'GitHub main version pins'}}];
""", 680)
branch("Check only?", "={{ $json.check }}", 900)
node("Return two version lines", "respondToWebhook", {"respondWith": "text", "responseBody": "={{ $json.stdout }}", "options": {"responseCode": 200, "responseHeaders": {"entries": [{"name": "Content-Type", "value": "text/plain; charset=utf-8"}]}}}, 1120, -160, 1.4)
branch("Webhook request?", "={{ $json.webhook }}", 1120, 100)
node("Accept preparation", "respondToWebhook", {"respondWith": "noData", "options": {"responseCode": 202}}, 1340, 0, 1.4)
branch("Upstream update available?", "={{ $json.update || $json.validate_only }}", 1560, 120)
code("No update", "return [{json:{...$json,status:'NO_UPDATE',build_started:false,image_pulled:false,container_restarted:false}}];", 1780, 300)
code("Prepare request", """
const request={...$json,run_id:'n8n-'+$execution.id,started_at:new Date().toISOString(),deadline:Date.now()+3600000};
request.dispatch_body=JSON.stringify({ref:'main',inputs:{run_id:request.run_id,validate_only:request.validate_only===true}});
return [{json:request}];
""", 1780)
http("Dispatch preparation Action", base + "/actions/workflows/fedora45-container-preparation.yml/dispatches", 2000,
     method="POST", sendBody=True, specifyBody="json", jsonBody="={{ $json.dispatch_body }}")
node("Wait for preparation", "wait", {"resume": "timeInterval", "amount": 30, "unit": "seconds"}, 2220, 0, 1.1,
     webhookId="c6e7b001-30b5-4a94-8fc3-1595d3766f20")
http("Read preparation runs", "={{ '"+base+"/actions/workflows/fedora45-container-preparation.yml/runs?event=workflow_dispatch&per_page=100&created='+encodeURIComponent('>='+$('Prepare request').first().json.started_at) }}", 2440)
code("Match exact preparation run", """
const request=$('Prepare request').first().json;
if(Date.now()>request.deadline)throw new Error('Preparation timed out');
if(!Array.isArray($json.workflow_runs))throw new Error('Missing workflow runs');
const runs=$json.workflow_runs.filter(r=>r.display_title===`Fedora45 preparation [prepare:${request.run_id}]`);
if(runs.length>1)throw new Error('Ambiguous preparation run');
const run=runs[0],completed=run?.status==='completed';
if(completed&&run.conclusion!=='success')throw new Error('Preparation '+run.conclusion+': '+run.html_url);
return [{json:{...request,completed,actions_run:run?.id??null,actions_url:run?.html_url??null}}];
""", 2660)
branch("Preparation finished?", "={{ $json.completed }}", 2880)
http("Read preparation artifacts", "={{ '"+base+"/actions/runs/'+$json.actions_run+'/artifacts' }}", 3100)
code("Select evidence artifact", """
const artifacts=($json.artifacts??[]).filter(a=>a.name==='container-preparation'&&!a.expired);
if(artifacts.length!==1||artifacts[0].size_in_bytes>10000000)throw new Error('Missing or invalid preparation evidence');
return [{json:{artifact_id:artifacts[0].id}}];
""", 3320)
http("Resolve evidence download", "={{ '"+base+"/actions/artifacts/'+$json.artifact_id+'/zip' }}", 3540,
     options={"timeout": 30000, "redirect": {"redirect": {"followRedirects": False}},
              "response": {"response": {"fullResponse": True, "responseFormat": "text", "neverError": True}}})
code("Validate evidence URL", """
const url=$json.headers?.location;
if($json.statusCode!==302 || typeof url!=='string' || !/^https:\\/\\/productionresults[a-z0-9]+\\.blob\\.core\\.windows\\.net\\//.test(url))throw new Error('Unexpected GitHub artifact download location');
return [{json:{url}}];
""", 3760)
node("Download preparation evidence", "httpRequest", {"url": "={{ $json.url }}", "authentication": "none",
     "options": {"timeout": 30000, "redirect": {"redirect": {"followRedirects": False}},
                 "response": {"response": {"responseFormat": "file", "outputPropertyName": "data"}}}}, 3980, 0, 4.2,
     retryOnFail=False, notes="Signed GitHub artifact URL only. Never forward the GitHub credential to artifact storage.")
node("Unpack evidence", "compression", {"operation": "decompress", "binaryPropertyName": "data", "outputPrefix": "evidence"}, 4200, 0, 1.1)
code("Handoff to Hermes", """
const binary=$input.first().binary??{};
const keys=Object.keys(binary).filter(k=>binary[k].fileName==='container-preparation.json');
if(keys.length!==1)throw new Error('Missing preparation report');
const report=JSON.parse((await this.helpers.getBinaryDataBuffer(0,keys[0])).toString('utf8'));
const validation=$('Prepare request').first().json.validate_only===true;
const statuses=validation?['VALIDATED_ONLY']:['NO_UPDATE','READY_FOR_BUILD'];
if(report.schema_version!==1||!statuses.includes(report.status)||report.validate_only!==validation)throw new Error('Preparation is not ready');
if(report.build_started!==false||report.image_pulled!==false||report.container_restarted!==false)throw new Error('Preparation scope violated');
if(report.status!=='NO_UPDATE'){
 if(!validation&&!/^[0-9a-f]{40}$/.test(report.build_commit??''))throw new Error('Missing build commit');
 for(const name of ['openclaw','hermes']){
  const sha=report.ephemeral_commits?.[name];
  if(!/^[0-9a-f]{40}$/.test(sha??'')||report.checks?.[name]?.status!=='PASS'||report.checks[name].generator_commit!==sha||report.build_inputs?.[name.toUpperCase()+'_EPHEMERAL_COMMIT']!==sha)throw new Error('Incomplete generator evidence: '+name);
 }
 if(report.checks?.hermes_patch?.status!=='PASS')throw new Error('Missing patch evidence');
}
const dispatch=report.status==='READY_FOR_BUILD'?{safrano_build_inputs:{build_commit:report.build_commit}}:{};
return [{json:{...report,...dispatch,target:$('Read request').first().json.target,next:'User/Hermes controls build and pull through Safrano MCP. Pass safrano_build_inputs as build_images inputs; workflow and all cascade sources stay on this build_commit. Container tests are a separate routine after restart.'}}];
""", 4420)
sync=node("Sync sources for ready build", "code", {"operation":"sync"}, 4640,
          credentials=source_credentials, retryOnFail=False,
          notes="Only READY_FOR_BUILD clones/updates depth-1 sources inside the existing n8n volume. No image build or repository scripts.")
sync["type"]="CUSTOM.fedora45Sources"

for source,target in [("Manual preparation","Read request"),("Webhook - preparation or --check","Read request"),("Read request","Published version pins"),("Published version pins","Latest OpenClaw release"),("Latest OpenClaw release","Latest Hermes release"),("Latest Hermes release","Validate versions"),("Validate versions","Check only?"),("Check only?","Return two version lines"),("Webhook request?","Accept preparation"),("Accept preparation","Upstream update available?"),("Upstream update available?","Prepare request"),("Prepare request","Dispatch preparation Action"),("Dispatch preparation Action","Wait for preparation"),("Wait for preparation","Read preparation runs"),("Read preparation runs","Match exact preparation run"),("Match exact preparation run","Preparation finished?"),("Preparation finished?","Read preparation artifacts"),("Read preparation artifacts","Select evidence artifact"),("Select evidence artifact","Resolve evidence download"),("Resolve evidence download","Validate evidence URL"),("Validate evidence URL","Download preparation evidence"),("Download preparation evidence","Unpack evidence"),("Unpack evidence","Handoff to Hermes")]:link(source,target)
for source,target in [("Check only?","Webhook request?"),("Webhook request?","Upstream update available?"),("Upstream update available?","No update"),("Preparation finished?","Wait for preparation")]:link(source,target,1)
connections["Read request"]={"main":[[{"node":"Sources preview?","type":"main","index":0}]]}
link("Sources preview?","Discover source repositories")
link("Discover source repositories","Return source inventory")
link("Sources preview?","Published version pins",1)
link("Handoff to Hermes","Sync sources for ready build")

workflow={"id":"fedora45LoopDraft","name":"Fedora45 Container Preparation","active":True,"nodes":nodes,"connections":connections,
          "settings":{"executionOrder":"v1","executionTimeout":3600,"availableInMCP":True,"saveDataErrorExecution":"all","saveDataSuccessExecution":"all"}}
(ROOT/'n8n-fedora45-workflow.json').write_text(json.dumps(workflow,indent=2,ensure_ascii=False)+'\n')
for suffix,identifier,title in [('integration','fedora45Integration','Integration'),('repair','fedora45Repair','Repair'),('step-report','fedora45StepReport','Step Report')]:
    retired={"id":identifier,"name":"Fedora45 Retired Host "+title,"active":False,"nodes":[
        {"id":str(uuid.uuid5(uuid.NAMESPACE_URL,identifier+'/retired')),"name":"Host workflow retired","type":"n8n-nodes-base.code","typeVersion":2,"position":[0,0],
         "parameters":{"jsCode":"throw new Error('Retired: n8n prepares container sources on GitHub Actions only. Build, pull and post-restart tests belong to the user/Hermes.');"}}],
        "connections":{},"settings":{"executionOrder":"v1","availableInMCP":False}}
    (ROOT/('n8n-fedora45-'+suffix+'.json')).write_text(json.dumps(retired,indent=2)+'\n')
print('Generated preparation graph; retired three host workflows.')
