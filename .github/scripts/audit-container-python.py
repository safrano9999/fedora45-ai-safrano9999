#!/usr/bin/env python3
"""Read-only Python and certificate inventory; run only on hosted CI runners."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tarfile

PROBE = r"""
import sys, os, json, ssl, re
try:
 import importlib.metadata as metadata
except ImportError:
 try:
  import importlib_metadata as metadata
 except ImportError:
  metadata = None
r = dict(version=sys.version.split()[0], executable=sys.executable,
 real_executable=os.path.realpath(sys.executable), prefix=sys.prefix,
 base_prefix=getattr(sys, 'base_prefix', sys.prefix), openssl=ssl.OPENSSL_VERSION,
 ssl_default_paths=ssl.get_default_verify_paths()._asdict(), packages={}, uid=os.getuid(), sys_path=sys.path)
try:
 import ensurepip, sysconfig
 from pathlib import Path
 seed_dir=Path(sysconfig.get_config_var('WHEEL_PKG_DIR') or Path(ensurepip.__file__).with_name('_bundled'))
 r['ensurepip']={'version':ensurepip.version(),'directory':str(seed_dir),'wheels':sorted(p.name for p in seed_dir.glob('pip-*.whl'))}
except Exception as e:
 r['ensurepip']={'error':type(e).__name__}
try:
 ctx=ssl.create_default_context()
 bad={'emSign ECC Root CA - G3','AAA Certificate Services','emSign Root CS CA - G3'}
 names={v for c in ctx.get_ca_certs() for row in c['subject'] for k,v in row if k=='commonName'}
 r['native_trust']={'certificates':len(ctx.get_ca_certs()),'distrusted_roots_present':sorted(names & bad)}
except Exception as e:
 r['native_trust']={'error':type(e).__name__}

for name in ('pip','certifi','requests','urllib3','uv','httpx','litellm','hermes-agent','fastapi','mcp'):
 try:
  d=metadata.distribution(name)
  r['packages'][name]={'version':d.version,'path':str(d.locate_file(''))}
 except Exception:
  pass
for name in ('certifi', 'pip._vendor.certifi'):
 try:
  module=__import__(name,fromlist=['where'])
  r[name]={'version':getattr(module,'__version__',None), 'bundle':module.where()}
 except Exception as e:
  r[name]={'error':type(e).__name__}
try:
 import pip
 from pathlib import Path
 vendor=(Path(pip.__file__).parent/'_vendor'/'vendor.txt')
 if vendor.is_file():
  r['pip_vendor_certifi_record']=[s.strip() for s in vendor.read_text().splitlines() if re.match(r'certifi\b',s.strip())]
except Exception:
 pass
