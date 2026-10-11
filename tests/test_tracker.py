import json
from datetime import date
from pathlib import Path

import pytest
import yaml

from tracker import dashboard, main, notify, scoring, sources
from tracker.sources import Job

ROOT = Path(__file__).resolve().parent.parent
CFG = yaml.safe_load((ROOT / "config.yaml").read_text())
TODAY = date(2026, 10, 11)


def job(title, location="Bengaluru, Karnataka, India", desc="", posted="Posted Today", company="NVIDIA", jid="1"):
    return Job(company=company, title=title, location=location, url="https://x/" + jid,
               job_id=jid, posted=posted, description=desc)


# ------------------------------------------------------------------- scoring

def test_good_match_is_kept_with_reasons():
    j = scoring.evaluate(job("SOC Analyst", desc="Experience with Sentinel, KQL and Splunk. 1-2 years."), CFG, TODAY)
    assert j is not None
    assert j.score >= 70
    assert "India" in j.reasons


@pytest.mark.parametrize("title", [
    "Senior Security Engineer", "Security Engineering Manager", "Principal Security Analyst",
    "Staff Security Engineer", "Security Engineer III", "Security Customer Engineer, Cloud",
    "Data Center Technician", "Product Manager, Safety",
])
def test_wrong_titles_are_dropped(title):
    assert scoring.evaluate(job(title), CFG, TODAY) is None


def test_non_india_location_dropped():
    assert scoring.evaluate(job("Security Analyst", location="Austin, Texas, United States"), CFG, TODAY) is None


def test_multiple_locations_kept_but_not_scored_for_location():
    j = scoring.evaluate(job("Security Analyst", location="3 Locations", desc="SIEM Splunk Sentinel KQL"), CFG, TODAY)
    assert j is not None and "India" not in j.reasons


def test_too_much_experience_dropped():
    assert scoring.evaluate(job("Security Analyst", desc="5 years of experience in security operations"), CFG, TODAY) is None
    assert scoring.evaluate(job("Security Analyst", desc="10 years of experience in cybersecurity"), CFG, TODAY) is None


def test_zero_to_two_years_ok():
    assert scoring.evaluate(job("Security Analyst", desc="0-2 years of experience, SIEM"), CFG, TODAY) is not None


def test_age_of_age_is_not_experience():
    assert scoring.min_years_required("must be 18 years of age") is None


def test_old_posting_dropped():
    assert scoring.evaluate(job("Security Analyst", posted="Posted 30+ Days Ago", desc="SIEM Splunk"), CFG, TODAY) is None


@pytest.mark.parametrize("text,expected", [
    ("Posted Today", 0), ("Posted Yesterday", 1), ("Posted 5 Days Ago", 5),
    ("2026-10-08", 3), ("2026-10-08T05:30:00Z", 3), ("October 8, 2026", 3), ("", None), ("soon", None),
])
def test_days_since_posted(text, expected):
    assert scoring.days_since_posted(text, TODAY) == expected


# ------------------------------------------------------------------- fetchers

def test_workday_parsing_and_paging(monkeypatch):
    page = {"jobPostings": [
        {"title": "Security Analyst", "externalPath": "/job/India-Pune/Security-Analyst_JR100",
         "locationsText": "India, Pune", "postedOn": "Posted Today", "bulletFields": ["JR100"]}]}
    calls = []
    monkeypatch.setattr(sources, "post_json", lambda url, body: calls.append((url, body)) or page)
    out = sources.fetch_workday("NVIDIA", "nvidia.wd5.myworkdayjobs.com", "nvidia", "Site", ["a", "b"])
    assert len(out) == 1                      # duplicate id across queries collapsed
    assert out[0].url == "https://nvidia.wd5.myworkdayjobs.com/en-US/Site/job/India-Pune/Security-Analyst_JR100"
    assert out[0].job_id == "JR100"
    assert calls[0][0] == "https://nvidia.wd5.myworkdayjobs.com/wday/cxs/nvidia/Site/jobs"
    assert len(calls) == 2                    # short page stops paging


def test_amazon_parsing(monkeypatch):
    data = {"jobs": [{"title": "Security Engineer I", "id_icims": "3001", "job_path": "/en/jobs/3001/x",
                      "normalized_location": "Hyderabad, Telangana, IND", "posted_date": "October 9, 2026",
                      "description_short": "Detect <b>threats</b>", "basic_qualifications": "1+ years"}]}
    monkeypatch.setattr(sources, "get_json", lambda url, params=None: data)
    out = sources.fetch_amazon(["security engineer"])
    assert out[0].url == "https://www.amazon.jobs/en/jobs/3001/x"
    assert "<b>" not in out[0].description
    assert scoring.evaluate(out[0], CFG, TODAY) is not None


def test_greenhouse_parsing(monkeypatch):
    data = {"jobs": [{"id": 77, "title": "Security Analyst", "absolute_url": "https://g/77",
                      "location": {"name": "Bangalore, India"}, "updated_at": "2026-10-09T10:00:00-04:00",
                      "content": "&lt;p&gt;Work with SIEM&lt;/p&gt;"}]}
    monkeypatch.setattr(sources, "get_json", lambda url, params=None: data)
    out = sources.fetch_greenhouse("Cloudflare", "cloudflare")
    assert out[0].description == "Work with SIEM"
    assert out[0].job_id == "77"


