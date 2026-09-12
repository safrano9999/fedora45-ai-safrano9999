# fedora45-ai-safrano9999-full

Optional layer on `fedora45-ai-safrano9999`. Adds only VikAI, including
its agent bootstrap and owner environment examples. Normal Safrano deployments
use the parent image. Full does not need a Smart1 `images.json` entry.

The cumulative example triple inherits Safrano and merges VikAI's examples.
`setup.sh` stores instances in `CONTAINER/<name>/`.
