"""Resolve only allowlisted Core-pre tools; never install, build, or run downloads."""
import argparse
import base64
import copy
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tomllib
import urllib.request
import urllib.parse

ROOT = Path(__file__).resolve().parent.parent
POLICY = Path('upgrade-loop/build-dependencies-whitelist.json')
CONF = Path('fedora45-ai-core-pre/build.conf')
DEFINITIONS = {
    'solana': ('anza-xyz/agave', ['SOLANA_VERSION', 'SOLANA_SHA256']),
    'electrum': ('spesmilo/electrum', ['ELECTRUM_VERSION', 'ELECTRUM_SHA256', 'ELECTRUM_KEYS_COMMIT']),
    'lnd': ('lightningnetwork/lnd', ['LND_VERSION', 'LND_SHA256']),
    'geth': ('ethereum/go-ethereum', ['GETH_VERSION', 'GETH_COMMIT', 'GETH_SHA256']),
    'bip39': ('iancoleman/bip39', ['BIP39_VERSION', 'BIP39_SHA256']),
    'uv': ('astral-sh/uv', ['UV_VERSION', 'UV_INSTALLER_SHA256']),
    'cloudflared': ('cloudflare/cloudflared', ['CLOUDFLARED_VERSION', 'CLOUDFLARED_SHA256']),
    'webhook': ('adnanh/webhook', ['WEBHOOK_VERSION', 'WEBHOOK_SHA256']),
    'fugu': ('SakanaAI/fugu', ['FUGU_VERSION', 'FUGU_COMMIT']),
    'codex': ('@openai/codex', ['CODEX_VERSION', 'CODEX_SHA256']),
    'claude-code': ('@anthropic-ai/claude-code', ['CLAUDE_CODE_VERSION', 'CLAUDE_CODE_SHA256']),
    'vditor': ('vditor', ['VDITOR_VERSION', 'VDITOR_SHA256']),
    'npm': ('npm', ['NPM_VERSION', 'NPM_SHA256']),
}
NPM = {'codex', 'claude-code', 'vditor', 'npm'}


def github(endpoint):
    return json.loads(subprocess.run(['gh', 'api', endpoint], check=True, text=True,
                                    capture_output=True, timeout=60).stdout)


def fetch(url, digest=False):
    # All URLs come from the fixed resolvers below, never from release descriptions.
    with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'curl/8.0'}), timeout=60) as stream:
        sha, body, size = hashlib.sha256(), bytearray(), 0
        limit = 512 * 1024 * 1024 if digest else 2 * 1024 * 1024
        while chunk := stream.read(1024 * 1024):
            size += len(chunk)
            if size > limit:
                raise ValueError('Build dependency download exceeds limit')
            sha.update(chunk)
            if not digest:
                body.extend(chunk)
        if not size:
            raise ValueError('Empty build dependency download')
        return sha.hexdigest() if digest else body.decode()


def numeric(value):
    match = re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)(?:-beta)?', value)
    if not match:
        raise ValueError('Expected a release version, not an alpha/RC/nightly')
    return tuple(map(int, match.groups()))


def validate_pins(name, pins):
    if set(pins) != set(DEFINITIONS[name][1]):
        raise ValueError('Unexpected pins for ' + name)
    for key, value in pins.items():
        if not isinstance(value, str):
            raise ValueError('Pins must be strings')
        if key.endswith('_SHA256'):
            valid = re.fullmatch('[0-9a-f]{64}', value)
        elif key.endswith('_COMMIT'):
            valid = re.fullmatch('[0-9a-f]{8}' if name == 'geth' else '[0-9a-f]{40}', value)
        elif name == 'fugu':
            valid = re.fullmatch('[A-Za-z0-9][A-Za-z0-9._-]*', value)
        else:
            valid = re.fullmatch(r'v?\d+\.\d+\.\d+-beta' if name == 'lnd' else r'v?\d+\.\d+\.\d+', value)
        if not valid:
            raise ValueError('Invalid pin: ' + key)


def asset_hash(repo, release, name, get=github, read=fetch):
    matches = [a for a in release.get('assets', []) if a['name'] == name]
    if len(matches) != 1:
        raise ValueError('Missing or ambiguous release asset: ' + name)
    digest = matches[0].get('digest') or ''
    if re.fullmatch(r'sha256:[0-9a-f]{64}', digest):
        return digest[7:]
    return read(f'https://github.com/{repo}/releases/download/{release["tag_name"]}/{name}', digest=True)


