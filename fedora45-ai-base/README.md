# fedora45-ai-base

`ghcr.io/safrano9999/fedora45-ai-base` is built directly from
`fedora45-ai-core`. The Containerfile declares each current Git source directly with `ADD`.
`EXTENSIONS` and `STANDALONE` in `build.conf` describe their runtime roles. `EXTENSIONS` contains OpenClaw
manifest repositories; `STANDALONE` contains complete application repositories.

`build/assemble-runtime.sh` runs inside the source stage on the GitHub runner.
It consumes the Git sources already fetched by BuildKit, records their commits, merges their Python
requirements, and directly merges each owner's rootfs-shaped `image/runtime/`
tree. Unsafe paths, symlinks, unsupported types, permission conflicts, and
cross-repository file collisions fail preparation. The resulting file manifest
and systemd enable list are included in the image. An owner may contribute the
same generic `image/buildtime/host/run` or `image/buildtime/container/run`
entrypoint as any other repository; those hooks receive only the common
buildtime environment. NEXTCLOUD's separate Fedora runtime plugin remains an
authenticated, checksummed release asset.

The Containerfile retains `safrano9999-paper`, links its `paper.pdf` into
`/README`, and runs every installed repository's generic container-buildtime
entrypoint. Applied `image/runtime/` trees are removed afterward;
`image/buildtime/` remains available to downstream image layers so the same
owner hooks can consume each layer's cumulative examples.

The cumulative `fedora45-ai-base.*_example` triple is generated from the
Base additional triple, the current Core triple, and the repositories listed
in `fedora45-ai-base-additional.repos`. `setup.sh` uses those local files
directly and keeps generated instances below `CONTAINER/<name>/`; it performs
no example download or merge. Use `--config-only` to render instances. Image builds use GitHub Actions;
host image pulls use Smart1.
