# Site layout

Every URL the update center serves, and what answers it.

The web container serves the live release (`/data/live`, a link to `/data/releases/<id>/`). Each
release is one complete site with the same layout as updates.jenkins.io.

## Top level

| Path | What it is |
|---|---|
| `/update-center.json` | The signed metadata Jenkins reads, as JSONP. Without `?version=` it redirects to `/current/update-center.json`; with it, to a tier (see [Version routing](#version-routing)). |
| `/update-center.actual.json` | The same metadata as plain JSON, routed the same way. |
| `/plugin-versions.json` | Every version of every plugin, for `jenkins-plugin-cli`. Redirects to `/current/`. |
| `/release-history.json` | Plugin releases by date. Redirects to `/current/`. |
| `/plugin-documentation-urls.json` | Documentation links per plugin. Redirects to `/current/`. |
| `/latestCore.txt` | The newest core version offered. Redirects to `/current/`. Absent when the repository holds no core. |
| `/latest/` | Links to the newest release of each plugin and of the core, for example `/latest/git.hpi`. |
| `/current/` | The site for the newest core: every plugin at its newest version. |
| `/stable/` | A link to the newest LTS tier, `dynamic-stable-<version>/`, or to `current/` when there is none. |
| `/experimental/` | The site including alpha and beta releases. |
| `/download/` | Index pages of every plugin and core version, and the download routes (see [Downloads](#downloads)). |
| `/updates/` | Tool installer metadata (JDK, Maven, Gradle, NodeJS and others), signed by the Jenkins project. Present when `TOOL_INSTALLERS_URL` is set. |
| `/tiers.json` | The weekly and LTS core versions that have a tier in this release (`weeklyCores`, `stableCores`). |
| `/uctest.json` | `{}`: Jenkins' connection check, and the health endpoint. |
| `/status.json` | The update center's own [status](status-json.md). Not part of the release. |
| `/` | A short `index.html`. |

## Tiers

A tier is a complete site for one core version, offering only the plugin versions that core can run:

- `dynamic-<version>/` for weekly cores, for example `dynamic-2.540/`;
- `dynamic-stable-<version>/` for LTS cores, for example `dynamic-stable-2.528.1/`.

The generator derives the tier list from the core versions plugin releases declare as their minimum
and whose `.war` the repository holds; each LTS tier also brings the next weekly tier, and each
weekly one the next LTS release in the repository, limited by `CORE_AGE_DAYS`. See
[Step 4: Tiers](../architecture/generation-pipeline.md#step-4-tiers). A repository with no core
produces no tiers: every request goes to `current/`.

## Version routing

Jenkins sends its own version: `/update-center.json?version=<version>`. Upstream's `.htaccess` rules,
unchanged, redirect it to the newest tier of the same kind at or below that version: weekly tiers
for `2.x` versions, LTS tiers for `2.x.y`. Examples from this project's routing tests, which use
upstream's own tier list (weekly tiers 2.172 to 2.240, LTS tiers 2.164.2 to 2.222.1):

| Request | Redirected to |
|---|---|
| `/update-center.json?version=2.246` | `/dynamic-2.240/update-center.json` |
| `/update-center.json?version=2.225` | `/dynamic-2.223/update-center.json` |
| `/update-center.json?version=2.204.3` | `/dynamic-stable-2.204.2/update-center.json` |
| `/update-center.json?version=2.46.1` | `/dynamic-stable-2.164.2/update-center.json` (older than every tier: the oldest LTS tier) |
| `/update-center.json` | `/current/update-center.json` |

A `-SNAPSHOT` suffix routes like the release it names. How the rules work:
[Version routing](../architecture/request-routing.md#version-routing).

## Downloads

Download URLs in the signed metadata point at `$PUBLIC_URL/download/`:

| Path | Answer |
|---|---|
| `/download/plugins/<id>/<version>/<id>.hpi` | The plugin, from the download backend. |
| `/download/war/<version>/jenkins.war` | The core, from the download backend. |
| `/download/plugins/<id>/latest/<id>.hpi` | Redirect to `/latest/<id>.hpi`, then to the newest version. |
| `/download/war/latest/jenkins.war` | Redirect to `/latest/jenkins.war`, then to the newest core. |
| `/download/plugins/<id>/`, `/download/war/` | Index pages listing every version. |

"From the download backend" means a 302 redirect with `DOWNLOAD_ACCESS=redirect`, or the file
streamed through the web container with `DOWNLOAD_ACCESS=proxy`. A version the backend does not have
returns 404. See [Downloads](../architecture/request-routing.md#downloads).

## Health and status endpoints

| Path | Use |
|---|---|
| `/uctest.json` | Returns `{}` with status 200 once a release is live. The container's own health check uses it; use it for external probes too. |
| `/status.json` | Last run, live release, certificate expiry and data age, with `Cache-Control: no-store`. |
