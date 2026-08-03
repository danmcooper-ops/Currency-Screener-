"""The scheduled-build gate.

Offline: the ECB fetch is stubbed. The cases that matter are the ones that
decide whether a weekday run happens at all, and the failure modes are
asymmetric — wrongly proceeding republishes yesterday's numbers under today's
date, wrongly skipping just loses a day that the next run reproduces exactly.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from scripts import should_build

NY = ZoneInfo('America/New_York')


@pytest.fixture
def run(monkeypatch, capsys):
    """Invoke main() with a pinned clock and a stubbed ECB response."""
    def _run(local_hour=16, ecb_offset_days=0, ecb=True, argv=None):
        now = datetime.now(NY).replace(hour=local_hour, minute=15)

        class FrozenDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return now

        monkeypatch.setattr(should_build, 'datetime', FrozenDateTime)

        def fake_fetch(*a, **kw):
            if not ecb:
                return None
            day = (now.date() + timedelta(days=ecb_offset_days)).isoformat()
            return {'USD': {day: 1.15}}

        monkeypatch.setattr(should_build.ecb_client,
                            'fetch_reference_rates', fake_fetch)
        should_build.main(argv if argv is not None
                          else ['--tz', 'America/New_York', '--hour', '16'])
        return capsys.readouterr().out
    return _run


def test_proceeds_at_the_target_hour_with_todays_rates(run):
    assert 'proceed=true' in run(local_hour=16, ecb_offset_days=0)


def test_skips_outside_the_target_hour(run):
    """The wrong-DST cron lands here — one of the two always does."""
    out = run(local_hour=15)
    assert 'proceed=false' in out
    assert 'not the 16:00 hour' in out


def test_skips_when_ecb_published_nothing_today(run):
    """A TARGET holiday: newest observation is an earlier date.

    Rebuilding would republish yesterday's rates stamped with today's date.
    """
    out = run(local_hour=16, ecb_offset_days=-1)
    assert 'proceed=false' in out
    assert 'newest ECB observation' in out


def test_skips_when_ecb_is_unreachable(run):
    out = run(local_hour=16, ecb=False)
    assert 'proceed=false' in out
    assert 'unavailable' in out


def test_skip_freshness_flag_bypasses_the_data_check(run):
    out = run(local_hour=16, ecb_offset_days=-30,
              argv=['--hour', '16', '--skip-freshness'])
    assert 'proceed=true' in out


def test_omitting_hour_disables_the_time_check(run):
    out = run(local_hour=3, ecb_offset_days=0, argv=[])
    assert 'proceed=true' in out


def test_unknown_timezone_fails_open(run):
    """A bad tz must not silently suppress every build."""
    out = run(argv=['--tz', 'Not/AZone', '--hour', '16'])
    assert 'proceed=true' in out


def test_writes_github_output_file(monkeypatch, tmp_path, run):
    out_file = tmp_path / 'gho.txt'
    monkeypatch.setenv('GITHUB_OUTPUT', str(out_file))
    run(local_hour=16, ecb_offset_days=0)
    written = out_file.read_text()
    assert 'proceed=true' in written
    assert written.count('\n') == 2      # exactly proceed= and reason=
    assert 'reason=' in written
