#!/usr/bin/env python3
"""Generate the GitHub-only n8n container preparation graph and retire host graphs."""
import json
from pathlib import Path
import re
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
LOCK_FILE = ROOT / "n8n-sources" / "versions.lock"
VERSION_LOCKS = {}
for line in LOCK_FILE.read_text().splitlines():
    line = line.strip()
    if not line or line.startswith('#'):
        continue
    match = re.fullmatch(r"([A-Z][A-Z0-9_]*)=(\d+\.\d+\.\d+)", line)
    if not match or match[1] in VERSION_LOCKS:
        raise ValueError(f"Invalid or duplicate version lock entry: {line}")
    VERSION_LOCKS[match[1]] = match[2]
client_description = "Feuer den maximalen Loop: body.mode=maximal selects Safrano sources, stable build deps, custom images and auto-upgrade. Pass returned requests to Safrano MCP. End turn; wait for feedback, do not poll. n8n has no host access."
node("MCP start and completion feedback", "stickyNote", {"content": "## MCP client instructions\nBefore execute_workflow, read this workflow's details. For the user's command 'Feuer den maximalen Loop' (also 'maximal loop'), execute this workflow once with webhookData.body.mode=maximal. This is the preset for upgrade-safrano9999 + upgrade-build-deps + auto-upgrade, selecting both source checks, automatic Safrano builds/pulls and the whitelist image upgrade, with saved webhook feedback by default. maximal also returns safrano_custom_images_request for Safrano MCP upgrade_custom_images. Pass that request unchanged in addition to the Fedora/whitelist request; these independent scopes can be dispatched together before ending the turn. An already_running response reuses the active custom upgrade; never force a duplicate. Custom upgrades preserve owner patches and skip incompatible images, with tests and builds only on GitHub Actions. Outside maximal, opt in with body.upgrade_custom_images=true. No additional mode flags are needed; explicit feedback=false is respected. On READY_FOR_BUILD pass safrano_build_request unchanged to Safrano MCP build_images. On NO_UPDATE pass the returned safrano_upgrade_request to podman_smart1 if present. Do not start another preparation when continuing a completed run. Optional upgrade-build-deps checks all allowlisted third-party build pins on GitHub Actions, preferring stable releases; Base image digests refresh only within their JSON-configured release lines. Combine mode=upgrade-safrano9999+upgrade-build-deps+auto-upgrade (or JSON upgrade_build_deps:true). Changes require a build from core-pre. Runtime requirements keep their existing constraints. Optional body.mode=upgrade-safrano9999 checks consumed Safrano sources against published latest images and returns the earliest build start key. Use body.mode=upgrade-safrano9999+auto-upgrade for the maximum sequence, also updating whitelist image digests/services after build/pull success. NO_UPDATE then returns safrano_upgrade_request for podman_smart1 auto-update. Use body.mode=upgrade-safrano9999+auto (or auto:true) for the authorized build-and-pull sequence: on READY_FOR_BUILD pass safrano_build_request unchanged to Safrano MCP build_images. It includes auto_pull:true and feedback:true using the saved Safrano destination; explicit feedback:false is respected. Without auto-upgrade, NO_UPDATE ends the sequence. Without this option the release gate is unchanged. Use executionMode=production and inputs.type=webhook, webhookData.method=POST. For feedback, set webhookData.body.callback_url (plus callback_secret when required), or body.feedback=true for the saved Hermes hook. Set feedback=false to disable.\nAfter the tool returns its execution ID, END THIS TURN and return control to the user. Do not poll, sleep or wait for completion. The hook receives only {} after success, failure or cancellation; fetch status/evidence then or when the user explicitly asks. A deliver-only Hermes hook posts an outgoing Telegram message and does not wake an agent turn. In that setup wait for the user to say weiter, then read the completed execution and continue its authorized Safrano request without rerunning preparation. This preset does not change Hermes webhook routing.\nOnly webhook feedback is supported here. Do not pass herdr_target: this container has no host/Herdr access. Delivery uses mcp-rendezvous from npm.", "width": 800, "height": 440}, -900, 420)
node("Scope", "stickyNote", {"content": "## Container preparation only\nGitHub API + GitHub Actions. No host access. No image build, pull, tagging, restart or live-container tests.\nOpenClaw is hard-pinned to 2026.9.4; newer upstream OpenClaw releases never change that pin. When OPENCLAW is present in versions.lock, n8n checks for the matching Deterministic release and fire-and-forgets the standalone component workflow only when that release is missing. The image build workflows never run Deterministic. A new Hermes release opens the default gate. Explicit mode upgrade-safrano9999 also checks consumed Safrano sources against published :latest image revisions and selects the earliest affected stage. Without source changes, no preparation or clones, except an explicit upgrade-build-deps check which runs on the GitHub runner. Dependency changes select Core-pre; no dependency changes return NO_UPDATE unless other requested inputs changed. Resolve latest Safrano sources ONCE after this gate. Share the same snapshot with preparation, depth-1 clones and builds. Test BOTH selected Ephemeral commits.\nReturn READY_FOR_BUILD and its commit to Hermes. Further steps belong to the user/Hermes. Compatibility failures stop; source repairs require explicit Go.", "width": 1600, "height": 300}, 0, -430)
node("Manual preparation", "manualTrigger", {}, -440, -120)
node("Webhook - preparation or --check", "webhook", {"httpMethod": "POST", "path": "fedora45-update-loop", "authentication": "headerAuth", "responseMode": "responseNode", "options": {"rawBody": True}}, -440, 120, 2,
     webhookId="851b669a-1ae5-4167-bac0-59e3d2e6555a", credentials={"httpHeaderAuth": {"id": "fedora45WebhookBearer", "name": "Fedora45 webhook bearer"}})
