import os

import pytest

from uc.schedule import ScheduleError, crontab_line, run_schedule
from ucsupport import make_settings


def test_crontab_line():
    assert crontab_line('0 */6 * * *') == '0 */6 * * * uc generate\n'
    assert crontab_line('@hourly') == '@hourly uc generate\n'


def test_crontab_line_rejects_nonsense():
    with pytest.raises(ScheduleError, match='5-field cron expression'):
        crontab_line('every six hours')


def run(settings, tmp_path, generate_once):
    calls = []
    run_schedule(settings, generate_once=lambda: (calls.append('generate'), generate_once()), exec_=lambda file, argv: calls.append(argv), crontab=tmp_path / 'crontab')
    return calls


def test_first_run_generates_then_hands_over_to_supercronic(tmp_path, secrets_dir):
    settings = make_settings(tmp_path, secrets_dir)
    calls = run(settings, tmp_path, lambda: None)
    assert calls == ['generate', ['supercronic', '-passthrough-logs', str(tmp_path / 'crontab')]]
    assert (tmp_path / 'crontab').read_text() == '0 */6 * * * uc generate\n'


def test_existing_release_skips_the_first_run(tmp_path, secrets_dir):
    settings = make_settings(tmp_path, secrets_dir)
    (settings.data_dir / 'releases' / '20260922T000000Z').mkdir(parents=True)
    os.symlink('releases/20260922T000000Z', settings.data_dir / 'live')
    assert run(settings, tmp_path, lambda: None) == [['supercronic', '-passthrough-logs', str(tmp_path / 'crontab')]]


def test_failed_first_run_still_schedules(tmp_path, secrets_dir):
    settings = make_settings(tmp_path, secrets_dir)

    def boom():
        msg = 'generator crashed'
        raise RuntimeError(msg)

    calls = run(settings, tmp_path, boom)
    assert calls[-1][0] == 'supercronic'