def resolve(entry, get=github, read=fetch):
    name, repo = entry['id'], entry['repository']
    if name in NPM:
        doc = json.loads(read('https://registry.npmjs.org/' + urllib.parse.quote(repo, safe='') + '/latest'))
        v = doc['version']
        numeric(v)
        if entry['release_line'] != 'stable' and not v.startswith(entry['release_line'] + '.'):
            raise ValueError('npm latest is outside the configured release line')
        url = f'https://registry.npmjs.org/{repo}/-/{repo.split("/")[-1]}-{v}.tgz'
        return {DEFINITIONS[name][1][0]: v, DEFINITIONS[name][1][1]: read(url, digest=True)}
    if name == 'electrum':
        # The official published download index excludes unpublished Git tags.
        candidates = re.findall(r'href="(\d+\.\d+\.\d+)/"', read('https://download.electrum.org/'))
        candidates = [v for v in candidates if entry['release_line'] == 'stable' or v.startswith(entry['release_line'] + '.')]
        v = max(candidates, key=numeric)
        return {'ELECTRUM_VERSION': v, 'ELECTRUM_SHA256': read(f'https://download.electrum.org/{v}/electrum-{v}-x86_64.AppImage', digest=True),
                'ELECTRUM_KEYS_COMMIT': get(f'repos/{repo}/commits/{v}')['sha']}
    if name == 'solana':
        metadata = read('https://release.anza.xyz/stable/solana-release-x86_64-unknown-linux-gnu.yml')
        matches = re.findall(r'^commit: ([0-9a-f]{40})$', metadata, re.M)
        if len(matches) != 1:
            raise ValueError('Missing Anza stable commit')
        doc = get(f'repos/{repo}/contents/Cargo.toml?ref={matches[0]}')
        version = tomllib.loads(base64.b64decode(doc['content']).decode())['workspace']['package']['version']
        numeric(version)
        release = get(f'repos/{repo}/releases/tags/v{version}')
    elif entry['release_line'] == 'stable':
        release = get(f'repos/{repo}/releases/latest')
    else:
        releases = get(f'repos/{repo}/releases?per_page=100')
        candidates = [r for r in releases if not r['draft'] and not r['prerelease'] and
                      re.fullmatch(r'v?\d+\.\d+\.\d+(?:-beta)?', r['tag_name']) and
                      r['tag_name'].removeprefix('v').startswith(entry['release_line'] + '.')]
        release = max(candidates, key=lambda r: numeric(r['tag_name']))
    if release.get('draft') or release.get('prerelease'):
        raise ValueError('Only published regular releases are allowed')
    tag = release['tag_name']
    if name == 'fugu':
        if not re.fullmatch('[A-Za-z0-9][A-Za-z0-9._-]*', tag) or re.search(r'nightly|alpha|beta|(?:^|[-.])rc[.0-9-]', tag, re.I):
            raise ValueError('Invalid regular Fugu release')
        commit = get(f'repos/{repo}/commits/{tag}')['sha']
        tree = get(f'repos/{repo}/git/trees/{commit}?recursive=1')
        paths = {f['path'] for f in tree.get('tree', [])}
        required = {'configs/files/fugu.json', 'configs/formats/modern/files/fugu.config.toml',
                    'configs/injects/model_providers.sakana.toml', 'scripts/codex-fugu'}
        if tree.get('truncated') or not required <= paths:
            raise ValueError('Fugu release lacks required image files')
        return {'FUGU_VERSION': tag, 'FUGU_COMMIT': commit}
    numeric(tag)
    if name != 'lnd' and '-beta' in tag:
        raise ValueError('Beta release outside LND regular release naming')
    v = tag.removeprefix('v')
    if entry['release_line'] != 'stable' and not v.startswith(entry['release_line'] + '.'):
        raise ValueError('Selected release is outside the configured release line')
    if name == 'geth':
        commit = get(f'repos/{repo}/commits/{tag}')['sha']
        if not re.fullmatch('[0-9a-f]{40}', commit):
            raise ValueError('Invalid Geth release commit')
        short = commit[:8]
        return {'GETH_VERSION': v, 'GETH_COMMIT': short,
                'GETH_SHA256': read(f'https://gethstore.blob.core.windows.net/builds/geth-linux-amd64-{v}-{short}.tar.gz', digest=True)}
    if name == 'solana':
        return {'SOLANA_VERSION': tag, 'SOLANA_SHA256': asset_hash(repo, release, 'solana-release-x86_64-unknown-linux-gnu.tar.bz2', get, read)}
    if name == 'lnd':
        return {'LND_VERSION': tag, 'LND_SHA256': asset_hash(repo, release, f'lnd-linux-amd64-{tag}.tar.gz', get, read)}
    if name in {'uv', 'cloudflared', 'webhook'}:
        asset = {'uv': 'uv-installer.sh', 'cloudflared': 'cloudflared-linux-amd64', 'webhook': 'webhook-linux-amd64.tar.gz'}[name]
        return {DEFINITIONS[name][1][0]: v, DEFINITIONS[name][1][1]: asset_hash(repo, release, asset, get, read)}
    return {'BIP39_VERSION': tag, 'BIP39_SHA256': asset_hash(repo, release, 'bip39-standalone.html', get, read)}