# ------------------------------------------------------------------ dashboard

def test_dashboard_escapes_script_end_tag():
    payload = {"generated": "now", "jobs": [{"key": "a:1", "company": "A", "title": "</script><b>x", "location": "",
                                             "url": "https://a", "posted": "", "summary": "", "score": 50,
                                             "reasons": [], "first_seen": ""}],
               "new_keys": [], "links": [], "status": {}}
    html = dashboard.build_html(payload)
    assert html.count("</script>") == 1       # only the real closing tag
    assert "<\\/script>" in html


# ------------------------------------------------------------------ main flow

def fake_sources(jobs_by_company, fail=()):
    out = []
    for name, jobs in jobs_by_company.items():
        def fetch(jobs=jobs, name=name):
            if name in fail:
                raise RuntimeError("boom")
            return jobs
        out.append((name, fetch))
    return out


def test_first_run_summary_then_only_new(tmp_path, monkeypatch):
    pushes = []
    monkeypatch.setattr(notify, "send_all", lambda t, b, c="", env=None: pushes.append((t, b)) or ["ntfy"])
    j1 = job("SOC Analyst", desc="Sentinel KQL", jid="1")
    j2 = job("Security Engineer", desc="Splunk SIEM", jid="2")

    r1 = main.run(CFG, root=tmp_path, sources=fake_sources({"NVIDIA": [j1, j2]}), today=TODAY, env={})
    assert r1["first_run"] and r1["matches"] == 2 and len(pushes) == 1
    assert "live" in pushes[0][0]

    # Second run: one old job, one brand new job
    j3 = job("Detection Engineer", desc="KQL Sentinel Defender", jid="3")
    r2 = main.run(CFG, root=tmp_path, sources=fake_sources({"NVIDIA": [j1, j2, j3]}), today=TODAY, env={})
    assert r2["new"] == 1 and len(pushes) == 2
    assert "Detection Engineer" in pushes[1][1] and "SOC Analyst" not in pushes[1][1]

    # Third run: nothing new, no notification
    r3 = main.run(CFG, root=tmp_path, sources=fake_sources({"NVIDIA": [j1, j2, j3]}), today=TODAY, env={})
    assert r3["new"] == 0 and len(pushes) == 2
    assert (tmp_path / "docs" / "index.html").exists()


def test_failed_source_keeps_earlier_results(tmp_path, monkeypatch):
    monkeypatch.setattr(notify, "send_all", lambda *a, **k: [])
    j1 = job("SOC Analyst", desc="Sentinel KQL", jid="1")
    main.run(CFG, root=tmp_path, sources=fake_sources({"NVIDIA": [j1]}), today=TODAY, env={})
    r = main.run(CFG, root=tmp_path, sources=fake_sources({"NVIDIA": [j1]}, fail=("NVIDIA",)), today=TODAY, env={})
    assert r["failed_sources"] == ["NVIDIA"]
    assert r["matches"] == 1
    saved = json.loads((tmp_path / "data" / "jobs.json").read_text())
    assert saved[0]["title"] == "SOC Analyst"
    assert "failed" in (tmp_path / "docs" / "index.html").read_text()


def test_one_broken_source_does_not_stop_others(tmp_path, monkeypatch):
    monkeypatch.setattr(notify, "send_all", lambda *a, **k: [])
    j2 = job("Security Engineer", desc="Splunk SIEM", company="Amazon", jid="9")
    r = main.run(CFG, root=tmp_path, sources=fake_sources({"NVIDIA": [], "Amazon": [j2]}, fail=("NVIDIA",)),
                 today=TODAY, env={})
    assert r["matches"] == 1


def test_dry_run_saves_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(notify, "send_all", lambda *a, **k: pytest.fail("should not notify"))
    main.run(CFG, root=tmp_path, dry_run=True, sources=fake_sources({"NVIDIA": [job("SOC Analyst", desc="Sentinel")]}),
             today=TODAY, env={})
    assert not (tmp_path / "data" / "seen.json").exists()


# --------------------------------------------------------------- notifications

def test_digest_is_ascii_title_and_lists_top_jobs():
    jobs = [job("SOC Analyst", jid=str(i)) for i in range(10)]
    for i, j in enumerate(jobs):
        j.score = 50 + i
    title, body = notify.build_digest(jobs, 3, "https://dash")
    title.encode("ascii")
    assert body.count("https://x/") == 3 and "7 more" in body and "https://dash" in body


def test_channels_skip_when_unconfigured_and_isolate_errors(monkeypatch):
    assert notify.send_all("t", "b", env={}) == []

    def boom(*a, **k):
        raise RuntimeError("down")
    monkeypatch.setattr(notify, "send_ntfy", boom)
    monkeypatch.setattr(notify, "send_telegram", lambda *a, **k: True)
    assert notify.send_all("t", "b", env={}) == ["telegram"]


def test_ntfy_posts_to_topic(monkeypatch):
    seen = {}

    class R:
        def raise_for_status(self): pass
    monkeypatch.setattr(notify.requests, "post", lambda url, data, headers, timeout: seen.update(url=url, h=headers) or R())
    assert notify.send_ntfy("T", "body", "https://dash", {"NTFY_TOPIC": "abc"})
    assert seen["url"] == "https://ntfy.sh/abc" and seen["h"]["Click"] == "https://dash"
