"""One machine-readable line for every stop, so an unattended run raises a follow-up for it.

A script that stops prints `FOLLOWUP stop <stage>: <why>` on stdout, the same shape as the
token-gap FOLLOWUP lines, and the skill raises one follow-up per line. Ids are cut to a prefix:
the follow-up refuses full ids outside links.
"""
import re

_ID = re.compile(r"\b[0-9a-f]{8}(?:-?[0-9a-f]{4}){3}-?[0-9a-f]{12}\b", re.I)


class Reported(SystemExit):
    """A stop whose cause already printed its own stop line (a child script's)."""


def stop_line(stage, why):
    first = (str(why).strip().splitlines() or ["no reason given"])[0]
    return f"FOLLOWUP stop {stage}: {_ID.sub(lambda m: m.group(0)[:8] + '…', first)[:200]}"


def run(main, stage):
    try:
        main()
    except Reported:
        raise
    except SystemExit as e:
        if e.code not in (0, None):
            print(stop_line(stage, e.code if isinstance(e.code, str) else f"exit {e.code}"), flush=True)
        raise
    except Exception as e:
        print(stop_line(stage, f"{type(e).__name__}: {e}"), flush=True)
        raise
