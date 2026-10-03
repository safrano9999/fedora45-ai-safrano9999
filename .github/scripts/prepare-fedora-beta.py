#!/usr/bin/env python3
# Source of truth: SCRIPTS/githubactions. Generated copies are overwritten.
"""Verify the approved Fedora OCI compose and expose a digest-pinned build context."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys
import tarfile
import tempfile
from urllib.request import urlopen


def extract_archive(archive, destination):
    with tarfile.open(archive) as source:
        for member in source.getmembers():
            path = PurePosixPath(member.name)
            if path.is_absolute() or '..' in path.parts or not (member.isfile() or member.isdir()):
                raise ValueError('Unsafe Fedora OCI archive member')
        source.extractall(destination, filter='data')


def validate_layout(layout, digest):
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', digest):
        raise ValueError('Invalid Fedora manifest digest')
    if json.loads((layout / 'oci-layout').read_text()).get('imageLayoutVersion') != '1.0.0':
        raise ValueError('Expected an OCI image layout')
    index = json.loads((layout / 'index.json').read_text())
    if not any(row.get('digest') == digest for row in index.get('manifests', [])):
        raise ValueError('Fedora archive does not contain the pinned manifest')

    def blob(reference):
        checksum = reference.get('digest', '')
        if not re.fullmatch(r'sha256:[0-9a-f]{64}', checksum):
            raise ValueError('Invalid OCI blob digest')
        path = layout / 'blobs/sha256' / checksum.split(':')[1]
        with path.open('rb') as data:
            actual = hashlib.file_digest(data, 'sha256').hexdigest()
        if actual != checksum.split(':')[1] or (reference.get('size') is not None and path.stat().st_size != reference['size']):
            raise ValueError('Fedora OCI blob checksum/size mismatch')
        return path

    manifest = json.loads(blob({'digest': digest}).read_text())
    if manifest.get('schemaVersion') != 2:
        raise ValueError('Invalid Fedora image manifest')
    config = json.loads(blob(manifest['config']).read_text())
    if (config.get('os'), config.get('architecture')) != ('linux', 'amd64'):
        raise ValueError('Expected the Linux amd64 Fedora Beta compose')
    if not manifest.get('layers'):
        raise ValueError('Fedora image has no layers')
    for layer in manifest['layers']:
        blob(layer)


def main():
    if os.environ.get('GITHUB_ACTIONS') != 'true':
        raise SystemExit('Fedora image preparation runs only on GitHub Actions')
    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root / 'upgrade-loop'))
    from build_dependencies import base_binding
    policy = json.loads((root / 'upgrade-loop/build-dependencies-whitelist.json').read_text())
    selected = [row for row in policy['base_images'] if row.get('source_archive')]
    if len(selected) != 1:
        raise ValueError('Expected exactly one approved Fedora Beta archive')
    pin = selected[0]
    base_binding(root, policy, pin)
    directory = Path(tempfile.mkdtemp(prefix='fedora-approved-beta-', dir=os.environ['RUNNER_TEMP']))
    archive = directory / 'fedora.oci.tar.xz'
    source = pin['source_archive']
    checksum = hashlib.sha256()
    size = 0
    with urlopen(source['url'], timeout=90) as response, archive.open('wb') as output:
        if response.url != source['url']:
            raise ValueError('Unexpected Fedora archive redirect')
        while data := response.read(1024 * 1024):
            size += len(data)
            if size > 512 * 1024 * 1024:
                raise ValueError('Fedora archive is unexpectedly large')
            checksum.update(data)
            output.write(data)
    if checksum.hexdigest() != source['sha256']:
        raise ValueError('Approved Fedora archive checksum mismatch')
    layout = directory / 'layout'
    layout.mkdir()
    extract_archive(archive, layout)
    validate_layout(layout, pin['digest'])
    with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
        output.write('context=' + pin['image'] + '=oci-layout://' + str(layout) + '@' + pin['digest'] + '\n')
    print('Verified Fedora ' + pin['release_line'] + ' archive and ' + pin['digest'])


if __name__ == '__main__':
    main()
