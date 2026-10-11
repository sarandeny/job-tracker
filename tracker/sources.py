"""Fetchers for company career feeds. Each fetcher returns a list of Job objects."""
from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass, field
from typing import Callable, Iterable

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger("tracker")

TIMEOUT = 25
_session: requests.Session | None = None


@dataclass
class Job:
    company: str
    title: str
    location: str
    url: str
    job_id: str
    posted: str = ""
    description: str = ""
    source: str = ""
    score: int = 0
    reasons: list[str] = field(default_factory=list)
    first_seen: str = ""

    @property
    def key(self) -> str:
        return f"{self.company}:{self.job_id}"

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "company": self.company,
            "title": self.title,
            "location": self.location,
            "url": self.url,
            "posted": self.posted,
            "summary": self.description[:280].strip(),
            "score": self.score,
            "reasons": self.reasons,
            "first_seen": self.first_seen,
        }

    @staticmethod
    def from_dict(d: dict) -> "Job":
        return Job(
            company=d["company"],
            title=d["title"],
            location=d.get("location", ""),
            url=d["url"],
            job_id=d["key"].split(":", 1)[1] if ":" in d.get("key", "") else d.get("key", ""),
            posted=d.get("posted", ""),
            description=d.get("summary", ""),
            score=d.get("score", 0),
            reasons=d.get("reasons", []),
            first_seen=d.get("first_seen", ""),
        )


# ---------------------------------------------------------------- HTTP helpers

def _http() -> requests.Session:
    global _session
    if _session is None:
        s = requests.Session()
        s.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (compatible; personal-job-tracker/1.0)",
                "Accept": "application/json",
            }
        )
        retry = Retry(
            total=3,
            backoff_factor=1.5,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET", "POST"),
        )
        s.mount("https://", HTTPAdapter(max_retries=retry))
        _session = s
    return _session


def get_json(url: str, params: dict | None = None) -> dict:
    r = _http().get(url, params=params, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def post_json(url: str, body: dict) -> dict:
    r = _http().post(url, json=body, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def strip_html(text: str) -> str:
    text = html.unescape(text or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


# ------------------------------------------------------------------- fetchers

def fetch_amazon(queries: Iterable[str], location: str = "India") -> list[Job]:
    jobs: dict[str, Job] = {}
    for q in queries:
        data = get_json(
            "https://www.amazon.jobs/en/search.json",
            params={
                "base_query": q,
                "loc_query": location,
                "country[]": "IND",
                "result_limit": 50,
                "sort": "recent",
            },
        )
        for j in data.get("jobs", []):
            job_id = str(j.get("id_icims") or j.get("id") or j.get("job_path"))
            if job_id in jobs:
                continue
            text = " ".join(
                filter(
                    None,
                    [
                        j.get("description_short"),
                        j.get("basic_qualifications"),
                        j.get("preferred_qualifications"),
                    ],
                )
            )
            jobs[job_id] = Job(
                company="Amazon",
                title=j.get("title", ""),
                location=j.get("normalized_location") or j.get("location", ""),
                url="https://www.amazon.jobs" + j.get("job_path", ""),
                job_id=job_id,
                posted=j.get("posted_date", ""),
                description=strip_html(text),
                source="amazon",
            )
    return list(jobs.values())


def fetch_workday(
    name: str, host: str, tenant: str, site: str, queries: Iterable[str], pages: int = 2
) -> list[Job]:
    api = f"https://{host}/wday/cxs/{tenant}/{site}/jobs"
    jobs: dict[str, Job] = {}
    for q in queries:
        for page in range(pages):
            data = post_json(
                api,
                {"appliedFacets": {}, "limit": 20, "offset": page * 20, "searchText": q},
            )
            postings = data.get("jobPostings", [])
            for p in postings:
                path = p.get("externalPath", "")
                bullets = p.get("bulletFields") or []
                job_id = str(bullets[0]) if bullets else path
                if job_id in jobs:
                    continue
                jobs[job_id] = Job(
                    company=name,
                    title=p.get("title", ""),
                    location=p.get("locationsText", ""),
                    url=f"https://{host}/en-US/{site}{path}",
                    job_id=job_id,
                    posted=p.get("postedOn", ""),
                    source="workday",
                )
            if len(postings) < 20:
                break
    return list(jobs.values())


def fetch_greenhouse(name: str, board: str) -> list[Job]:
    data = get_json(
        f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs", params={"content": "true"}
    )
    out = []
    for j in data.get("jobs", []):
        out.append(
            Job(
                company=name,
                title=j.get("title", ""),
                location=(j.get("location") or {}).get("name", ""),
                url=j.get("absolute_url", ""),
                job_id=str(j.get("id")),
                posted=j.get("updated_at", ""),
                description=strip_html(j.get("content", "")),
                source="greenhouse",
            )
        )
    return out


# ------------------------------------------------------------------- registry

def build_sources(cfg: dict) -> list[tuple[str, Callable[[], list[Job]]]]:
    """Return (company name, zero-argument fetch function) for every enabled source."""
    queries = cfg.get("search_queries", ["security analyst"])
    src = cfg.get("sources", {})
    out: list[tuple[str, Callable[[], list[Job]]]] = []

    amazon = src.get("amazon", {})
    if amazon.get("enabled", False):
        loc = amazon.get("location", "India")
        out.append(("Amazon", lambda: fetch_amazon(queries, loc)))

    for w in src.get("workday", []):
        out.append(
            (
                w["name"],
                lambda w=w: fetch_workday(w["name"], w["host"], w["tenant"], w["site"], queries),
            )
        )

    for g in src.get("greenhouse", []):
        out.append((g["name"], lambda g=g: fetch_greenhouse(g["name"], g["board"])))

    return out
