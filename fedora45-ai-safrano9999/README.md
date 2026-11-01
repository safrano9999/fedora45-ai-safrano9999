# fedora45-ai-safrano9999

Built on `fedora45-ai-kachelmann`, inheriting Base and the KACHELMANN
services and APK. Adds the repositories listed in `build.conf`; contains no
VikAI bootstrap, service, or environment defaults. Optional VikAI belongs to
`fedora45-ai-safrano9999-full`.

Cumulative examples merge this layer's additional triple, the KACHELMANN
parent triple, and the owner examples in `fedora45-ai-safrano9999-additional.repos`.
`setup.sh` uses those files and stores instances in `CONTAINER/<name>/`.
On ARM64, `setup.sh --arch arm64` selects
`ghcr.io/safrano9999/fedora45-ai-arm64-safrano9999:latest`; on x86_64 it uses
the regular image name.
