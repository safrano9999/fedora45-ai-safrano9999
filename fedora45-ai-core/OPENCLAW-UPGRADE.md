# OpenClaw-Upgrade in `fedora45-ai-core`

Diese Datei ist ein Runbook für das nächste OpenClaw-Upgrade. Sie ändert den
aktuellen Build nicht. `fedora45-ai-core` übernimmt kein fertiges
Ephemeral-Image, sondern ergänzt den generischen Fedora-Unterbau aus
`fedora45-ai-core-pre` um vier zueinander passende Bestandteile
zusammen:

1. das exakte OpenClaw-NPM-Paket,
2. das dazugehörige Deterministic-Patch-Archiv,
3. einen dazu kompatiblen Commit von `openclaw-ephemeral`,
4. eine kompatible NOTE-Version.

Diese Pins dürfen nur gemeinsam aktualisiert werden.

## Änderungen im `Containerfile`

### 1. OpenClaw-Version aktualisieren

Den Default des Build-Arguments anheben:

```Dockerfile
ARG OPENCLAW_VERSION=<NEUE_OPENCLAW_VERSION>
```

`OPENCLAW_NPM_INTEGRITY` bleibt ein erforderliches Build-Argument. Der Wert
muss exakt zu `openclaw@<NEUE_OPENCLAW_VERSION>` aus der NPM-Registry passen.
Die vorhandene Integritätsprüfung und die Versionsprüfung nach
`npm install -g` bleiben erhalten.

### 2. Kanonische OpenClaw-Pfade verwenden

Ab Beta 5 darf `OPENCLAW_CONFIG` nicht mehr als aktiver Konfigurationspfad
verwendet werden. Den OpenClaw-Teil des `ENV`-Blocks auf folgende Defaults
umstellen:

```Dockerfile
ENV HOME=/root \
    OPENCLAW_CONFIG_DIR=/root/.openclaw \
    OPENCLAW_STATE_DIR=/root/.openclaw \
    OPENCLAW_CONFIG_PATH=/root/.openclaw/openclaw.json \
    OPENCLAW_WORKSPACE_DIR=/root/.openclaw/workspace \
    OPENCLAW_GATEWAY_PORT=18789 \
    OPENCLAW_DISABLE_BONJOUR=1 \
    PYTHONPATH=/usr/local/lib/openclaw-ephemeral \
    PYTHONUNBUFFERED=1
```

`OPENCLAW_CONFIG_DIR` bleibt nur für unsere Installations- und Layer-Skripte
erhalten. OpenClaw selbst und `openclaw-ephemeral` verwenden
`OPENCLAW_CONFIG_PATH`.

Die Image-Defaults zeigen absichtlich nach `/root/.openclaw`, damit
`openclaw plugins install` während des Image-Builds keine Laufzeit-Volumes
beschreibt. Die persistenten Laufzeitwerte werden später über die
Container-Konfiguration gesetzt.

### 3. Passendes Deterministic-Archiv einbauen

Das Patch-Archiv muss aus exakt derselben OpenClaw-Version stammen wie das
NPM-Paket. Folgende Werte werden gemeinsam ersetzt:

```text
OPENCLAW_DETERMINISTIC_REPOSITORY
OPENCLAW_DETERMINISTIC_TAG
OPENCLAW_DETERMINISTIC_ASSET
OPENCLAW_DETERMINISTIC_SHA256
```

Der aktuell hart kodierte Dateiname in dieser Zeile muss ebenfalls auf den
neuen Assetnamen geändert werden:

```Dockerfile
COPY build/vendor/openclaw-deterministic/<NEUES_ASSET>.tar.gz /tmp/openclaw-deterministic.tar.gz
```

Besser ist es, bei diesem Upgrade die harte Dateinamensbindung einmalig zu
entfernen:

```Dockerfile
COPY build/vendor/openclaw-deterministic/ /tmp/openclaw-deterministic/

RUN set -euo pipefail; \
    artifact="/tmp/openclaw-deterministic/${OPENCLAW_DETERMINISTIC_ASSET}"; \
    printf '%s  %s\n' "${OPENCLAW_DETERMINISTIC_SHA256}" "$artifact" | sha256sum -c -; \
    openclaw_root="$(npm root -g)/openclaw"; \
    test "$(jq -r .version "$openclaw_root/package.json")" = "${OPENCLAW_VERSION}"; \
    cp -a "$openclaw_root/dist/control-ui" /tmp/openclaw-control-ui; \
    rm -rf "$openclaw_root/dist"; \
    tar -xzf "$artifact" -C "$openclaw_root"; \
    mv /tmp/openclaw-control-ui "$openclaw_root/dist/control-ui"; \
    rm -rf /tmp/openclaw-deterministic; \
    test -s "$openclaw_root/dist/control-ui/index.html"; \
    node "$openclaw_root/openclaw.mjs" --version | grep -F "${OPENCLAW_VERSION}"
```

Damit genügt bei späteren Upgrades der Asset-Pin in `build.conf`.

### 4. Lokale Media-Roots unverändert lassen

