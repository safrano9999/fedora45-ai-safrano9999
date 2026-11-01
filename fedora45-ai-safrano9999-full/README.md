# fedora45-ai-safrano9999-full

Optional layer on `fedora45-ai-safrano9999`. Adds only VikAI, including
its agent bootstrap and owner environment examples. Normal Safrano deployments
use the parent image. Full does not need a Smart1 `images.json` entry.

The cumulative example triple inherits Safrano and merges VikAI's examples.
`setup.sh` stores instances in `CONTAINER/<name>/`.
On ARM64, `setup.sh --arch arm64` selects
`ghcr.io/safrano9999/fedora45-ai-arm64-full:latest`; on x86_64 it uses the
regular Full image name.
