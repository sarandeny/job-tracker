"""Daily run: fetch, filter, find what's new, notify, refresh the dashboard."""
from __future__ import annotations

import argparse
import json
import logging
import os
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable

import yaml

from . import notify as notify_mod
from .dashboard import build_html
from .scoring import evaluate
from .sources import Job, build_sources

log = logging.getLogger("tracker")

ROOT = Path(__file__).resolve().parent.parent
SourceList = list[tuple[str, Callable[[], list[Job]]]]


def _load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def run(
    cfg: dict,
    root: Path = ROOT,
    dry_run: bool = False,
    do_notify: bool = True,
    sources: SourceList | None = None,
    today: date | None = None,
    env=os.environ,
) -> dict:
    data_dir, docs_dir = root / "data", root / "docs"
    data_dir.mkdir(parents=True, exist_ok=True)
    docs_dir.mkdir(parents=True, exist_ok=True)

    seen: dict[str, str] = _load_json(data_dir / "seen.json", {})
    previous = [Job.from_dict(d) for d in _load_json(data_dir / "jobs.json", [])]
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    first_run = not seen

    matches: dict[str, Job] = {}
    status: dict[str, dict] = {}
    for name, fetch in sources if sources is not None else build_sources(cfg):
        try:
            fetched = fetch()
        except Exception as e:
            log.warning("%s failed: %s", name, e)
            status[name] = {"ok": False, "error": str(e)[:120]}
            # keep earlier results for this company so the dashboard doesn't go blank
            for j in previous:
                if j.company == name:
                    matches[j.key] = j
            continue
        kept = [j for j in (evaluate(j, cfg, today) for j in fetched) if j]
        for j in kept:
            matches[j.key] = j
        status[name] = {"ok": True, "fetched": len(fetched), "matched": len(kept)}
        log.info("%s: %d fetched, %d matched", name, len(fetched), len(kept))

    new_jobs = []
    for key, job in matches.items():
        if key in seen:
            job.first_seen = seen[key]
        else:
            job.first_seen = now
            new_jobs.append(job)

    push_min = cfg["notify"].get("min_score_for_push", 55)
    to_push = [j for j in new_jobs if j.score >= push_min]
    if first_run:
        # Don't flood the phone on day one: one summary of the best matches.
        to_push = sorted(matches.values(), key=lambda j: j.score, reverse=True)

    payload = {
        "generated": now,
        "jobs": [j.to_dict() for j in matches.values()],
        "new_keys": [j.key for j in new_jobs] if not first_run else [],
        "links": cfg.get("links", []),
        "status": status,
    }
    (docs_dir / "index.html").write_text(build_html(payload), encoding="utf-8")

    sent: list[str] = []
    if not dry_run:
        (data_dir / "jobs.json").write_text(
            json.dumps([j.to_dict() for j in matches.values()], indent=1, ensure_ascii=False),
            encoding="utf-8",
        )
        for key, job in matches.items():
            seen.setdefault(key, job.first_seen)
        (data_dir / "seen.json").write_text(json.dumps(seen, indent=1), encoding="utf-8")

        if do_notify and to_push:
            url = env.get("DASHBOARD_URL") or cfg["notify"].get("dashboard_url", "")
            title, body = notify_mod.build_digest(
                to_push, cfg["notify"].get("max_items_in_push", 6), url, initial=first_run
            )
            sent = notify_mod.send_all(title, body, url, env)

    return {
        "matches": len(matches),
        "new": len(new_jobs),
        "pushed": len(to_push) if sent else 0,
        "channels": sent,
        "failed_sources": [n for n, s in status.items() if not s["ok"]],
        "first_run": first_run,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Daily security job tracker")
    ap.add_argument("--config", default=str(ROOT / "config.yaml"))
    ap.add_argument("--dry-run", action="store_true", help="don't save state or notify")
    ap.add_argument("--no-notify", action="store_true")
    ap.add_argument("--test-notify", action="store_true", help="send a test alert and exit")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if args.test_notify:
        sent = notify_mod.send_all(
            "Job tracker test", "If you can read this, alerts are connected.", os.environ.get("DASHBOARD_URL", "")
        )
        print("Sent via:", ", ".join(sent) if sent else "nothing (no channel configured)")
        return

    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    result = run(cfg, dry_run=args.dry_run, do_notify=not args.no_notify)
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    main()
