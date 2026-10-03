"""Verify the exact approved OCI input without downloading or building images."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('fedora_beta', ROOT / '.github/scripts/prepare-fedora-beta.py')
beta = importlib.util.module_from_spec(spec)
spec.loader.exec_module(beta)


class FedoraBetaTests(unittest.TestCase):
    def fixture(self, architecture='amd64'):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        root = Path(scratch.name)
        (root / 'blobs/sha256').mkdir(parents=True)
        def put(payload):
            data = json.dumps(payload).encode()
            digest = hashlib.sha256(data).hexdigest()
            (root / 'blobs/sha256' / digest).write_bytes(data)
            return {'digest': 'sha256:' + digest, 'size': len(data)}
        config = put({'os': 'linux', 'architecture': architecture})
        layer = put({'synthetic-layer': True})
        manifest = put({'schemaVersion': 2, 'config': config, 'layers': [layer]})
        (root / 'oci-layout').write_text('{"imageLayoutVersion":"1.0.0"}')
        (root / 'index.json').write_text(json.dumps({'manifests': [manifest]}))
        return root, manifest['digest'], layer

    def test_approved_layout_checks_config_and_every_layer(self):
        root, digest, layer = self.fixture()
        beta.validate_layout(root, digest)
        (root / 'blobs/sha256' / layer['digest'].split(':')[1]).write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'checksum/size'):
            beta.validate_layout(root, digest)

    def test_wrong_architecture_and_wrong_manifest_are_rejected(self):
        root, digest, _ = self.fixture('arm64')
        with self.assertRaisesRegex(ValueError, 'amd64'):
            beta.validate_layout(root, digest)
        with self.assertRaisesRegex(ValueError, 'pinned manifest'):
            beta.validate_layout(root, 'sha256:' + '0' * 64)

    def test_tar_links_and_parent_paths_are_rejected(self):
        for name, link in (('../outside', False), ('inside', True)):
            with self.subTest(name=name):
                root, _, _ = self.fixture()
                source = root / 'test.tar'
                with tarfile.open(source, 'w') as archive:
                    member = tarfile.TarInfo(name)
                    if link:
                        member.type, member.linkname = tarfile.SYMTYPE, '/etc/passwd'
                    archive.addfile(member, io.BytesIO(b''))
                with self.assertRaisesRegex(ValueError, 'Unsafe'):
                    beta.extract_archive(source, root / 'output')

    def test_production_source_is_certified_beta_not_a_daily_registry_tag(self):
        policy = json.loads((ROOT / 'upgrade-loop/build-dependencies-whitelist.json').read_text())
        pin, = policy['base_images']
        self.assertEqual(pin['release_line'], '45_Beta-1.3')
        self.assertEqual(pin['source_archive']['sha256'], 'c9bacf3e467932a035233b123836ddee11bdcede60a9f8bb8d9d6560fae1e5b8')
        self.assertEqual(pin['digest'], 'sha256:5f6c3c965a3af6b0faa21efc46ab46b9acfb1e1a8f69cec63fd6e44bdcf270cb')
        self.assertEqual((ROOT / 'fedora45-ai-core-pre/Containerfile').read_text().splitlines()[0],
                         'FROM fedora45-approved-beta AS ai-core-pre')


if __name__ == '__main__':
    unittest.main()
