import json
from pathlib import Path

import pytest
from sitefixture import TEST_CERT_B64, write_generator_output

from uc.layout import finalize_layout
from uc.model import Tiers
from uc.validate import ValidationError, count_plugins, download_key, load_site_json, validate_release

URL = "https://uc.example.com"
ROOT = URL + "/download"
TIERS = Tiers(("2.540",), ("2.528.1",))
TEMPLATES = Path("/opt/uc/templates")
OFFERED = {"plugins/git/5.2.0", "plugins/credentials/1415.v831096eb_5534", "war/2.528.1"}


def build(staging, tiers=TIERS, public_url=URL, **doc_options):
    write_generator_output(staging, public_url, tiers, **doc_options)
    finalize_layout(staging, tiers, TEMPLATES)
    (staging / ".htaccess").write_text("RewriteEngine on\n")
    (staging / ".htdownload-map").write_text("")
    (staging / "tiers.json").write_text(json.dumps({"weeklyCores": list(tiers.weekly), "stableCores": list(tiers.stable)}))
    return staging


def validate(staging, **overrides):
    options = dict(tiers=TIERS, downloads_root=ROOT, signing_cert_b64=TEST_CERT_B64,
                   previous_plugin_count=None, min_plugins=1, min_ratio=0.9, force=False, download_map=None)
    options.update(overrides)
    return validate_release(staging, **options)


def test_valid_release(tmp_path):
    result = validate(build(tmp_path))
    assert result.plugin_count == 2
    assert result.offered == OFFERED


def test_load_site_json_strips_jsonp(tmp_path):
    (tmp_path / "a.json").write_text('updateCenter.post(\n{"a": 1}\n);')
    (tmp_path / "b.json").write_text('{"b": 2}')
    assert load_site_json(tmp_path / "a.json") == {"a": 1}
    assert load_site_json(tmp_path / "b.json") == {"b": 2}


def test_download_key():
    assert download_key(ROOT + "/plugins/git/5.2.0/git.hpi", ROOT) == "plugins/git/5.2.0"
    assert download_key(ROOT + "/war/2.528.1/jenkins.war", ROOT) == "war/2.528.1"
    assert download_key("https://updates.jenkins.io/download/plugins/git/5.2.0/git.hpi", ROOT) is None
    assert download_key(ROOT + "/plugins/git/5.2.0/other.hpi", ROOT) is None


def test_missing_files(tmp_path):
    staging = build(tmp_path)
    (staging / ".htdownload-map").unlink()
    (staging / "current" / "plugin-versions.json").unlink()
    with pytest.raises(ValidationError) as err:
        validate(staging)
    assert "missing .htdownload-map" in err.value.problems
    assert "current/ is missing plugin-versions.json" in err.value.problems


def test_missing_tier_directory(tmp_path):
    staging = build(tmp_path, tiers=Tiers())
    with pytest.raises(ValidationError, match="dynamic-2.540/ is missing update-center.json"):
        validate(staging)


def test_wrong_signing_certificate(tmp_path):
    with pytest.raises(ValidationError, match="not signed with the configured signing certificate"):
        validate(build(tmp_path), signing_cert_b64="OTHER")


def test_download_urls_must_use_public_url(tmp_path):
    with pytest.raises(ValidationError, match="download URLs outside https://uc.example.com/download/"):
        validate(build(tmp_path, public_url="https://updates.jenkins.io"))


def test_htaccess_must_not_redirect_elsewhere(tmp_path):
    staging = build(tmp_path)
    (staging / ".htaccess").write_text("RewriteRule ^x https://get.jenkins.io/x [R]\n")
    with pytest.raises(ValidationError, match="redirects to an absolute URL"):
        validate(staging)


def test_plugin_count_ratio_and_force(tmp_path):
    staging = build(tmp_path)
    with pytest.raises(ValidationError, match="plugin count 2 < 0.9 × 10"):
        validate(staging, previous_plugin_count=10)
    assert validate(staging, previous_plugin_count=10, force=True).plugin_count == 2


def test_min_plugins(tmp_path):
    with pytest.raises(ValidationError, match="below MIN_PLUGINS=5"):
        validate(build(tmp_path), min_plugins=5)


def test_download_map_must_cover_offered_versions(tmp_path):
    staging = build(tmp_path)
    with pytest.raises(ValidationError, match="no entry for plugins/credentials/1415.v831096eb_5534, war/2.528.1"):
        validate(staging, download_map={"plugins/git/5.2.0": "a"})
    validate(staging, download_map={key: "x" for key in OFFERED})


def test_current_without_core(tmp_path):
    with pytest.raises(ValidationError, match="offers no Jenkins core"):
        validate(build(tmp_path, core=None))


def test_count_plugins(tmp_path):
    assert count_plugins(build(tmp_path) / "current") == 2
    assert count_plugins(tmp_path / "nope") is None
