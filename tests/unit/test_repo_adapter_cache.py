import datetime as dt
import os
import time

import pytest

from uc.repo_adapter.archive import ArchiveFacts
from uc.repo_adapter.cache import Cache

SHA = "ab" + "0" * 62
FACTS = ArchiveFacts(b"Manifest-Version: 1.0\n", ("io.jenkins.plugins", "caffeine-api", "3.2.4"), b"<project/>",
                     b"<div>d</div>", dt.datetime(2024, 5, 1, 12, 30, tzinfo=dt.timezone.utc))


def test_facts_round_trip(tmp_path):
    cache = Cache(tmp_path)
    assert cache.facts(SHA) is None
    cache.store_facts(SHA, FACTS)
    assert cache.facts(SHA) == FACTS
    assert (tmp_path / "ab" / SHA / "pom.xml").read_bytes() == b"<project/>"


def test_absent_facts_are_remembered_as_markers(tmp_path):
    cache = Cache(tmp_path)
    cache.store_facts(SHA, ArchiveFacts(None, None, None, None, None))
    assert cache.facts(SHA) == ArchiveFacts(None, None, None, None, None)
    assert (tmp_path / "ab" / SHA / "pom.none").is_file()
    assert (tmp_path / "ab" / SHA / "released.none").is_file()


def test_facts_without_the_final_marker_are_incomplete(tmp_path):
    cache = Cache(tmp_path)
    cache.store_facts(SHA, FACTS)
    (tmp_path / "ab" / SHA / "released").unlink()  # as if the process died before the last write
    assert cache.facts(SHA) is None


def test_sha1_verdict_and_description(tmp_path):
    cache = Cache(tmp_path)
    assert (cache.sha1(SHA), cache.pom_verdict(SHA)) == (None, None)
    cache.store_sha1(SHA, "a" * 40)
    cache.store_pom_verdict(SHA, None)
    assert (cache.sha1(SHA), cache.pom_verdict(SHA)) == ("a" * 40, "")
    cache.store_pom_verdict(SHA, "not XML")
    assert cache.pom_verdict(SHA) == "not XML"
    assert not cache.has_description(SHA)
    cache.store_description(SHA, None)
    assert cache.has_description(SHA) and cache.description(SHA) is None


def test_rejects_keys_that_are_not_sha256(tmp_path):
    with pytest.raises(ValueError):
        Cache(tmp_path).facts("../../etc/passwd")


def test_prune_removes_entries_untouched_for_30_days(tmp_path):
    cache = Cache(tmp_path)
    old, fresh = "cd" + "1" * 62, "ef" + "2" * 62
    cache.store_facts(old, FACTS)
    cache.store_facts(fresh, FACTS)
    long_ago = time.time() - 31 * 86400
    os.utime(tmp_path / "cd" / old, (long_ago, long_ago))
    assert cache.prune() == 1
    assert cache.facts(old) is None and cache.facts(fresh) == FACTS
