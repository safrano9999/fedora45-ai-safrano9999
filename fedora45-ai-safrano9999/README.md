# fedora45-ai-safrano9999

Built on `fedora45-ai-kachelmann`, inheriting Base and the KACHELMANN
services and APK. Adds the repositories listed in `build.conf`; contains no
VikAI bootstrap, service, or environment defaults. Optional VikAI belongs to
`fedora45-ai-safrano9999-full`.

Cumulative examples merge this layer's additional triple, the KACHELMANN
parent triple, and the owner examples in `fedora45-ai-safrano9999-additional.repos`.
`setup.sh` uses those files and stores instances in `CONTAINER/<name>/`.