print(json.dumps(r,sort_keys=True))
"""
DISCOVER = r"""
printf '__PYTHON__\n'
find / -xdev \( -path /proc -o -path /sys -o -path /dev \) -prune -o \( -type f -o -type l \) \( -name python -o -name python2 -o -name python3 -o -name 'python[23].[0-9]*' -o -name pypy -o -name pypy3 \) -print 2>/dev/null
printf '__ENVIRONMENTS__\n'
find / -xdev \( -path /proc -o -path /sys -o -path /dev \) -prune -o -type f \( -name pyvenv.cfg -o -name 'requirements*.txt' -o -name pyproject.toml \) -print 2>/dev/null
printf '__DISTRO__\n'
cat /etc/os-release 2>/dev/null
printf '__PACKAGES__\n'
if command -v rpm >/dev/null 2>&1; then rpm -qa --qf '%{NAME} %{VERSION}-%{RELEASE}\n' | grep -Ei '(^python|^pip|^uv |certifi|ca-certificates)'; fi
if command -v apk >/dev/null 2>&1; then apk info -v | grep -Ei '(^python|^py[23]-pip|^py[23]-certifi|^uv-|^ca-certificates)'; fi
if command -v dpkg-query >/dev/null 2>&1; then dpkg-query -W -f='${Package} ${Version}\n' | grep -Ei '(^python|^pip|^uv |certifi|ca-certificates)'; fi
true
"""

def command(args, timeout=120):
 try:
  p=subprocess.run(args, capture_output=True, text=True, timeout=timeout)
  return p.returncode,p.stdout,p.stderr[-3000:]
 except subprocess.TimeoutExpired:
  return 124,'','command timed out'


def sandbox(image, entrypoint, *args, app_user=False):
 cmd=['docker','run','--rm','--pull=never','--read-only','--tmpfs','/tmp:rw,nosuid,nodev,size=64m','--network','none','--cap-drop','ALL','--security-opt','no-new-privileges']
 if not app_user: cmd+=['--user','0']
 return cmd+['--entrypoint',entrypoint,image,*args]


def exported_paths(image):
 """Fallback for images without a usable shell: inspect, never start, filesystem."""
 code,cid,err=command(['docker','create','--entrypoint','/audit-not-started',image])
 if code: return [],err
 cid=cid.strip()
 paths=[]
 p=subprocess.Popen(['docker','export',cid],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
 try:
  with tarfile.open(fileobj=p.stdout,mode='r|') as tf:
   for member in tf:
    if re.fullmatch(r'(python[23]?(?:\.[0-9]+)?|pypy3?)',Path(member.name).name):
     if member.isfile() or member.issym(): paths.append('/'+member.name.lstrip('/'))
  p.wait(timeout=120)
  return sorted(set(paths)),None if p.returncode==0 else 'filesystem export failed'
 except Exception as e:
  p.kill()
  return paths,str(e)
 finally:
  command(['docker','rm','-v',cid])


def main():
 ap=argparse.ArgumentParser()
 ap.add_argument('image')
 ap.add_argument('--out',required=True)
 ns=ap.parse_args()
 out=Path(ns.out)
 out.parent.mkdir(parents=True,exist_ok=True)
 result={'image':ns.image,'checked_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'interpreters':[],'errors':[]}
 try:
  code,stdout,stderr=command(['docker','pull',ns.image],1800)
  if code:
   result.update(status='pull_failed',errors=[stderr or stdout[-2000:]])
   return
  code,stdout,stderr=command(['docker','image','inspect',ns.image,'--format','{{json .RepoDigests}}'])
  result['resolved_digests']=json.loads(stdout) if code==0 else []
  code,stdout,stderr=command(sandbox(ns.image,'/bin/sh','-c',DISCOVER),300)
  result['discovery_method']='shell'
  if code==0 and '__PYTHON__' in stdout:
   sections={}
   current=None
   for line in stdout.splitlines():
    if re.fullmatch(r'__\w+__',line):
     current=line
     sections[current]=[]
    elif current: sections[current].append(line)
   paths=[p for p in sections.get('__PYTHON__',[]) if re.fullmatch(r'python[23]?(?:\.[0-9]+)?|pypy3?',Path(p).name)]
   result['environment_files']=sections.get('__ENVIRONMENTS__',[])
   result['distro']=sections.get('__DISTRO__',[])
   result['distro_packages']=sections.get('__PACKAGES__',[])
  else:
   result['discovery_method']='filesystem_export'
   paths,err=exported_paths(ns.image)
   if err:
    result['errors'].append(err)
    result['status']='discovery_incomplete'
  result['candidate_interpreters']=paths
  result['probe_mode']='configured Python startup as image default user'
  seen=set()
  for path in paths:
   code,stdout,stderr=command(sandbox(ns.image,path,'-c',PROBE,app_user=True),45)
   if code:
    vc,vo,ve=command(sandbox(ns.image,path,'--version'),15)
    result['errors'].append({'executable':path,'probe_error':stderr,'version_output':(vo+ve).strip()[:300] if vc==0 else None})
    continue
   try:
    record=json.loads(stdout.strip().splitlines()[-1])
    key=(record['real_executable'],record['prefix'])
    if key not in seen:
     seen.add(key)
     result['interpreters'].append(record)
   except (ValueError,IndexError,KeyError) as e:
    result['errors'].append({'executable':path,'probe_error':str(e)})
  if result.get('status')!='discovery_incomplete':
   result['status']='python_present' if result['interpreters'] else ('no_python_interpreter_found' if not paths else 'python_candidates_unresolved')
 except Exception as e:
  result.update(status='audit_error')
  result['errors'].append(str(e))
 finally:
  out.write_text(json.dumps(result,indent=2)+'\n')
  print(json.dumps({'image':ns.image,'status':result.get('status'),'python_versions':sorted({r['version'] for r in result['interpreters']})}))

if __name__=='__main__': main()
