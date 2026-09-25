# config/overlay/

Files placed here are merged into upstream's `resources/` on every generation
([generation pipeline, step 2](../../docs/architecture/generation-pipeline.md#step-2-resources)).
This README is ignored.

- `*.properties`: your lines are appended after upstream's, and later keys win. For example, to hide
  a plugin or one release of it everywhere, create `artifact-ignores.properties`:

  ```properties
  # suspended internally, see CHANGE-1234
  some-plugin
  git@5.2.0
  ```

- `warnings.json`: a JSON array of warnings, merged by `id` (yours replace upstream's entries with
  the same id). Jenkins shows these on the plugin manager and the admin monitor:

  ```json
  [{"id": "CORP-2026-01", "type": "plugin", "name": "some-plugin",
    "message": "Blocked by security review", "url": "https://wiki.corp.example/CORP-2026-01",
    "versions": [{"lastVersion": "1.4", "pattern": "1[.][0-4](|[.-].*)"}]}]
  ```

- Any other file (for example `index-template.html`, `platform-plugins.json`) replaces upstream's.

How-to guide: [Block plugins and add warnings](../../docs/how-to/block-plugins-and-add-warnings.md).
