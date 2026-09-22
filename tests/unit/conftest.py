import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "support"))


@pytest.fixture(scope="session")
def pki_material(tmp_path_factory):
    from uc.pki import init_pki

    directory = tmp_path_factory.mktemp("pki")
    init_pki(directory, root_days=60, signing_days=45)
    return directory


@pytest.fixture
def secrets_dir(tmp_path, pki_material):
    directory = tmp_path / "secrets"
    directory.mkdir()
    for name in ("update-center.key", "update-center.cert", "root-ca.crt"):
        shutil.copy(pki_material / name, directory / name)
    (directory / "artifactory_username").write_text("uc\n")
    (directory / "artifactory_password").write_text("secret\n")
    return directory
