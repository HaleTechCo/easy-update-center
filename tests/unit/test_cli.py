import json
import os

from sitefixture import uc_document, write_site

from uc.cli import EXIT_LOCKED, main
from uc.publish import RunLock
from uc.status import read_status, write_status


def use_env(monkeypatch, tmp_path, secrets_dir):
    values = {
        "MODE": "airgapped", "PUBLIC_URL": "https://uc.example.com",
        "REPO_URL": "http://artifactory:8081/artifactory/", "REPO_KEY": "jenkins", "REPO_ADAPTER": "off",
        "SECRETS_DIR": str(secrets_dir), "UC_DATA_DIR": str(tmp_path / "data"),
        "UC_OVERLAY_DIR": str(tmp_path / "overlay"),
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    return tmp_path / "data"


def test_status_prints_json(monkeypatch, tmp_path, secrets_dir, capsys):
    data = use_env(monkeypatch, tmp_path, secrets_dir)
    write_status(data, {"live_release": "x"})
    assert main(["status"]) == 0
    assert json.loads(capsys.readouterr().out) == {"live_release": "x"}


def test_configuration_errors_exit_1(monkeypatch, capsys):
    monkeypatch.setenv("MODE", "bogus")
    assert main(["status"]) == 1
    assert "uc: error: MODE must be one of" in capsys.readouterr().err


def test_rollback_updates_status(monkeypatch, tmp_path, secrets_dir, capsys):
    data = use_env(monkeypatch, tmp_path, secrets_dir)
    for rid in ("20260922T010000Z", "20260922T020000Z"):
        write_site(data / "releases" / rid / "current", uc_document("https://uc.example.com"))
    os.symlink("releases/20260922T020000Z", data / "live")
    assert main(["rollback"]) == 0
    assert "20260922T010000Z" in capsys.readouterr().out
    assert read_status(data)["live_release"] == "20260922T010000Z"
    assert read_status(data)["plugin_count"] == 2


def test_generate_reports_a_held_lock(monkeypatch, tmp_path, secrets_dir):
    data = use_env(monkeypatch, tmp_path, secrets_dir)
    data.mkdir(parents=True)
    with RunLock(data):
        assert main(["generate"]) == EXIT_LOCKED


def test_rollback_reports_a_held_lock(monkeypatch, tmp_path, secrets_dir):
    data = use_env(monkeypatch, tmp_path, secrets_dir)
    for rid in ("20260922T010000Z", "20260922T020000Z"):
        write_site(data / "releases" / rid / "current", uc_document("https://uc.example.com"))
    os.symlink("releases/20260922T020000Z", data / "live")
    with RunLock(data):
        assert main(["rollback"]) == EXIT_LOCKED
    assert read_status(data).get("live_release") != "20260922T010000Z"
    assert os.readlink(data / "live") == "releases/20260922T020000Z"


def test_pki_init_and_show(tmp_path, capsys):
    out = tmp_path / "pki"
    assert main(["pki", "init", "--out", str(out), "--root-days", "30", "--signing-days", "10"]) == 0
    assert "root-ca.crt" in capsys.readouterr().out
    assert main(["pki", "show", "--dir", str(out)]) == 0
    shown = capsys.readouterr().out
    assert "update-center.cert: CN=Jenkins Update Center Signing" in shown
    assert "root-ca.crt: CN=Jenkins Update Center Root CA" in shown