code("Normalize auto mode", r"""
const item=$input.first();
let body=item.json.body;
if(item.binary?.data)body=(await this.helpers.getBinaryDataBuffer(0,'data')).toString('utf8').trim();
if(typeof body==='string')body=body.trim().startsWith('{')?JSON.parse(body):{mode:body.trim()};
body=body&&typeof body==='object'?body:{};
const query=item.json.query??{},options={...query,...body};
let mode=options.mode??'';
if(typeof mode!=='string')throw new Error('mode must be a string');
const maximalAlias=mode.trim().replace(/^--/,'').toLowerCase()==='maximal';
if(options.maximal!==undefined&&![true,false,'true','false'].includes(options.maximal))throw new Error('maximal must be boolean');
const maximal=maximalAlias||[true,'true'].includes(options.maximal);
if(maximal){
 if(!maximalAlias&&mode.trim()&&!/^(?:--)?upgrade-(?:safrano9999|build-deps)$/.test(mode.trim()))throw new Error('maximal cannot be combined with another mode');
 for(const key of ['auto','auto_upgrade','auto-upgrade','upgrade_build_deps','upgrade-build-deps','--upgrade-build-deps','upgrade-safrano9999','--upgrade-safrano9999']){
  if(options[key]!==undefined&&![true,'true'].includes(options[key]))throw new Error('maximal requires '+key+'; use individual modes to select fewer steps');
 }
 mode='upgrade-safrano9999+upgrade-build-deps+auto-upgrade';
 options.auto=true;options.auto_upgrade=true;options.upgrade_build_deps=true;
}
const parts=mode.split('+').map(s=>s.trim().replace(/^--/,''));
const combined=parts.some(p=>p==='upgrade-safrano9999'||p==='upgrade-build-deps');
if(combined&&(parts.some(p=>!['upgrade-safrano9999','upgrade-build-deps','auto','auto-upgrade'].includes(p))||new Set(parts).size!==parts.length||parts.includes('auto')&&parts.includes('auto-upgrade')))throw new Error('Invalid upgrade mode combination');
const depOption=options.upgrade_build_deps??options['upgrade-build-deps']??options['--upgrade-build-deps'];
if(depOption!==undefined&&![true,false,'true','false',''].includes(depOption))throw new Error('upgrade-build-deps must be boolean');
const upgrade_build_deps=parts.includes('upgrade-build-deps')||[true,'true',''].includes(depOption);
const upgradeOption=options.auto_upgrade??options['auto-upgrade'];
if(upgradeOption!==undefined&&![true,false,'true','false'].includes(upgradeOption))throw new Error('auto-upgrade must be boolean');
const auto_upgrade=upgradeOption===undefined?parts.includes('auto-upgrade'):[true,'true'].includes(upgradeOption);
if(options.auto!==undefined&&![true,false,'true','false'].includes(options.auto))throw new Error('auto must be boolean');
const auto=auto_upgrade||(options.auto===undefined?parts.includes('auto'):[true,'true'].includes(options.auto));
if(combined)mode=parts.includes('upgrade-safrano9999')?'upgrade-safrano9999':'upgrade-build-deps';
if(auto&&(['--check','--sources','--validate-only'].includes(mode)||['--check','--sources','--validate-only'].some(k=>Object.hasOwn(options,k))))throw new Error('auto cannot be combined with a check-only mode');
const customOption=options.upgrade_custom_images??options['upgrade-custom-images'];
if(customOption!==undefined&&![true,false,'true','false'].includes(customOption))throw new Error('upgrade_custom_images must be boolean');
if(maximal&&[false,'false'].includes(customOption))throw new Error('maximal includes custom image upgrades');
const upgrade_custom_images=maximal||[true,'true'].includes(customOption);
if(upgrade_custom_images&&['--check','--sources','--validate-only'].includes(mode))throw new Error('custom image upgrades cannot be combined with check-only modes');
const normalized={...body,mode,maximal,auto,auto_upgrade,upgrade_build_deps,upgrade_custom_images};
if(auto&&options.feedback===undefined)normalized.feedback=true;
return [{json:{...item.json,body:normalized}}];
""", -560, 300)
code("Read request", """
const item=$input.first();
const webhook=Object.prototype.hasOwnProperty.call(item.json,'headers');
let raw='';
if(item.binary?.data) raw=(await this.helpers.getBinaryDataBuffer(0,'data')).toString('utf8').trim();
else if(typeof item.json.body==='string') raw=item.json.body.trim();
let body=item.json.body&&typeof item.json.body==='object'?item.json.body:{};
if(raw.startsWith('{')){body=JSON.parse(raw);raw='';}
if(body.mode)raw=body.mode;
if(!['','--check','--validate-only','--sources','upgrade-safrano9999','--upgrade-safrano9999','upgrade-build-deps','--upgrade-build-deps'].includes(raw)) throw new Error('Expected an empty body, --check, --validate-only, --sources or upgrade-safrano9999');
const query={...(item.json.query??{}),...body};
const maximal=query.maximal===true||query.maximal==='true';
const upgrade_custom_images=query.upgrade_custom_images===true||query.upgrade_custom_images==='true';
const auto_upgrade=query.auto_upgrade===true||query.auto_upgrade==='true';
const auto=auto_upgrade||query.auto===true||query.auto==='true';
const build_feedback=auto&&query.feedback!==false&&query.feedback!=='false';
const upgrade_build_deps=query.upgrade_build_deps===true||query.upgrade_build_deps==='true';
const opt=query['upgrade-safrano9999']??query['--upgrade-safrano9999'];
if(opt!==undefined&&![true,false,'true','false',''].includes(opt))throw new Error('upgrade-safrano9999 must be boolean');
const upgrade_safrano9999=['upgrade-safrano9999','--upgrade-safrano9999'].includes(raw)||[true,'true',''].includes(opt);
const check=Object.prototype.hasOwnProperty.call(query,'--check') || raw==='--check';
const validate_only=Object.prototype.hasOwnProperty.call(query,'--validate-only') || raw==='--validate-only';
const sources=Object.prototype.hasOwnProperty.call(query,'--sources') || raw==='--sources';
if([check,validate_only,sources].filter(Boolean).length>1)throw new Error('Choose one check mode');
if((upgrade_safrano9999||upgrade_build_deps)&&(check||sources))throw new Error('upgrade-safrano9999 cannot be combined with --check or --sources');
const target=query.target||'fedora45-ai-safrano9999',source_ref=query.ref||'main';
if(!/^fedora45-ai-[a-z0-9-]+$/.test(target))throw new Error('Invalid target image');
if(!sources&&query.ref)throw new Error('The ref parameter belongs to --sources');
return [{json:{webhook,check,validate_only,sources,maximal,upgrade_safrano9999,upgrade_build_deps,upgrade_custom_images,auto,auto_upgrade,build_feedback,target,source_ref,completion_feedback:item.json.completion_feedback}}];
""", -200)
branch("Sources preview?", "={{ $json.sources }}", -200, -180)
source_credentials={"httpHeaderAuth": {"id": "fedora45GitHubPreparation", "name": "Fedora45 GitHub preparation"}}
preview=node("Discover source repositories", "code", {"operation":"inventory"}, 20, -180,
             credentials=source_credentials)