def plan(root=ROOT, get=github, read=fetch):
    policy_text, conf = (root / POLICY).read_text(), (root / CONF).read_text()
    policy = json.loads(policy_text)
    if policy.get('schema_version') != 1 or policy.get('image') != 'fedora45-ai-core-pre' or not policy.get('entries'):
        raise ValueError('Invalid build dependency whitelist')
    seen, changes, selected = set(), [], []
    updated = copy.deepcopy(policy)
    after = conf
    for entry in updated['entries']:
        name = entry['id']
        if name not in DEFINITIONS or name in seen or entry.get('repository') != DEFINITIONS[name][0]:
            raise ValueError('Unknown or duplicate build dependency')
        seen.add(name)
        if not re.fullmatch(r'stable|\d+(?:\.\d+)?', entry.get('release_line', '')):
            raise ValueError('Invalid release line')
        validate_pins(name, entry['pins'])
        for key, value in entry['pins'].items():
            if re.findall(r'^' + key + r'=(.*)$', conf, re.M) != [value]:
                raise ValueError('Whitelist/build.conf pin drift: ' + key)
        wanted = resolve(entry, get, read)
        validate_pins(name, wanted)
        key = DEFINITIONS[name][1][0]
        if name != 'fugu' and numeric(wanted[key]) < numeric(entry['pins'][key]):
            raise ValueError('Refusing dependency downgrade: ' + name)
        # Same release with changed bytes is not an ordinary upgrade.
        if wanted[key] == entry['pins'][key] and wanted != entry['pins']:
            raise ValueError('Published release bytes changed: ' + name)
        selected.append({'id': name, 'release_line': entry['release_line'], 'pins': wanted})
        if wanted != entry['pins']:
            changes.append({'id': name, 'before': entry['pins'], 'after': wanted})
        for key, value in wanted.items():
            after, count = re.subn(r'^' + key + r'=.*$', key + '=' + value, after, flags=re.M)
            if count != 1:
                raise ValueError('Missing or duplicate build pin')
        entry['pins'] = wanted
    report = {'schema_version': 1, 'status': 'PASS', 'image': policy['image'], 'required': bool(changes),
              'policy_sha256': hashlib.sha256(policy_text.encode()).hexdigest(), 'selected': selected, 'changes': changes}
    return report, {CONF: after, POLICY: json.dumps(updated, indent=2) + '\n'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--apply', action='store_true', help='Write pins on the GitHub preparation runner only')
    parser.add_argument('--emit', action='store_true', help='Validate and emit saved pins without network access')
    args = parser.parse_args()
    if args.emit:
        policy = json.loads((args.root / POLICY).read_text())
        conf = (args.root / CONF).read_text()
        seen = set()
        for entry in policy['entries']:
            if entry['id'] in seen:
                raise ValueError('Duplicate build dependency')
            seen.add(entry['id'])
            validate_pins(entry['id'], entry['pins'])
            for key, value in entry['pins'].items():
                if re.findall(r'^' + key + r'=(.*)$', conf, re.M) != [value]:
                    raise ValueError('Whitelist/build.conf pin drift: ' + key)
                print(key + '=' + value)
        return
    report, files = plan(args.root)
    if args.apply:
        import os
        if os.environ.get('GITHUB_ACTIONS') != 'true':
            raise SystemExit('Pin application belongs to GitHub Actions')
        for path, content in files.items():
            (args.root / path).write_text(content)
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
