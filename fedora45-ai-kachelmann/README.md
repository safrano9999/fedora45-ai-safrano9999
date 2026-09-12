# fedora45-ai-kachelmann

`ghcr.io/safrano9999/fedora45-ai-kachelmann` is a private image built directly
from `fedora45-ai-base`. It adds only the KACHELMANN repository, its Python
requirements, and its owner-maintained `image/runtime/` rootfs overlay.
`EXTENSIONS=KACHELMANN` and the empty `STANDALONE` value in `build.conf` are the
sole repository source of truth. OpenClaw discovery is delegated to the shared
Ephemeral integrator. Preparation rejects unsafe paths, types, symlinks,
permissions, and overlay collisions, then emits the runtime file manifest and
systemd enable list directly from that exact checkout.

Optional `image/buildtime/host/run` and `image/buildtime/container/run`
entrypoints follow the same generic owner contract as every other repository.
Inherited container hooks are rerun against Kachelmann's cumulative example
triple without any repository-specific call in this layer.

The cumulative `fedora45-ai-kachelmann.*_example` triple is generated from the
Kachelmann additional triple, the current Base triple, and KACHELMANN's latest
example asset. `setup.sh` uses those local files directly. Generated instances
live below `CONTAINER/<name>/`; examples are symlinked and shared setup files
are hardlinked.

Markdown, uploaded documents, and image assets are stored in the configured
KACHELMANN database: PostgreSQL uses `BYTEA`, MariaDB/MySQL uses `LONGBLOB`,
and SQLite uses `BLOB`. There is no separate content volume or content-path
configuration. With SQLite selected, setup retains only the existing dedicated
KACHELMANN SQLite database volume. The external `kachelmann-mcp` sidecar uses
the same database service, credentials, and table prefix for the networked
backends and remains fully ephemeral. SQLite stays local to the Fedora main
container under the no-shared-volume contract. The application and plugin code
remain immutable in the image.

Use `./setup.sh` for an instance, `./build/build-local.sh` for a local image, or the
`fedora45-ai-kachelmann-image.yml` workflow for the private GHCR image.