preview["type"]="CUSTOM.fedora45Sources"
node("Return source inventory", "respondToWebhook", {"respondWith":"json","responseBody":"={{ $json }}","options":{"responseCode":200}}, 240, -180, 1.4)
http("Published version pins", base + "/contents/fedora45-ai-core-pre/Containerfile?ref=main", 20)
release_nodes = []
for component, url in (("openclaw", "https://api.github.com/repos/openclaw/openclaw/releases/latest"),
                       ("hermes", "https://api.github.com/repos/NousResearch/hermes-agent/releases/latest")):
    if component.upper() not in VERSION_LOCKS:
        title = "Latest " + ("OpenClaw" if component == "openclaw" else "Hermes") + " release"
        http(title, url, 240 + 220 * len(release_nodes))
        release_nodes.append((component, title))
lock_json = json.dumps({name.lower(): value for name, value in VERSION_LOCKS.items()}, separators=(",", ":"))
release_json = "{" + ",".join(f"{name}:$('{'Latest ' + ('OpenClaw' if name == 'openclaw' else 'Hermes')} release').first().json" for name, _ in release_nodes) + "}"
code("Validate versions", """
const pin=$('Published version pins').first().json;
if(pin.encoding!=='base64') throw new Error('Unexpected GitHub content encoding');
const text=Buffer.from(pin.content,'base64').toString('utf8');
const release=__RELEASE_JSON__;
const locks=__LOCK_JSON__;
const versions={};
const parse=v=>{if(!/^\\d+\\.\\d+\\.\\d+$/.test(v))throw new Error('Invalid release version');return v.split('.').map(Number);};
for(const name of ['openclaw','hermes']){
 const matches=[...text.matchAll(new RegExp('^ARG '+name.toUpperCase()+'_VERSION=(\\\\S+)$','gm'))];
 if(matches.length!==1) throw new Error('Missing or duplicate version pin: '+name);
 const current=matches[0][1],locked=locks[name];
 if(locked){if(current!==locked)throw new Error(name+' pin mismatch: expected '+locked+', found '+current);versions[name]={current,latest:locked};continue;}
 const r=release[name];if(!r||r.draft||r.prerelease)throw new Error('Expected stable release: '+name);
 const latest=name==='openclaw'?r.tag_name.replace(/^v/,''):r.name.match(/^Hermes Agent v(\\d+\\.\\d+\\.\\d+)(?:\\s|$)/)?.[1];
 const a=parse(current),b=parse(latest??'');
 const first=a.findIndex((v,i)=>v!==b[i]);if(first>=0&&b[first]<a[first])throw new Error('Upstream release older than pin');
 versions[name]={current,latest};
}
const update=Object.values(versions).some(v=>v.current!==v.latest);
const stdout=['openclaw','hermes'].map(n=>{const v=versions[n],label=n==='openclaw'?'OpenClaw':'Hermes';return v.current===v.latest?`✅ ${label} version ${v.current} is actual`:`🟡 ${label}: New version available! actual ${v.current}, latest ${v.latest}`;}).join('\\n');
return [{json:{...$('Read request').first().json,versions,update,stdout,baseline:'GitHub main version pins',version_locks:locks}}];
""".replace('__RELEASE_JSON__', release_json).replace('__LOCK_JSON__', lock_json), 680)
branch("Check only?", "={{ $json.check }}", 900)
node("Return two version lines", "respondToWebhook", {"respondWith": "text", "responseBody": "={{ $json.stdout }}", "options": {"responseCode": 200, "responseHeaders": {"entries": [{"name": "Content-Type", "value": "text/plain; charset=utf-8"}]}}}, 1120, -160, 1.4)
branch("Webhook request?", "={{ $json.webhook }}", 1120, 100)
node("Accept preparation", "respondToWebhook", {"respondWith": "json", "responseBody": "={{ {status:'ACCEPTED',execution_id:$execution.id,feedback_enabled:$json.completion_feedback?.enabled === true,feedback_id:$json.completion_feedback?.feedback_id ?? null,next:$json.completion_feedback?.next ?? 'End this turn immediately. Do not poll. No completion notification was requested.'} }}", "options": {"responseCode": 202}}, 1340, 0, 1.4)
branch("Upstream update available?", "={{ $json.update || $json.validate_only || $json.upgrade_safrano9999 || $json.upgrade_build_deps }}", 1560, 120)
code("No update", """
const upgrade=$json.auto_upgrade===true?{safrano_upgrade_request:{action:'auto-update',feedback:$json.build_feedback!==false},next:'No source build needed. Continue the authorized maximum update: pass safrano_upgrade_request unchanged to Safrano MCP podman_smart1, then end the turn and wait for feedback without polling.'}:{};
const custom=$json.upgrade_custom_images===true?{safrano_custom_images_request:{action:'run',auto_pull:$json.auto===true,restart:$json.auto_upgrade===true,feedback:$json.build_feedback!==false}}:{};
return [{json:{...$json,...upgrade,...custom,status:'NO_UPDATE',build_started:false,image_pulled:false,container_restarted:false}}];
""", 1780, 300)
code("Prepare request", """
const request={...$json,run_id:'n8n-'+$execution.id,started_at:new Date().toISOString(),deadline:Date.now()+10800000};
return [{json:request}];
""", 1780)
branch("OpenClaw lock present?", "={{ Boolean($json.version_locks?.openclaw) }}", 2000, -300)
http("Read Deterministic releases", "https://api.github.com/repos/safrano9999/openclaw-deterministic-latest/releases?per_page=100", 2220, -460)
code("Check Deterministic release", """
const request=$('Prepare request').first().json;
const version=request.version_locks?.openclaw;
const releases=Array.isArray($json)?$json:[];
const asset=`openclaw-${version}-deterministic.tar.gz`;
const ready=releases.some(release=>!release.draft&&!release.prerelease&&
  typeof release.tag_name==='string'&&release.tag_name.startsWith(`${version}-deterministic.`)&&
  release.assets?.some(item=>item.name===asset&&/^sha256:[0-9a-f]{64}$/.test(item.digest??'')));
return [{json:{...request,deterministic_ready:ready,deterministic_version:version}}];
""", 2440, -460)
branch("Deterministic release present?", "={{ $json.deterministic_ready === true }}", 2660, -460)
http("Dispatch Deterministic when missing", "https://api.github.com/repos/safrano9999/fedora45-ai-safrano9999/actions/workflows/openclaw-components.yml/dispatches", 2880, -620,
     method="POST", sendBody=True, specifyBody="json",
     jsonBody="={{ {ref:'main',inputs:{run_id:$json.run_id,publish:true,target_version:$json.deterministic_version}} }}")
