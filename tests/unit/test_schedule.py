"""Unit tests for uc.schedule: the crontab, the recorded start, and the first run before handing over to supercronic."""

import json
import os

import pytest

from uc.config import settings_digest
from uc.schedule import ScheduleError, crontab_text, job_line, run_schedule
from ucsupport import make_settings


def test_job_line():
    assert job_line('0 */6 * * *', 'GENERATE_SCHEDULE', 'uc generate --wait') == '0 */6 * * * uc generate --wait\n'
    assert job_line('@hourly', 'GENERATE_SCHEDULE', 'uc generate --wait') == '@hourly uc generate --wait\n'


def test_crontab_runs_generate_and_waits_for_the_lock(tmp_path, secrets_dir):
    assert crontab_text(make_settings(tmp_path, secrets_dir)) == '0 */6 * * * uc generate --wait\n'


def test_crontab_adds_the_doctor_report_when_scheduled(tmp_path, secrets_dir):
    settings = make_settings(tmp_path, secrets_dir, DOCTOR_SCHEDULE='30 5 * * *')
    assert crontab_text(settings) == '0 */6 * * * uc generate --wait\n30 5 * * * uc doctor --record\n'


@pytest.mark.parametrize(('name', 'value'), [('GENERATE_SCHEDULE', 'every six hours'), ('DOCTOR_SCHEDULE', 'nightly')])
def test_crontab_rejects_nonsense(tmp_path, secrets_dir, name, value):
    with pytest.raises(ScheduleError, match=f'{name} must be a 5-field cron expression or an @macro'):
        crontab_text(make_settings(tmp_path, secrets_dir, **{name: value}))


def run(settings, tmp_path, generate_once):
    calls = []
    run_schedule(settings, generate_once=lambda: (calls.append('generate'), generate_once()), exec_=lambda _file, argv, _env: calls.append(argv), crontab=tmp_path / 'crontab')
    return calls


def test_first_run_generates_then_hands_over_to_supercronic(tmp_path, secrets_dir):
    settings = make_settings(tmp_path, secrets_dir)
    calls = run(settings, tmp_path, lambda: None)
    assert calls == ['generate', ['supercronic', '-passthrough-logs', str(tmp_path / 'crontab')]]
    assert (tmp_path / 'crontab').read_text() == '0 */6 * * * uc generate --wait\n'


def test_the_start_is_recorded_for_uc_config(tmp_path, secrets_dir):
    settings = make_settings(tmp_path, secrets_dir)
    run(settings, tmp_path, lambda: None)
    state = json.loads((settings.data_dir / 'scheduler.json').read_text(encoding='utf-8'))
    assert state['settings_digest'] == settings_digest(settings)
    assert state['started_at'].endswith('Z')


def test_a_bad_schedule_stops_before_the_first_run(tmp_path, secrets_dir):
    settings = make_settings(tmp_path, secrets_dir, DOCTOR_SCHEDULE='nightly')
    with pytest.raises(ScheduleError):
        run(settings, tmp_path, lambda: None)
    assert not (settings.data_dir / 'scheduler.json').exists()


def test_existing_release_skips_the_first_run(tmp_path, secrets_dir):
    settings = make_settings(tmp_path, secrets_dir)
    (settings.data_dir / 'releases' / '20260922T000000Z').mkdir(parents=True)
    (settings.data_dir / 'live').symlink_to('releases/20260922T000000Z')
    assert run(settings, tmp_path, lambda: None) == [['supercronic', '-passthrough-logs', str(tmp_path / 'crontab')]]


def test_failed_first_run_still_schedules(tmp_path, secrets_dir):
    settings = make_settings(tmp_path, secrets_dir)

    def boom():
        msg = 'generator crashed'
        raise RuntimeError(msg)

    calls = run(settings, tmp_path, boom)
    assert calls[-1][0] == 'supercronic'


def test_an_unwritable_data_directory_does_not_stop_the_scheduler(tmp_path, secrets_dir, capsys):
    if os.geteuid() == 0:
        pytest.skip('root can write anywhere')
    settings = make_settings(tmp_path, secrets_dir)
    settings.data_dir.mkdir(parents=True)
    settings.data_dir.chmod(0o555)
    try:
        calls = run(settings, tmp_path, lambda: None)
    finally:
        settings.data_dir.chmod(0o755)
    assert calls[-1][0] == 'supercronic'
    out = capsys.readouterr().out
    assert f'uc schedule: could not record the start in {settings.data_dir / "scheduler.json"}: ' in out
    assert '; uc config cannot tell when a restart is needed' in out
    assert not [path for path in settings.data_dir.iterdir() if path.name.startswith('.scheduler')]
