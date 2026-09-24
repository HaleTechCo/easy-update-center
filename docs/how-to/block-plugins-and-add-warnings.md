# Block plugins and add warnings

How to hide plugins or releases and publish your own security warnings through the overlay directory.

## How the overlay works

Files in `config/overlay/` are merged into upstream's `resources/` on every generation, after
upstream's own data. The directory is mounted read-only into the `scheduler` and `uc` containers; its
`README.md` is ignored.

- `*.properties`: your lines are appended after upstream's, and later keys win.
- `warnings.json`: a JSON array of warnings, merged by `id`: yours replace upstream's entry with the
  same id, or are added.
- Any other file replaces upstream's file of the same name (for example `index-template.html` or
  `platform-plugins.json`).

Changes apply at the next generation. To apply them now: `docker compose run --rm uc generate`.
`status.json` lists the overlay files applied under `resources.overlay`.

## Hide a plugin or a release

Create `config/overlay/artifact-ignores.properties`. Each line names a plugin id (every version) or
`<id>@<version>` (one release):

```properties
# suspended internally, see CHANGE-1234
some-plugin
git@5.2.0
```

Hidden plugins and releases disappear from every site, as if they were not in the repository.

## Add a security warning

Create `config/overlay/warnings.json` with a JSON array. Jenkins shows each warning on the plugin
manager and in the administrative monitor for affected versions:

```json
[{"id": "CORP-2026-01", "type": "plugin", "name": "some-plugin",
  "message": "Blocked by security review", "url": "https://wiki.corp.example/CORP-2026-01",
  "versions": [{"lastVersion": "1.4", "pattern": "1[.][0-4](|[.-].*)"}]}]
```

Every warning needs an `id`, and ids must be unique within your file; the run fails otherwise. Use
your own prefix (such as `CORP-`) so your ids never collide with upstream's `SECURITY-` ids, unless
you mean to replace one.

## Replace an upstream file

Any other file in `config/overlay/` replaces upstream's file of the same name in `resources/`
wholesale; for example, `platform-plugins.json` changes the plugins Jenkins suggests in its setup
wizard. The upstream files are listed in update-center2's
[`resources/` directory](https://github.com/jenkins-infra/update-center2/tree/master/resources).
