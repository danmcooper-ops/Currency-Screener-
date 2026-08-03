"""Decide whether a scheduled build should proceed.

    python scripts/should_build.py --tz America/New_York --hour 16

Writes `proceed=true|false` and a `reason=` line in GitHub Actions output
format, so the workflow can gate its steps with an `if:` rather than failing
the job — a deliberate skip is not an error and should not show a red X.

Two independent checks.

**Local hour.** GitHub cron is fixed-UTC, but 16:15 America/New_York is
20:15 UTC under EDT and 21:15 UTC under EST. The workflow therefore registers
both crons and this check discards whichever one is not currently 16:00 local.
Exactly one survives on any given day, in either half of the year, with no
schedule editing at the DST boundaries.

A scheduled run delayed past the hour is dropped rather than run late. That is
an acceptable loss here: every build regenerates from full history, so a
missed day carries no state forward and the next run is identical to the one
that would have happened. Nothing accumulates incrementally.

**Fresh ECB data.** The model's spine is the ECB daily reference rate series,
which is not published on TARGET holidays. Rather than maintain a holiday
calendar — which would need updating annually and would encode the wrong
country's calendar anyway — this asks the question that actually matters: did
today's rates publish? If the newest observation is not today's date, there is
nothing new to score, and the build is skipped.

That also catches outages, not just holidays: an ECB endpoint failure looks
identical to a holiday here, and in both cases rebuilding would republish
yesterday's numbers under today's date.
"""

import argparse
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data import ecb_client


def emit(proceed, reason):
    """Write GitHub Actions outputs, and echo for the run log."""
    line_p = 'proceed=%s' % ('true' if proceed else 'false')
    line_r = 'reason=%s' % reason
    out = os.environ.get('GITHUB_OUTPUT')
    if out:
        with open(out, 'a', encoding='utf-8') as f:
            f.write(line_p + '\n' + line_r + '\n')
    print('%s  (%s)' % (line_p, reason))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--tz', default='America/New_York',
                    help='IANA timezone the --hour check is evaluated in')
    ap.add_argument('--hour', type=int, default=None,
                    help='only proceed during this local hour (0-23); '
                         'omit to skip the time check entirely')
    ap.add_argument('--skip-freshness', action='store_true',
                    help='do not require a same-day ECB observation')
    args = ap.parse_args(argv)

    try:
        tz = ZoneInfo(args.tz)
    except Exception:
        return emit(True, 'unknown timezone %r, not gating on time' % args.tz)

    now = datetime.now(tz)

    if args.hour is not None and now.hour != args.hour:
        return emit(False, 'local time is %s in %s, not the %02d:00 hour'
                    % (now.strftime('%H:%M'), args.tz, args.hour))

    if args.skip_freshness:
        return emit(True, 'freshness check disabled')

    per_eur = ecb_client.fetch_reference_rates(ttl_days=0.0)
    if not per_eur:
        # Distinct from a holiday: the source is unreachable. Skipping is
        # still right — there is nothing new to publish either way.
        return emit(False, 'ECB reference rates unavailable')

    usd = per_eur.get('USD') or {}
    if not usd:
        return emit(False, 'ECB responded without a USD series')

    latest = max(usd)
    today = now.date().isoformat()
    if latest != today:
        return emit(False, 'newest ECB observation is %s, not %s '
                           '(TARGET holiday, weekend, or not yet published)'
                    % (latest, today))

    return emit(True, 'ECB published %s; building' % latest)


if __name__ == '__main__':
    raise SystemExit(main())