resolve=node("Resolve latest sources once", "code", {"operation":"resolve"}, 1900, -180,
             credentials=source_credentials, retryOnFail=False,
             notes="After the release or explicit Safrano gate, resolve latest once and compare published images when requested. The same source snapshot binds preparation, depth-1 clones and image builds.")
resolve["type"]="CUSTOM.fedora45Sources"
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
 const expected=$('Resolve latest sources once').first().json;
 const canonical=v=>Array.isArray(v)?v.map(canonical):v&&typeof v==='object'?Object.fromEntries(Object.keys(v).sort().map(k=>[k,canonical(v[k])])):v;
 if(!/^[0-9a-f]{64}$/.test(report.source_snapshot_id??'')||report.source_snapshot_id!==expected.source_snapshot_id||JSON.stringify(canonical(report.source_snapshot))!==JSON.stringify(canonical(expected.source_snapshot)))throw new Error('Preparation changed the shared source snapshot');
 const entries=Object.fromEntries(report.source_snapshot.repositories.map(e=>[e.repository,e]));
 if(!validation&&!/^[0-9a-f]{40}$/.test(report.build_commit??''))throw new Error('Missing build commit');
 for(const name of ['openclaw','hermes']){
  const sha=report.ephemeral_commits?.[name];
  if(!/^[0-9a-f]{40}$/.test(sha??'')||report.checks?.[name]?.status!=='PASS'||report.checks[name].generator_commit!==sha||report.build_inputs?.[name.toUpperCase()+'_EPHEMERAL_COMMIT']!==sha)throw new Error('Incomplete generator evidence: '+name);
  if(entries['safrano9999/'+name+'-ephemeral']?.commit!==sha)throw new Error('Generator differs from n8n snapshot');
 }
 for(const [repo,prefix] of [['openclaw-deterministic-latest','OPENCLAW_DETERMINISTIC'],['NOTE','NOTE_RELEASE']]){
  const release=entries['safrano9999/'+repo]?.release;
  if(!release||report.build_inputs?.[prefix+'_TAG']!==release.ref||report.build_inputs?.[prefix+'_SHA256']!==release.sha256)throw new Error('Release differs from n8n snapshot');
  if(repo==='openclaw-deterministic-latest'&&report.build_inputs?.OPENCLAW_UPSTREAM_SHA!==report.source_snapshot.openclaw_source?.commit)throw new Error('OpenClaw source differs from n8n snapshot');
 }
}
const plan=report.source_snapshot?.build_plan;
if(plan&&JSON.stringify(report.build_plan)!==JSON.stringify(plan))throw new Error('Preparation changed the build start plan');
const requestedDeps=$('Read request').first().json.upgrade_build_deps===true;
if(requestedDeps&&(report.upgrade_build_deps!==true||report.build_dependencies?.schema_version!==1||report.build_dependencies.status!=='PASS'||typeof report.build_dependencies.required!=='boolean'||report.build_dependencies.image!=='fedora45-ai-core-pre'))throw new Error('Missing build dependency evidence');
if(!requestedDeps&&report.upgrade_build_deps===true)throw new Error('Unrequested build dependency upgrade');
const depsChanged=requestedDeps&&report.build_dependencies.required;
const key=depsChanged?'fedora45_core_pre':plan?.start_key??'fedora45_core_pre';
if(plan?.required!==undefined&&report.status==='READY_FOR_BUILD'&&!plan.required&&!depsChanged)throw new Error('No source update to build');
const request=$('Read request').first().json;
const automatic=request.auto===true?{auto_pull:true,...(request.auto_upgrade===true?{auto_upgrade:true}:{}),feedback:request.build_feedback!==false}:{};
const dispatch=report.status==='READY_FOR_BUILD'?{safrano_build_inputs:{build_commit:report.build_commit},safrano_build_key:key,safrano_build_request:{...automatic,action:'run',section:'chains',key,cascade:depsChanged?request.target!=='fedora45-ai-core-pre':plan?.cascade??(request.target!=='fedora45-ai-core-pre'),inputs:{build_commit:report.build_commit}}}:{};
const custom=!validation&&request.upgrade_custom_images===true?{safrano_custom_images_request:{action:'run',auto_pull:request.auto===true,restart:request.auto_upgrade===true,feedback:request.build_feedback!==false}}:{};
const standalone=report.status==='NO_UPDATE'&&request.auto_upgrade===true?{safrano_upgrade_request:{action:'auto-update',feedback:request.build_feedback!==false}}:{};
return [{json:{...report,...dispatch,...standalone,...custom,target:request.target,auto:request.auto===true,auto_upgrade:request.auto_upgrade===true,next:report.status==='NO_UPDATE'?(request.auto_upgrade===true?'No source build needed. Pass safrano_upgrade_request unchanged to Safrano MCP podman_smart1 to complete the authorized whitelist upgrade.':'No update; finished.'):
(request.auto===true?'Continue the authorized automatic update (build, Smart1 pull and, when auto_upgrade is selected, whitelist digest/service upgrade): pass safrano_build_request unchanged to Safrano MCP build_images. Saved feedback is already selected unless explicitly disabled. ':'User/Hermes controls build and pull through Safrano MCP. Pass safrano_build_request to build_images. ')+ 'Its key is the earliest affected stage; cascade stops at target. The workflow and all cascade sources stay on this build_commit. Container tests are a separate routine after restart.'}}];
""", 4420)
sync=node("Sync sources for ready build", "code", {"operation":"sync"}, 4640,
          credentials=source_credentials, retryOnFail=False,
          notes="Only READY_FOR_BUILD clones/updates depth-1 sources inside the existing n8n volume. No image build or repository scripts.")
sync["type"]="CUSTOM.fedora45Sources"

version_edges = [("Read request", "Published version pins")]
version_tail = "Published version pins"
for _, title in release_nodes:
    version_edges.append((version_tail, title))
    version_tail = title
version_edges.append((version_tail, "Validate versions"))
for source,target in [("Manual preparation","Read request"),("Webhook - preparation or --check","Read request"),*version_edges,("Validate versions","Check only?"),("Check only?","Return two version lines"),("Webhook request?","Accept preparation"),("Accept preparation","Upstream update available?"),("Upstream update available?","Prepare request"),("Dispatch preparation Action","Wait for preparation"),("Wait for preparation","Read preparation runs"),("Read preparation runs","Match exact preparation run"),("Match exact preparation run","Preparation finished?"),("Preparation finished?","Read preparation artifacts"),("Read preparation artifacts","Select evidence artifact"),("Select evidence artifact","Resolve evidence download"),("Resolve evidence download","Validate evidence URL"),("Validate evidence URL","Download preparation evidence"),("Download preparation evidence","Unpack evidence"),("Unpack evidence","Handoff to Hermes")]:link(source,target)
for source,target in [("Check only?","Webhook request?"),("Webhook request?","Upstream update available?"),("Upstream update available?","No update"),("Preparation finished?","Wait for preparation")]:link(source,target,1)
connections["Read request"]={"main":[[{"node":"Sources preview?","type":"main","index":0}]]}
connections["Prepare request"]={"main":[[{"node":"OpenClaw lock present?","type":"main","index":0}]]}
link("OpenClaw lock present?", "Read Deterministic releases")
link("OpenClaw lock present?", "Resolve latest sources once", 1)
link("Read Deterministic releases", "Check Deterministic release")
link("Check Deterministic release", "Deterministic release present?")
link("Deterministic release present?", "Resolve latest sources once")
link("Deterministic release present?", "Dispatch Deterministic when missing", 1)
link("Dispatch Deterministic when missing", "Resolve latest sources once")
branch("Sources require preparation?", "={{ $json.build_required }}", 2000, -180)
link("Resolve latest sources once","Sources require preparation?")
link("Sources require preparation?","Dispatch preparation Action")
link("Sources require preparation?","No update",1)
link("Sources preview?","Discover source repositories")
link("Discover source repositories","Return source inventory")
link("Sources preview?","Published version pins",1)
link("Handoff to Hermes","Sync sources for ready build")

callback=node("Register completion hook", "code", {}, -340, 300,
              notes="Uses published mcp-rendezvous from npm. Pass callback_url (+ callback_secret), or feedback=true for the saved hook; feedback=false disables it. Webhook only, no Herdr/host access. After start, end the client turn and do not poll. Send only {} on success/error/canceled/crashed; clients fetch details afterwards.")
callback["type"]="CUSTOM.fedora45Feedback"
for trigger in ["Manual preparation", "Webhook - preparation or --check"]:
    connections[trigger]={"main":[[{"node":"Normalize auto mode","type":"main","index":0}]]}
link("Normalize auto mode", "Register completion hook")
link("Register completion hook", "Read request")

workflow={"id":"fedora45LoopDraft","name":"Fedora45 Container Preparation","description":client_description,"active":True,"nodes":nodes,"connections":connections,
          "settings":{"executionOrder":"v1","executionTimeout":3600,"availableInMCP":True,"saveDataErrorExecution":"all","saveDataSuccessExecution":"all"}}
(ROOT/'n8n-fedora45-workflow.json').write_text(json.dumps(workflow,indent=2,ensure_ascii=False)+'\n')
for suffix,identifier,title in [('integration','fedora45Integration','Integration'),('repair','fedora45Repair','Repair'),('step-report','fedora45StepReport','Step Report')]:
    retired={"id":identifier,"name":"Fedora45 Retired Host "+title,"active":False,"nodes":[
        {"id":str(uuid.uuid5(uuid.NAMESPACE_URL,identifier+'/retired')),"name":"Host workflow retired","type":"n8n-nodes-base.code","typeVersion":2,"position":[0,0],
         "parameters":{"jsCode":"throw new Error('Retired: n8n prepares container sources on GitHub Actions only. Build, pull and post-restart tests belong to the user/Hermes.');"}}],
        "connections":{},"settings":{"executionOrder":"v1","availableInMCP":False}}
    (ROOT/('n8n-fedora45-'+suffix+'.json')).write_text(json.dumps(retired,indent=2)+'\n')
print('Generated preparation graph; retired three host workflows.')