Das Containerfile darf OpenClaws gebündelte `local-roots-*.js` nicht verändern
und insbesondere `/` nicht als globalen Media-Root ergänzen. Plugins legen
ausgehende Dateien über OpenClaws Media-Store ab; dessen dynamisch ermittelte
State-, Workspace- und Temp-Verzeichnisse reichen für die Auslieferung aus.

### 5. Passenden Ephemeral-Commit übernehmen

`OPENCLAW_EPHEMERAL_COMMIT` muss auf einen Commit zeigen, der die neue
OpenClaw-Konfiguration unterstützt. Für Beta 5 oder neuer muss dieser Commit
mindestens Folgendes enthalten:

- `OPENCLAW_CONFIG_PATH` als kanonischen Config-Pfad,
- getrennte Behandlung von State, Config und Workspace,
- explizite Aufnahme aller Verzeichnisse mit `openclaw.plugin.json` in
  `plugins.load.paths`,
- das zum neuen OpenClaw-Schema passende Agent-/Model-/Plugin-Format.

Nur die im `build/prepare-build-context.sh` aufgelisteten Ephemeral-Dateien werden in
den Build-Kontext übernommen. Kommt beim Upgrade eine neue Runtime-Datei dazu,
muss sie dort ergänzt und die Prüfung auf die exakte Dateianzahl angepasst
werden.

### 6. NOTE gegen die neue Plugin-API prüfen

NOTE benötigt keine Änderung, solange OpenClaw es mit explizitem Load-Pfad als
`loaded` importiert und `/note` sowie `before_agent_reply` registriert. Falls
eine neue NOTE-Version benötigt wird, diese vier Werte gemeinsam aktualisieren:

```text
NOTE_REPOSITORY
NOTE_RELEASE_TAG
NOTE_RELEASE_ASSET
NOTE_RELEASE_SHA256
```

Die Installation im Containerfile muss danach weiterhin Manifest,
Python-Umgebung und OpenClaw-Version verifizieren.

## Zwingende Begleitänderungen außerhalb des `Containerfile`

Die Werte in folgenden Dateien sind teilweise absichtlich hart gepinnt und
müssen beim Upgrade synchron geändert werden:

- `build.conf`
- `../fedora45-ai-core-pre/build.conf`, wenn sich die OpenClaw-Basisversion ändert
- `build/resolve-build-inputs.sh`
- `build/prepare-build-context.sh`, falls sich Asset- oder Runtime-Dateien ändern
- `.github/workflows/fedora45-ai-core-image.yml`
- `.github/workflows/fedora45-ai-core-pre-image.yml`, wenn Core-pre neu gebaut wird
- Source-of-Truth des Workflows unter
  `SCRIPTS/githubactions/fedora45-ai-safrano9999/workflows/`

Für Beta 5 oder neuer außerdem die bisherige OpenClaw-Symlink-Persistenz
entfernen:

- keine Links von `/root/.openclaw/workspace` oder
  `/root/.openclaw/agents` erzeugen,
- das Named Volume direkt nach `/named_volumes/OPENCLAW` mounten,
- zur Laufzeit setzen:

```text
OPENCLAW_STATE_DIR=/named_volumes/OPENCLAW
OPENCLAW_WORKSPACE_DIR=/named_volumes/OPENCLAW/workspace
```

`OPENCLAW_CONFIG_PATH=/root/.openclaw/openclaw.json` bleibt absichtlich
ephemeral und wird bei jedem Start neu erzeugt.

Auch die Plugin-Registrierung in den nachgelagerten Fedora-Layern darf
`plugins.load.paths` nicht wieder entfernen. Gefundene Plugin-Verzeichnisse
werden idempotent hinzugefügt.

## Reihenfolge für das Upgrade

1. Neue OpenClaw-Version und NPM-Integrität festlegen.
2. Passendes Deterministic-Release bauen und dessen SHA-256 pinnen.
3. Kompatiblen Ephemeral-Commit und gegebenenfalls NOTE-Release pinnen.
4. `Containerfile`, `build.conf`, Workflow-Pins und statische Checks gemeinsam
   aktualisieren.
5. Build-Kontext neu erzeugen.
6. `fedora45-ai-core` bauen und prüfen, dass OpenClaw exakt die erwartete
   Version meldet.
7. Beim ersten Lauf mit bestehendem Named Volume die SQLite-Migration offline
   mit gestopptem Gateway durchführen.
8. Danach die Fedora-Kaskade `core-pre -> core -> base -> safrano9999` bauen.

## Laufzeit-Abnahme

Nach dem Upgrade müssen mindestens folgende Punkte erfüllt sein:

- Gateway meldet `ready` und `/healthz` ist `live`.
- Keine Meldung `plugin not found` oder `stale config entry`.
- NOTE erscheint in der geladenen Pluginliste und eine normale Nachricht wird
  deterministisch gespeichert.
- Die nachgelagerten Plugins erscheinen ebenfalls in der geladenen
  Pluginliste.
- Workspace und SQLite liegen im Named Volume; `openclaw.json` liegt weiterhin
  unter `/root/.openclaw`.
- Neustarts erzeugen keine Symlink- oder Legacy-Workspace-Migrationsschleife.
