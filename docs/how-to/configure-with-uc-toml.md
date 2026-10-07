# Configure with uc.toml

How to keep the update center's settings in `config/uc.toml` instead of, or as well as, `.env`, and
check what each one ends up as.

Every setting can go in either place; a variable in `.env` wins over the file. The rules:
[How settings are read](../reference/configuration.md#how-settings-are-read).

## Start from the example

```bash
cp config/uc.example.toml config/uc.toml
```

The example lists every setting the file can hold, commented out, each showing its default or an
example value, and the comment above it says what applies when it is unset. Set `mode` and
`public_url`, then uncomment what you need. For an air-gapped starting point, generate one instead:

```bash
docker compose run --rm --no-deps uc config --example toml --mode airgapped > config/uc.toml
```

`config/uc.toml` is gitignored. The containers read it as uid 10001, so keep it readable by everyone
(`chmod 644`); it holds no secrets, since credentials stay in `secrets/`.

## Move settings out of .env

Each variable has a key in the file: a variable whose name starts with a section's name and an
underscore goes under that section, the rest of its name in lower case; any other is a top-level
key.
This `.env`:

```bash
MODE=airgapped
PUBLIC_URL=https://jenkins-updates.corp.example
REPO_TYPE=artifact-keeper
REPO_URL=https://artifacts.corp.example/
REPO_KEY=jenkins-plugins-local
CORE_AGE_DAYS=0
GENERATE_SCHEDULE=0 */4 * * *
```

becomes:

```toml
mode = "airgapped"
public_url = "https://jenkins-updates.corp.example"
core_age_days = 0

[generate]
schedule = "0 */4 * * *"

[repo]
type = "artifact-keeper"
url = "https://artifacts.corp.example/"
key = "jenkins-plugins-local"
```

Then remove those lines from `.env`: a variable still set there wins. Keep `COMPOSE_FILE`,
`UC_ENV_FILE`, `UC_IMAGE`, `WEB_IMAGE`, `HTTP_PORT` and `HTTPS_PORT` in `.env`: Docker Compose reads
them before any container starts.

## Check the result

```bash
docker compose run --rm --no-deps uc config
```

It lists every setting with its value and where it came from: `environment`, `uc.toml: <key>`,
`mode`, `derived` or `default`. A mistake in the file, such as an unknown or misplaced key, stops it
with the right spelling. `uc config --check` only validates, for a script or a pipeline.

## Apply the changes

```bash
docker compose up -d --force-recreate      # or: make reload
```

`make reload` checks the settings first and recreates nothing when they are invalid. Each run reads
the file afresh, but the schedules, the web container's settings and the repo-adapter's are read
when those services start:
[When changes apply](../reference/configuration.md#when-changes-apply).
