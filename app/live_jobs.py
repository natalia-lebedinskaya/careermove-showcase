"""Live international job search for CareerMove AI.

The module intentionally uses documented JSON/RSS feeds. Open-weight models may
rank real vacancies, but they never create vacancy URLs or source records.
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import random
import re
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed, wait
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from threading import BoundedSemaphore, Lock
from typing import Any, Callable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from requests.exceptions import ConnectTimeout, ProxyError, SSLError
from urllib3.util.retry import Retry


UTC = timezone.utc
USER_AGENT = "CareerMoveAI/13.3.2 (public international job search; contact via application settings)"


def configured_search_proxy() -> str:
    """Return a validated server-side HTTP(S) proxy without exposing credentials."""
    value = clean_text(
        os.getenv("SEARCH_PROXY_URL") or os.getenv("QUOTAGUARDSTATIC_URL") or "",
        2000,
    ).strip()
    if not value:
        return ""
    parsed = urlsplit(value)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return ""
    return value


def _direct_fallback_enabled() -> bool:
    value = str(os.getenv("SEARCH_PROXY_DIRECT_FALLBACK", "1")).strip().lower()
    return value not in {"0", "false", "no", "off"}


class SearchSourceSession(requests.Session):
    """Resilient GET-only source client with an optional isolated egress proxy."""

    def __init__(
        self,
        proxy_url: str = "",
        *,
        request_timeout: float | None = None,
        retries_enabled: bool = True,
    ) -> None:
        super().__init__()
        self.proxy_url = proxy_url
        self.request_timeout = request_timeout
        self.trust_env = False
        retry = Retry(
            total=2 if retries_enabled else 0,
            connect=2 if retries_enabled else 0,
            read=1 if retries_enabled else 0,
            status=2,
            backoff_factor=0.45,
            status_forcelist=(408, 425, 429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET", "HEAD"}),
            respect_retry_after_header=True,
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry, pool_connections=12, pool_maxsize=12)
        self.mount("http://", adapter)
        self.mount("https://", adapter)

    def request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        method_upper = str(method).upper()
        if self.request_timeout is not None:
            supplied = kwargs.get("timeout")
            try:
                ceiling = float(supplied)
            except (TypeError, ValueError):
                ceiling = self.request_timeout
            kwargs["timeout"] = min(max(0.5, ceiling), self.request_timeout)
        if not self.proxy_url or method_upper not in {"GET", "HEAD"}:
            return super().request(method, url, **kwargs)
        proxied = dict(kwargs)
        proxied["proxies"] = {"http": self.proxy_url, "https": self.proxy_url}
        try:
            response = super().request(method, url, **proxied)
            if response.status_code != 407 or not _direct_fallback_enabled():
                return response
        except (ProxyError, ConnectTimeout, SSLError):
            if not _direct_fallback_enabled():
                raise
        direct = dict(kwargs)
        direct["proxies"] = {}
        return super().request(method, url, **direct)


def build_search_session(
    *, request_timeout: float | None = None, retries_enabled: bool = True,
) -> SearchSourceSession:
    """Build an outbound client used only for public vacancy sources."""
    return SearchSourceSession(
        configured_search_proxy(),
        request_timeout=request_timeout,
        retries_enabled=retries_enabled,
    )


def search_network_label() -> str:
    if configured_search_proxy():
        fallback = " · прямой резерв включён" if _direct_fallback_enabled() else ""
        return f"Серверный прокси активен{fallback}"
    return "Сервер CareerMove подключается к источникам напрямую"


def safe_network_error(error: Exception) -> str:
    """Redact proxy credentials before an upstream failure reaches cache or UI."""
    message = clean_text(error, 500)
    proxy_url = configured_search_proxy()
    if proxy_url:
        message = message.replace(proxy_url, "server proxy")
    return re.sub(r"(https?://)[^/@\s]+@", r"\1***@", message, flags=re.IGNORECASE)

TELEGRAM_ABROAD_CHANNELS = {
    "young_relocate": {"label": "Relocate", "query": ""},
    "jobs_abroad": {"label": "Jobs abroad", "query": ""},
    "evacuatejobs": {"label": "Remocate", "query": ""},
    "Relocats": {"label": "IT Relocation", "query": ""},
    "qa_chillout_jobs": {"label": "Jobs", "query": ""},
    "cyithr": {"label": "CY HR", "query": ""},
}

# Each public Telegram channel is a separate source from the user's point of
# view.  The previous implementation gathered all six under the single name
# "Telegram Abroad" and then reported only eight sources in the interface.
# Keep the channel-level identity so a broken feed, an old post, or a useful
# niche community is visible and controllable independently.
TELEGRAM_SOURCE_SPECS = {
    "Telegram · Relocate": {
        "channel": "young_relocate", "label": "Relocate", "query": "", "url": "https://t.me/s/young_relocate",
        "ttl_minutes": 30, "attribution": "Public Russian-language relocation vacancies",
    },
    "Telegram · Jobs Abroad": {
        "channel": "jobs_abroad", "label": "Jobs abroad", "query": "", "url": "https://t.me/s/jobs_abroad",
        "ttl_minutes": 30, "attribution": "Public Russian-language international vacancies",
    },
    "Telegram · Remocate": {
        "channel": "evacuatejobs", "label": "Remocate", "query": "", "url": "https://t.me/s/evacuatejobs",
        "ttl_minutes": 30, "attribution": "Public Russian-language relocation vacancies",
    },
    "Telegram · IT Relocation": {
        "channel": "Relocats", "label": "IT Relocation", "query": "", "url": "https://t.me/s/Relocats",
        "ttl_minutes": 30, "attribution": "Public Russian-language IT relocation vacancies",
    },
    "Telegram · QA Jobs": {
        "channel": "qa_chillout_jobs", "label": "QA Jobs", "query": "", "url": "https://t.me/s/qa_chillout_jobs",
        "ttl_minutes": 30, "attribution": "Public Russian-language QA vacancies",
    },
    "Telegram · CY HR": {
        "channel": "cyithr", "label": "CY HR", "query": "", "url": "https://t.me/s/cyithr",
        "ttl_minutes": 30, "attribution": "Public Russian-language international vacancies",
    },
    "Telegram · Serbia Jobs": {
        "channel": "serbia_jobs", "label": "Serbia Jobs", "query": "", "url": "https://t.me/s/serbia_jobs",
        "ttl_minutes": 30, "attribution": "Public Serbia and Russian-speaking community vacancies",
    },
    "Telegram · For Analysts": {
        "channel": "foranalysts", "label": "For Analysts", "query": "", "url": "https://t.me/s/foranalysts",
        "ttl_minutes": 30, "attribution": "Public analyst and product vacancies",
    },
    "Telegram · Job for QA": {
        "channel": "forallqa", "label": "Job for QA", "query": "", "url": "https://t.me/s/forallqa",
        "ttl_minutes": 30, "attribution": "Public Russian-language QA and TestOps vacancies",
    },
    "Telegram · Job for QA Remote": {
        "channel": "forallqa", "label": "Job for QA Remote", "query": "удал",
        "url": "https://t.me/s/forallqa?q=%D1%83%D0%B4%D0%B0%D0%BB",
        "ttl_minutes": 30, "attribution": "Public Russian-language remote QA vacancies",
    },
    "Telegram · IT Support Jobs": {
        "channel": "call_rabota", "label": "IT Support Jobs", "query": "", "url": "https://t.me/s/call_rabota",
        "ttl_minutes": 30, "attribution": "Public Russian-language IT support vacancies",
    },
    "Telegram · IT Support Remote": {
        "channel": "call_rabota", "label": "IT Support Remote", "query": "удаленно",
        "url": "https://t.me/s/call_rabota?q=%D1%83%D0%B4%D0%B0%D0%BB%D0%B5%D0%BD%D0%BD%D0%BE",
        "ttl_minutes": 30, "attribution": "Public Russian-language remote IT support vacancies",
    },
    "Telegram · IT Support Junior": {
        "channel": "call_rabota", "label": "IT Support Junior", "query": "#junior",
        "url": "https://t.me/s/call_rabota?q=%23junior",
        "ttl_minutes": 30, "attribution": "Public Russian-language junior IT support vacancies",
    },
    "Telegram · IT Digital Jobs": {
        "channel": "it_vakansii_jobs", "label": "IT Digital Jobs", "query": "", "url": "https://t.me/s/it_vakansii_jobs",
        "ttl_minutes": 30, "attribution": "Public Russian-language IT and digital vacancies",
    },
    "Telegram · IT Digital QA": {
        "channel": "it_vakansii_jobs", "label": "IT Digital QA", "query": "QA",
        "url": "https://t.me/s/it_vakansii_jobs?q=QA",
        "ttl_minutes": 30, "attribution": "Public Russian-language IT and QA vacancies",
    },
    "Telegram · Creative Remote": {
        "channel": "rueventjob", "label": "Creative Remote", "query": "", "url": "https://t.me/s/rueventjob",
        "ttl_minutes": 30, "attribution": "Public Russian-language remote work vacancies",
    },
    "Telegram · Workzilla Support": {
        "channel": "workzilla_vacancies", "label": "Workzilla Support", "query": "поддерж",
        "url": "https://t.me/s/workzilla_vacancies?q=%D0%BF%D0%BE%D0%B4%D0%B4%D0%B5%D1%80%D0%B6",
        "ttl_minutes": 30, "attribution": "Public Russian-language remote support gigs and vacancies",
    },
    "Telegram · Junior Internships": {
        "channel": "juniors_rabota_jobs", "label": "Junior Internships", "query": "удаленно",
        "url": "https://t.me/s/juniors_rabota_jobs?q=%D1%83%D0%B4%D0%B0%D0%BB%D0%B5%D0%BD%D0%BD%D0%BE",
        "ttl_minutes": 30, "attribution": "Public Russian-language junior and internship vacancies",
    },
    "Telegram · Junior Internships · Стажировка": {
        "channel": "juniors_rabota_jobs", "label": "Junior Internships · Стажировка", "query": "стаж",
        "url": "https://t.me/s/juniors_rabota_jobs?q=%D1%81%D1%82%D0%B0%D0%B6",
        "ttl_minutes": 20, "attribution": "Public Russian-language internship vacancies",
    },
    "Telegram · Junior Internships · Без опыта": {
        "channel": "juniors_rabota_jobs", "label": "Junior Internships · Без опыта", "query": "без опыта",
        "url": "https://t.me/s/juniors_rabota_jobs?q=%D0%B1%D0%B5%D0%B7%20%D0%BE%D0%BF%D1%8B%D1%82%D0%B0",
        "ttl_minutes": 20, "attribution": "Public Russian-language zero-experience vacancies",
    },
    "Telegram · Junior Internships · QA": {
        "channel": "juniors_rabota_jobs", "label": "Junior Internships · QA", "query": "тест",
        "url": "https://t.me/s/juniors_rabota_jobs?q=%D1%82%D0%B5%D1%81%D1%82",
        "ttl_minutes": 20, "attribution": "Public Russian-language entry-level QA vacancies",
    },
    "Telegram · Junior Internships · Support": {
        "channel": "juniors_rabota_jobs", "label": "Junior Internships · Support", "query": "поддерж",
        "url": "https://t.me/s/juniors_rabota_jobs?q=%D0%BF%D0%BE%D0%B4%D0%B4%D0%B5%D1%80%D0%B6",
        "ttl_minutes": 20, "attribution": "Public Russian-language entry-level support vacancies",
    },
}

SOURCE_SPECS = {
    "CareerSpace": {
        "url": "https://careerspace.app/collection/remote",
        "ttl_minutes": 20,
        "attribution": "Current remote vacancies from CareerSpace",
    },
    "SETTERS Media": {
        "url": "https://www.setters.media/a-teams/jobs",
        "ttl_minutes": 20,
        "attribution": "Current vacancies from SETTERS Media A-Teams",
    },
    "Hirify": {
        "url": "https://hirify.me/en/remote-jobs",
        "ttl_minutes": 20,
        "attribution": "Current public vacancies from Hirify",
    },
    "Talanto": {
        "url": "https://talanto.work/",
        "ttl_minutes": 60,
        "attribution": "Talanto public QA listing",
    },
    "Habr Career": {
        "url": "https://career.habr.com/vacancies/rss",
        "ttl_minutes": 30,
        "attribution": "Vacancies by Habr Career",
    },
    "Arbeitnow": {
        "url": "https://www.arbeitnow.com/api/job-board-api",
        "ttl_minutes": 15,
        "attribution": "Jobs by Arbeitnow",
    },
    "Remote OK": {
        "url": "https://remoteok.com/api",
        "ttl_minutes": 15,
        "attribution": "Jobs by Remote OK",
    },
    "We Work Remotely": {
        "url": "https://weworkremotely.com/remote-jobs.rss",
        "ttl_minutes": 15,
        "attribution": "Jobs by We Work Remotely",
    },
    # These are distinct official WWR category feeds, not copied catalog links.
    # Keeping them separate lets a user see which kind of work actually produced
    # a card and broadens search beyond IT without scraping a private website.
    "We Work Remotely · Programming": {
        "url": "https://weworkremotely.com/categories/remote-programming-jobs.rss",
        "ttl_minutes": 15,
        "attribution": "Programming jobs by We Work Remotely",
    },
    "We Work Remotely · Product": {
        "url": "https://weworkremotely.com/categories/remote-product-jobs.rss",
        "ttl_minutes": 15,
        "attribution": "Product jobs by We Work Remotely",
    },
    "We Work Remotely · Customer support": {
        "url": "https://weworkremotely.com/categories/remote-customer-support-jobs.rss",
        "ttl_minutes": 15,
        "attribution": "Customer support jobs by We Work Remotely",
    },
    "We Work Remotely · Design": {
        "url": "https://weworkremotely.com/categories/remote-design-jobs.rss",
        "ttl_minutes": 15,
        "attribution": "Design jobs by We Work Remotely",
    },
    "We Work Remotely · Sales & marketing": {
        "url": "https://weworkremotely.com/categories/remote-sales-and-marketing-jobs.rss",
        "ttl_minutes": 15,
        "attribution": "Sales and marketing jobs by We Work Remotely",
    },
    "Remotive": {
        "url": "https://remotive.com/api/remote-jobs",
        "ttl_minutes": 30,
        "attribution": "Jobs by Remotive",
    },
    "Remotive · Software development": {
        "url": "https://remotive.com/api/remote-jobs?category=software-dev",
        "ttl_minutes": 30,
        "attribution": "Software development jobs by Remotive",
    },
    "Remotive · Customer support": {
        "url": "https://remotive.com/api/remote-jobs?category=customer-support",
        "ttl_minutes": 30,
        "attribution": "Customer support jobs by Remotive",
    },
    # Narrow official feeds make the first result batch useful for QA rather
    # than relying on the first page of a general remote-job catalogue.
    "Remotive · Quality assurance": {
        "url": "https://remotive.com/api/remote-jobs?category=qa",
        "ttl_minutes": 30,
        "attribution": "Quality assurance jobs by Remotive",
    },
    "Jobicy": {
        "url": "https://jobicy.com/api/v2/remote-jobs",
        "ttl_minutes": 60,
        "attribution": "Jobs by Jobicy",
    },
    "Jobicy · Testing": {
        "url": "https://jobicy.com/api/v2/remote-jobs?tag=testing",
        "ttl_minutes": 60,
        "attribution": "Testing jobs by Jobicy",
    },
    "Jobicy · Intern": {
        "url": "https://jobicy.com/api/v2/remote-jobs?tag=intern",
        "ttl_minutes": 60,
        "attribution": "Entry-level and internship candidates by Jobicy",
    },
    # Himalayas publishes a documented public search endpoint with direct
    # vacancy URLs, full descriptions and a publication/expiry timestamp.
    # It is deliberately queried by role: generic aggregator pages produce a
    # great deal of irrelevant material for a time-sensitive job search.
    "Himalayas · QA": {
        "url": "https://himalayas.app/jobs/api/search?q=qa&worldwide=true&sort=recent&page=1",
        "ttl_minutes": 20,
        "attribution": "Direct worldwide QA vacancies by Himalayas",
    },
    "Himalayas · Manual QA": {
        "url": "https://himalayas.app/jobs/api/search?q=manual%20qa&worldwide=true&sort=recent&page=1",
        "ttl_minutes": 20,
        "attribution": "Direct manual QA vacancies by Himalayas",
    },
    "Himalayas · Software tester": {
        "url": "https://himalayas.app/jobs/api/search?q=software%20tester&worldwide=true&sort=recent&page=1",
        "ttl_minutes": 20,
        "attribution": "Direct software testing vacancies by Himalayas",
    },
    "Himalayas · Junior QA": {
        "url": "https://himalayas.app/jobs/api/search?q=junior%20qa&worldwide=true&sort=recent&page=1",
        "ttl_minutes": 20,
        "attribution": "Direct junior QA vacancies by Himalayas",
    },
    "Himalayas · QA intern": {
        "url": "https://himalayas.app/jobs/api/search?q=qa%20intern&worldwide=true&sort=recent&page=1",
        "ttl_minutes": 20,
        "attribution": "Direct worldwide QA internship candidates by Himalayas",
    },
    "Himalayas · Software testing intern": {
        "url": "https://himalayas.app/jobs/api/search?q=software%20testing%20intern&worldwide=true&sort=recent&page=1",
        "ttl_minutes": 20,
        "attribution": "Direct worldwide software testing internship candidates by Himalayas",
    },
    "Himalayas · Technical support intern": {
        "url": "https://himalayas.app/jobs/api/search?q=technical%20support%20intern&worldwide=true&sort=recent&page=1",
        "ttl_minutes": 20,
        "attribution": "Direct worldwide technical support internship candidates by Himalayas",
    },
    "Himalayas · Software trainee": {
        "url": "https://himalayas.app/jobs/api/search?q=software%20trainee&worldwide=true&sort=recent&page=1",
        "ttl_minutes": 20,
        "attribution": "Direct worldwide software trainee candidates by Himalayas",
    },
    "Himalayas · IT support": {
        "url": "https://himalayas.app/jobs/api/search?q=technical%20support&worldwide=true&sort=recent&page=1",
        "ttl_minutes": 20,
        "attribution": "Direct remote IT support vacancies by Himalayas",
    },
    "Himalayas · Help desk": {
        "url": "https://himalayas.app/jobs/api/search?q=help%20desk&worldwide=true&sort=recent&page=1",
        "ttl_minutes": 20,
        "attribution": "Direct help desk vacancies by Himalayas",
    },
    "Himalayas · Product support": {
        "url": "https://himalayas.app/jobs/api/search?q=product%20support&worldwide=true&sort=recent&page=1",
        "ttl_minutes": 20,
        "attribution": "Direct product support vacancies by Himalayas",
    },
    "Himalayas · Customer support": {
        "url": "https://himalayas.app/jobs/api/search?q=customer%20support&worldwide=true&sort=recent&page=1",
        "ttl_minutes": 20,
        "attribution": "Direct customer support vacancies by Himalayas",
    },
    "Himalayas · Customer success": {
        "url": "https://himalayas.app/jobs/api/search?q=customer%20success&worldwide=true&sort=recent&page=1",
        "ttl_minutes": 20,
        "attribution": "Direct customer success vacancies by Himalayas",
    },
    "Himalayas · Customer onboarding": {
        "url": "https://himalayas.app/jobs/api/search?q=customer%20onboarding&worldwide=true&sort=recent&page=1",
        "ttl_minutes": 20,
        "attribution": "Direct customer onboarding vacancies by Himalayas",
    },
    "Himalayas · Implementation specialist": {
        "url": "https://himalayas.app/jobs/api/search?q=implementation%20specialist&worldwide=true&sort=recent&page=1",
        "ttl_minutes": 20,
        "attribution": "Direct implementation specialist vacancies by Himalayas",
    },
    "Himalayas · Application support": {
        "url": "https://himalayas.app/jobs/api/search?q=application%20support&worldwide=true&sort=recent&page=1",
        "ttl_minutes": 20,
        "attribution": "Direct application support vacancies by Himalayas",
    },
    "Himalayas · Service desk": {
        "url": "https://himalayas.app/jobs/api/search?q=service%20desk&worldwide=true&sort=recent&page=1",
        "ttl_minutes": 20,
        "attribution": "Direct service desk vacancies by Himalayas",
    },
    "Himalayas · Support operations": {
        "url": "https://himalayas.app/jobs/api/search?q=support%20operations&worldwide=true&sort=recent&page=1",
        "ttl_minutes": 20,
        "attribution": "Direct support operations vacancies by Himalayas",
    },
    "Himalayas · Customer operations": {
        "url": "https://himalayas.app/jobs/api/search?q=customer%20operations&worldwide=true&sort=recent&page=1",
        "ttl_minutes": 20,
        "attribution": "Direct customer operations vacancies by Himalayas",
    },
    "Himalayas · QA analyst": {
        "url": "https://himalayas.app/jobs/api/search?q=qa%20analyst&worldwide=true&sort=recent&page=1",
        "ttl_minutes": 20,
        "attribution": "Direct QA analyst vacancies by Himalayas",
    },
    "Himalayas · Customer service": {
        "url": "https://himalayas.app/jobs/api/search?q=customer%20service%20representative&worldwide=true&sort=recent&page=1",
        "ttl_minutes": 20,
        "attribution": "Direct customer service vacancies by Himalayas",
    },
    "Himalayas · Support specialist": {
        "url": "https://himalayas.app/jobs/api/search?q=support%20specialist&worldwide=true&sort=recent&page=1",
        "ttl_minutes": 20,
        "attribution": "Direct support specialist vacancies by Himalayas",
    },
    "Himalayas · Technical support specialist": {
        "url": "https://himalayas.app/jobs/api/search?q=technical%20support%20specialist&worldwide=true&sort=recent&page=1",
        "ttl_minutes": 20,
        "attribution": "Direct technical support specialist vacancies by Himalayas",
    },
    "Himalayas · Customer care": {
        "url": "https://himalayas.app/jobs/api/search?q=customer%20care&worldwide=true&sort=recent&page=1",
        "ttl_minutes": 20,
        "attribution": "Direct customer care vacancies by Himalayas",
    },
    "Himalayas · Community support": {
        "url": "https://himalayas.app/jobs/api/search?q=community%20support&worldwide=true&sort=recent&page=1",
        "ttl_minutes": 20,
        "attribution": "Direct community support vacancies by Himalayas",
    },
    "Himalayas · IT technician": {
        "url": "https://himalayas.app/jobs/api/search?q=it%20technician&worldwide=true&sort=recent&page=1",
        "ttl_minutes": 20,
        "attribution": "Direct IT technician vacancies by Himalayas",
    },
    "Himalayas · UAT tester": {
        "url": "https://himalayas.app/jobs/api/search?q=uat%20tester&worldwide=true&sort=recent&page=1",
        "ttl_minutes": 20,
        "attribution": "Direct UAT testing vacancies by Himalayas",
    },
    "Himalayas · QA tester": {
        "url": "https://himalayas.app/jobs/api/search?q=qa%20tester&worldwide=true&sort=recent&page=1",
        "ttl_minutes": 20,
        "attribution": "Direct QA tester vacancies by Himalayas",
    },
    "Himalayas · Test analyst": {
        "url": "https://himalayas.app/jobs/api/search?q=test%20analyst&worldwide=true&sort=recent&page=1",
        "ttl_minutes": 20,
        "attribution": "Direct test analyst vacancies by Himalayas",
    },
    "Himalayas · Client success": {
        "url": "https://himalayas.app/jobs/api/search?q=client%20success&worldwide=true&sort=recent&page=1",
        "ttl_minutes": 20,
        "attribution": "Direct client success vacancies by Himalayas",
    },
    "Himalayas · QA page 2": {
        "url": "https://himalayas.app/jobs/api/search?q=qa&worldwide=true&sort=recent&page=2",
        "ttl_minutes": 20,
        "attribution": "Second page of direct worldwide QA vacancies by Himalayas",
    },
    "Himalayas · Customer support page 2": {
        "url": "https://himalayas.app/jobs/api/search?q=customer%20support&worldwide=true&sort=recent&page=2",
        "ttl_minutes": 20,
        "attribution": "Second page of direct customer support vacancies by Himalayas",
    },
    "Himalayas · Content reviewer": {
        "url": "https://himalayas.app/jobs/api/search?q=content%20reviewer&worldwide=true&sort=recent&page=1",
        "ttl_minutes": 20,
        "attribution": "Direct content review vacancies by Himalayas",
    },
    "Himalayas · Data annotator": {
        "url": "https://himalayas.app/jobs/api/search?q=data%20annotator&worldwide=true&sort=recent&page=1",
        "ttl_minutes": 20,
        "attribution": "Direct data annotation vacancies by Himalayas",
    },
    "Himalayas · AI evaluator": {
        "url": "https://himalayas.app/jobs/api/search?q=ai%20evaluator&worldwide=true&sort=recent&page=1",
        "ttl_minutes": 20,
        "attribution": "Direct AI evaluation vacancies by Himalayas",
    },
    "Himalayas · Trust and safety": {
        "url": "https://himalayas.app/jobs/api/search?q=trust%20and%20safety&worldwide=true&sort=recent&page=1",
        "ttl_minutes": 20,
        "attribution": "Direct trust and safety vacancies by Himalayas",
    },
    "Himalayas · KYC analyst": {
        "url": "https://himalayas.app/jobs/api/search?q=kyc%20analyst&worldwide=true&sort=recent&page=1",
        "ttl_minutes": 20,
        "attribution": "Direct KYC analyst vacancies by Himalayas",
    },
    "Himalayas · Fraud analyst": {
        "url": "https://himalayas.app/jobs/api/search?q=fraud%20analyst&worldwide=true&sort=recent&page=1",
        "ttl_minutes": 20,
        "attribution": "Direct fraud analysis vacancies by Himalayas",
    },
    "Himalayas · Back office support": {
        "url": "https://himalayas.app/jobs/api/search?q=back%20office%20support&worldwide=true&sort=recent&page=1",
        "ttl_minutes": 20,
        "attribution": "Direct back-office support vacancies by Himalayas",
    },
    "Himalayas · Virtual assistant": {
        "url": "https://himalayas.app/jobs/api/search?q=virtual%20assistant&worldwide=true&sort=recent&page=1",
        "ttl_minutes": 20,
        "attribution": "Direct virtual assistant vacancies by Himalayas",
    },
    "Himalayas · QA tester page 2": {
        "url": "https://himalayas.app/jobs/api/search?q=qa%20tester&worldwide=true&sort=recent&page=2",
        "ttl_minutes": 20,
        "attribution": "Second page of direct QA tester vacancies by Himalayas",
    },
    "Himalayas · Software tester page 2": {
        "url": "https://himalayas.app/jobs/api/search?q=software%20tester&worldwide=true&sort=recent&page=2",
        "ttl_minutes": 20,
        "attribution": "Second page of direct software testing vacancies by Himalayas",
    },
    "Himalayas · IT support page 2": {
        "url": "https://himalayas.app/jobs/api/search?q=technical%20support&worldwide=true&sort=recent&page=2",
        "ttl_minutes": 20,
        "attribution": "Second page of direct IT support vacancies by Himalayas",
    },
    "Himalayas · Help desk page 2": {
        "url": "https://himalayas.app/jobs/api/search?q=help%20desk&worldwide=true&sort=recent&page=2",
        "ttl_minutes": 20,
        "attribution": "Second page of direct help desk vacancies by Himalayas",
    },
    "Himalayas · Product support page 2": {
        "url": "https://himalayas.app/jobs/api/search?q=product%20support&worldwide=true&sort=recent&page=2",
        "ttl_minutes": 20,
        "attribution": "Second page of direct product support vacancies by Himalayas",
    },
    "Himalayas · Customer care page 2": {
        "url": "https://himalayas.app/jobs/api/search?q=customer%20care&worldwide=true&sort=recent&page=2",
        "ttl_minutes": 20,
        "attribution": "Second page of direct customer care vacancies by Himalayas",
    },
    "Himalayas · Support specialist page 2": {
        "url": "https://himalayas.app/jobs/api/search?q=support%20specialist&worldwide=true&sort=recent&page=2",
        "ttl_minutes": 20,
        "attribution": "Second page of direct support specialist vacancies by Himalayas",
    },
    "Himalayas · Data annotator page 2": {
        "url": "https://himalayas.app/jobs/api/search?q=data%20annotator&worldwide=true&sort=recent&page=2",
        "ttl_minutes": 20,
        "attribution": "Second page of direct data annotation vacancies by Himalayas",
    },
    "Himalayas · Content reviewer page 2": {
        "url": "https://himalayas.app/jobs/api/search?q=content%20reviewer&worldwide=true&sort=recent&page=2",
        "ttl_minutes": 20,
        "attribution": "Second page of direct content review vacancies by Himalayas",
    },
    "Himalayas · AI evaluator page 2": {
        "url": "https://himalayas.app/jobs/api/search?q=ai%20evaluator&worldwide=true&sort=recent&page=2",
        "ttl_minutes": 20,
        "attribution": "Second page of direct AI evaluation vacancies by Himalayas",
    },
    "Himalayas · Technical support page 3": {
        "url": "https://himalayas.app/jobs/api/search?q=technical%20support&worldwide=true&sort=recent&page=3",
        "ttl_minutes": 20,
        "attribution": "Third page of direct technical support vacancies by Himalayas",
    },
    "Himalayas · Customer service broad": {
        "url": "https://himalayas.app/jobs/api/search?q=customer%20service&worldwide=true&sort=recent&page=1",
        "ttl_minutes": 20,
        "attribution": "Direct customer service vacancies by Himalayas",
    },
    "Himalayas · Customer service broad page 2": {
        "url": "https://himalayas.app/jobs/api/search?q=customer%20service&worldwide=true&sort=recent&page=2",
        "ttl_minutes": 20,
        "attribution": "Second page of direct customer service vacancies by Himalayas",
    },
    "Himalayas · Customer service broad page 3": {
        "url": "https://himalayas.app/jobs/api/search?q=customer%20service&worldwide=true&sort=recent&page=3",
        "ttl_minutes": 20,
        "attribution": "Third page of direct customer service vacancies by Himalayas",
    },
    "Himalayas · Customer service broad page 4": {
        "url": "https://himalayas.app/jobs/api/search?q=customer%20service&worldwide=true&sort=recent&page=4",
        "ttl_minutes": 20,
        "attribution": "Fourth page of direct customer service vacancies by Himalayas",
    },
    "Himalayas · Customer experience associate": {
        "url": "https://himalayas.app/jobs/api/search?q=customer%20experience%20associate&worldwide=true&sort=recent&page=1",
        "ttl_minutes": 20,
        "attribution": "Direct customer experience associate vacancies by Himalayas",
    },
    "Himalayas · Customer experience associate page 2": {
        "url": "https://himalayas.app/jobs/api/search?q=customer%20experience%20associate&worldwide=true&sort=recent&page=2",
        "ttl_minutes": 20,
        "attribution": "Second page of customer experience associate vacancies by Himalayas",
    },
    "Himalayas · Customer experience associate page 3": {
        "url": "https://himalayas.app/jobs/api/search?q=customer%20experience%20associate&worldwide=true&sort=recent&page=3",
        "ttl_minutes": 20,
        "attribution": "Third page of customer experience associate vacancies by Himalayas",
    },
    "Himalayas · Client success page 2": {
        "url": "https://himalayas.app/jobs/api/search?q=client%20success&worldwide=true&sort=recent&page=2",
        "ttl_minutes": 20,
        "attribution": "Second page of direct client success vacancies by Himalayas",
    },
    "Himalayas · Client success page 3": {
        "url": "https://himalayas.app/jobs/api/search?q=client%20success&worldwide=true&sort=recent&page=3",
        "ttl_minutes": 20,
        "attribution": "Third page of direct client success vacancies by Himalayas",
    },
    "Himalayas · QA page 3": {
        "url": "https://himalayas.app/jobs/api/search?q=qa&worldwide=true&sort=recent&page=3",
        "ttl_minutes": 20,
        "attribution": "Third page of direct worldwide QA vacancies by Himalayas",
    },
    "Himalayas · Customer support page 3": {
        "url": "https://himalayas.app/jobs/api/search?q=customer%20support&worldwide=true&sort=recent&page=3",
        "ttl_minutes": 20,
        "attribution": "Third page of direct customer support vacancies by Himalayas",
    },
    "Himalayas · Support specialist page 3": {
        "url": "https://himalayas.app/jobs/api/search?q=support%20specialist&worldwide=true&sort=recent&page=3",
        "ttl_minutes": 20,
        "attribution": "Third page of direct support specialist vacancies by Himalayas",
    },
    "Himalayas · Content reviewer page 3": {
        "url": "https://himalayas.app/jobs/api/search?q=content%20reviewer&worldwide=true&sort=recent&page=3",
        "ttl_minutes": 20,
        "attribution": "Third page of direct content review vacancies by Himalayas",
    },
    "Himalayas · Data annotator page 3": {
        "url": "https://himalayas.app/jobs/api/search?q=data%20annotator&worldwide=true&sort=recent&page=3",
        "ttl_minutes": 20,
        "attribution": "Third page of direct data annotation vacancies by Himalayas",
    },
    **TELEGRAM_SOURCE_SPECS,
}

# These boards also have a dedicated, auditable search surface in the product.
# They participate in the main refresh too, while the normal hard gates still
# reject Russia-only employment, Russian offices and stale cards.
SPECIAL_SOURCE_NAMES = ("CareerSpace", "SETTERS Media", "Hirify")

# A button click must give the candidate something useful to review quickly.
# This is the *interactive* subset of the public catalogue, not a cosmetic
# source counter.  Every entry here is queried by the manual refresh button.
# The remaining catalogue entries are either category duplicates or manual
# links which cannot be responsibly scanned without the person's own login.
# Keep this explicit rather than relying on dictionary order.
FAST_SOURCE_NAMES = (
    # Direct detail pages and sources most likely to contain worldwide,
    # relocation-friendly QA/support roles are intentionally first. Telegram
    # is retained only because concrete-post validation below rejects channel
    # digests and landing pages.
    *SPECIAL_SOURCE_NAMES,
    "Talanto",
    "Habr Career",
    "Arbeitnow",
    "Himalayas · QA",
    "Himalayas · Manual QA",
    "Himalayas · Software tester",
    "Himalayas · Junior QA",
    "Himalayas · IT support",
    "Himalayas · Help desk",
    "Himalayas · Product support",
    "Himalayas · Customer support",
    "Himalayas · Customer success",
    "Himalayas · Customer onboarding",
    "Himalayas · Implementation specialist",
    "Himalayas · Application support",
    "Himalayas · Service desk",
    "Himalayas · Support operations",
    "Himalayas · Customer operations",
    "Himalayas · QA analyst",
    "Himalayas · Customer service",
    "Himalayas · Support specialist",
    "Himalayas · Technical support specialist",
    "Himalayas · Customer care",
    "Himalayas · Community support",
    "Himalayas · IT technician",
    "Himalayas · UAT tester",
    "Himalayas · QA tester",
    "Himalayas · Test analyst",
    "Himalayas · Client success",
    "Himalayas · QA page 2",
    "Himalayas · Customer support page 2",
    "Himalayas · Content reviewer",
    "Himalayas · Data annotator",
    "Himalayas · AI evaluator",
    "Himalayas · Trust and safety",
    "Himalayas · KYC analyst",
    "Himalayas · Fraud analyst",
    "Himalayas · Back office support",
    "Himalayas · Virtual assistant",
    "Himalayas · QA tester page 2",
    "Himalayas · Software tester page 2",
    "Himalayas · IT support page 2",
    "Himalayas · Help desk page 2",
    "Himalayas · Product support page 2",
    "Himalayas · Customer care page 2",
    "Himalayas · Support specialist page 2",
    "Himalayas · Data annotator page 2",
    "Himalayas · Content reviewer page 2",
    "Himalayas · AI evaluator page 2",
    "Himalayas · Technical support page 3",
    "Himalayas · Customer service broad",
    "Himalayas · Customer service broad page 2",
    "Himalayas · Customer service broad page 3",
    "Himalayas · Customer service broad page 4",
    "Himalayas · Customer experience associate",
    "Himalayas · Customer experience associate page 2",
    "Himalayas · Customer experience associate page 3",
    "Himalayas · Client success page 2",
    "Himalayas · Client success page 3",
    "Himalayas · QA page 3",
    "Himalayas · Customer support page 3",
    "Himalayas · Support specialist page 3",
    "Himalayas · Content reviewer page 3",
    "Himalayas · Data annotator page 3",
    "Remote OK",
    "We Work Remotely",
    "We Work Remotely · Programming",
    "We Work Remotely · Product",
    "We Work Remotely · Customer support",
    "We Work Remotely · Design",
    "We Work Remotely · Sales & marketing",
    "Jobicy",
    "Jobicy · Testing",
    "Remotive",
    "Remotive · Quality assurance",
    "Remotive · Customer support",
    "Remotive · Software development",
    "Telegram · Relocate",
    "Telegram · Jobs Abroad",
    "Telegram · Remocate",
    "Telegram · IT Relocation",
    "Telegram · QA Jobs",
    "Telegram · CY HR",
    "Telegram · Serbia Jobs",
    "Telegram · For Analysts",
    "Telegram · Job for QA",
    "Telegram · Job for QA Remote",
    "Telegram · IT Support Jobs",
    "Telegram · IT Support Remote",
    "Telegram · IT Support Junior",
    "Telegram · IT Digital Jobs",
    "Telegram · IT Digital QA",
    "Telegram · Creative Remote",
    "Telegram · Workzilla Support",
    "Telegram · Junior Internships",
    "Telegram · Junior Internships · Стажировка",
    "Telegram · Junior Internships · Без опыта",
    "Telegram · Junior Internships · QA",
    "Telegram · Junior Internships · Support",
)

# A public-board refresh is I/O-bound.  Eight workers meant that half of an
# interactive 25-source pass never even started before its global deadline.
# Sixteen remains deliberately bounded, while provider-level locks below keep
# near-identical Himalayas requests from being sent as a burst.
INTERACTIVE_MAX_WORKERS = 16

PLATFORM_DIRECTORY = {
    "Live feeds": [
        ("Talanto", "https://talanto.work/", "Public vacancies; matched individually to each profile"),
        ("Habr Career", "https://career.habr.com/vacancies", "Public technology vacancies; strict geography filters still apply"),
        ("Arbeitnow", "https://www.arbeitnow.com/", "European jobs, remote and visa filters"),
        ("Remote OK", "https://remoteok.com/", "Worldwide remote jobs"),
        ("We Work Remotely", "https://weworkremotely.com/", "Established remote job board"),
        ("Remotive", "https://remotive.com/remote-jobs", "Curated remote roles"),
        ("Jobicy", "https://jobicy.com/", "Remote jobs with structured feeds"),
    ],
    "Employment": [
        ("LinkedIn Jobs", "https://www.linkedin.com/jobs/", "Global roles and recruiter network"),
        ("Indeed", "https://www.indeed.com/", "Large international job search"),
        ("Wellfound", "https://wellfound.com/jobs", "Startups and product companies"),
        ("Welcome to the Jungle", "https://www.welcometothejungle.com/en/jobs", "Modern product roles"),
        ("Himalayas", "https://himalayas.app/jobs", "Remote-first companies"),
        ("Remote.com Jobs", "https://remote.com/jobs", "Worldwide remote work"),
        ("No Fluff Jobs", "https://nofluffjobs.com/", "European tech jobs with salary ranges"),
        ("TestDevJobs", "https://testdevjobs.com/", "Testing and QA roles"),
        ("HeadHunter Global", "https://hh.ru/search/vacancy?professional_role=124", "Manual search: public vacancies abroad; automatic API can be protected by provider limits"),
        ("Arc", "https://arc.dev/remote-jobs", "Remote technology roles"),
        ("FlexJobs", "https://www.flexjobs.com/", "Curated flexible and remote jobs"),
    ],
    "Freelance and part-time": [
        ("Upwork", "https://www.upwork.com/", "Projects, hourly and fixed-price work"),
        ("Contra", "https://contra.com/", "Independent work and portfolio"),
        ("PeoplePerHour", "https://www.peopleperhour.com/", "Freelance projects"),
        ("Freelancer", "https://www.freelancer.com/", "Worldwide project marketplace"),
        ("Braintrust", "https://www.usebraintrust.com/talent", "Vetted technology network"),
        ("Toptal", "https://www.toptal.com/", "High-bar freelance network"),
    ],
}

QA_TITLE_RE = re.compile(
    r"(?:\ba?qa\b|quality assurance|software test(?:er|ing)|test engineer|test analyst|uat test(?:er|ing)|"
    r"manual tester|mobile tester|quality engineer|\bsdet\b|инженер (?:по тестировани[юя]|автоматизации тестирования)|"
    r"тестировщик|автотестировщик)",
    re.IGNORECASE,
)
QA_ADJACENT_TITLE_RE = re.compile(
    r"(?:technical support|product support|customer support|customer onboarding|onboarding support|"
    r"implementation specialist|support engineer|support specialist|client support|user support|"
    r"application support|help\s*desk|service\s*desk|customer success|customer experience|"
    r"support operations|customer operations|client operations|technical customer success|"
    r"support analyst|customer service|customer care|customer happiness|community support|"
    r"client success|customer advocate|support advocate|service agent|service advisor|"
    r"it technician|support consultant|back office support|manual qa support|техподдерж|поддержк|онбординг)",
    re.IGNORECASE,
)
ENTRY_LEVEL_ADJACENT_TITLE_RE = re.compile(
    r"(?:content (?:moderator|reviewer)|document reviewer|data (?:annotator|annotation|labeler|labeling)|"
    r"ai (?:trainer|evaluator|evaluation analyst)|quality rater|trust (?:and|&) safety|"
    r"ky[bc] analyst|fraud (?:analyst|operations)|payment operations|back[ -]?office (?:support|coordinator)|"
    r"customer operations (?:associate|coordinator|specialist)|virtual assistant)",
    re.IGNORECASE,
)
TRACKING_KEYS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "ref", "referrer", "source", "trk", "trackingid",
}

GOLDEN_SCORE = 60
REVIEW_SCORE = 45
BROAD_REVIEW_SCORE = 20
VISIBLE_REVIEW_TARGET_PER_CANDIDATE = 70
MATCHING_VERSION = "v24-deep-special-internships-2026-08-17"
AI_PAUSE_SETTING = "live_jobs_ai_pause"
AI_RATE_LIMIT_COOLDOWN_MINUTES = 10
HIGH_ENGLISH_RE = re.compile(
    r"\b(?:b2\+?|c1|c2|upper[- ]?intermediate|advanced|fluent|excellent)\b.{0,45}\benglish\b|"
    r"\benglish\b.{0,45}\b(?:b2\+?|c1|c2|upper[- ]?intermediate|advanced|fluent|excellent|daily communication)\b|"
    r"daily communication",
    re.IGNORECASE,
)
RUSSIAN_SPEAKING_RE = re.compile(
    r"russian[- ]speaking|russian language|русскоязыч|русский язык|говорить по-русски|"
    r"команда на русском|онбординг на русском|daily на русском|русск(?:ая|ий|ие) команд|"
    r"русск(?:ий|ого) рекрутер|russian recruiter",
    re.IGNORECASE,
)
STRICT_OFFICE_LOCATIONS = (
    "moscow", "москва", "saint petersburg", "st. petersburg", "st petersburg",
    "санкт-петербург", "петербург", "spb office", "moscow office",
)
RUSSIAN_CITY_RE = re.compile(
    r"\b(?:krasnodar|sochi|rostov(?:-on-don)?|moscow|saint\s+petersburg|st\.?\s*petersburg|"
    r"novosibirsk|yekaterinburg|ekaterinburg|kazan|nizhny\s+novgorod|chelyabinsk|samara|"
    r"omsk|ufa|perm|voronezh|volgograd|krasnoyarsk|saratov|tyumen|vladivostok|irkutsk|"
    r"habarovsk|khabarovsk|tomsk|tula|ryazan|kaliningrad|"
    r"краснодар|сочи|ростов(?:-на-дону)?|москва|санкт-петербург|петербург|новосибирск|"
    r"екатеринбург|казань|нижний\s+новгород|челябинск|самара|омск|уфа|пермь|воронеж|"
    r"волгоград|красноярск|саратов|тюмень|владивосток|иркутск|хабаровск|томск|тула|"
    r"рязань|калининград)\b",
    re.IGNORECASE,
)
RUSSIAN_EMPLOYMENT_RE = re.compile(
    r"\bтк\s*рф\b|оформлен(?:ие|и[ея])[^.;\n]{0,45}(?:по\s+тк|в\s+рф|в\s+россии)|"
    r"российск(?:ое|ого|ому)\s+(?:юр(?:идическ(?:ое|ого))?\s*лиц[оа]|трудов(?:ой|ому)\s+договор[у]?)|"
    r"employment\s+(?:under|through)\s+russian\s+law|russian\s+employment\s+contract",
    re.IGNORECASE,
)
STRICT_COUNTRY_ONLY = (
    "us only", "u.s. only", "usa only", "united states only", "canada only",
    "uk only", "united kingdom only", "poland only", "germany only",
    "eu residents only", "eu-based candidates only", "must reside in the eu",
    "must be based in the eu", "european union only", "remote, us",
    "remote - us", "remote (us)", "remote in the us",
)
INTERNATIONAL_MARKERS = (
    "вне рф", "вне россии", "за пределами рф", "за пределами россии", "outside russia",
    "worldwide", "anywhere", "global remote", "remote globally", "international remote",
    "кипр", "cyprus", "лимассол", "limassol", "армения", "armenia", "ереван", "yerevan",
    "грузия", "georgia", "тбилиси", "tbilisi", "казахстан", "kazakhstan", "алматы", "almaty",
    "вьетнам", "vietnam", "дананг", "da nang", "danang", "хошимин", "ho chi minh", "ханой", "hanoi",
    "сербия", "serbia", "белград", "belgrade", "черногория", "montenegro", "турция", "turkey",
    "оаэ", "uae", "дубай", "dubai", "узбекистан", "uzbekistan", "кыргызстан", "kyrgyzstan",
    "азербайджан", "azerbaijan", "баку", "baku", "беларусь", "belarus", "минск", "minsk",
    "europe", "emea",
)


def utcnow() -> datetime:
    return datetime.now(UTC)


def iso(value: datetime | None = None) -> str:
    return (value or utcnow()).astimezone(UTC).replace(microsecond=0).isoformat()


def parse_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), UTC)
        except (ValueError, OSError, OverflowError):
            return None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=parsed.tzinfo or UTC).astimezone(UTC)
    except ValueError:
        pass
    try:
        return parsedate_to_datetime(text).astimezone(UTC)
    except (TypeError, ValueError, OverflowError):
        return None


def clean_text(value: Any, limit: int = 8000) -> str:
    if value is None:
        return ""
    raw = html.unescape(str(value)).replace("\x00", " ")
    if "<" in raw and ">" in raw:
        raw = BeautifulSoup(raw, "html.parser").get_text(" ")
    return re.sub(r"\s+", " ", raw).strip()[:limit]


def clean_multiline(value: Any, limit: int = 8000) -> str:
    if value is None:
        return ""
    raw = html.unescape(str(value)).replace("\x00", " ").replace("\r\n", "\n").replace("\r", "\n")
    if "<" in raw and ">" in raw:
        raw = BeautifulSoup(raw, "html.parser").get_text("\n")
    raw = re.sub(r"[ \t]+", " ", raw)
    raw = re.sub(r"\n{3,}", "\n\n", raw)
    return raw.strip()[:limit]


def display_text(value: Any, limit: int = 8000) -> str:
    """Keep Streamlit from interpreting salary dollar signs as LaTeX delimiters."""
    return clean_text(value, limit).replace("$", "USD ")


def canonical_url(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parts = urlsplit(text)
        query = [
            (key, val) for key, val in parse_qsl(parts.query, keep_blank_values=True)
            if key.lower() not in TRACKING_KEYS and not key.lower().startswith("utm_")
        ]
        path = parts.path.rstrip("/") or "/"
        return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, urlencode(query), ""))
    except ValueError:
        return text


def stable_hash(*parts: Any) -> str:
    raw = "|".join(clean_text(part, 2000).lower() for part in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def job_fingerprint(job: dict[str, Any]) -> str:
    return stable_hash(job.get("company"), job.get("title"), job.get("location"))[:32]


def clean_job_title(value: Any, limit: int = 240) -> str:
    """Normalize scraped titles without changing the role itself."""
    title = clean_text(value, limit)
    if title.startswith("++"):
        title = "C" + title
    title = title.strip(" \t\r\n🚀🔥🔹🔸🧪📌💎•:—–-)]}")
    title = re.sub(
        r"^(?:вакансия|позиция|ищем|требуется|нужен|нужна|открыта\s+позиция|position)\s*[:—–-]*\s*",
        "",
        title,
        flags=re.IGNORECASE,
    )
    title = title.strip(" \t\r\n🚀🔥🔹🔸🧪📌💎•:—–-)]}")
    title = re.sub(r"\s+", " ", title).strip()
    title = re.sub(r"\s+([,.;:])", r"\1", title)
    while title and title[-1] in "([{":
        title = title[:-1].rstrip()
    title = title.strip(" \t\r\n:—–-)]}")
    if title.count("(") > title.count(")"):
        title = re.sub(r"\s*\([^()]*$", "", title).strip()
    if title.count("[") > title.count("]"):
        title = re.sub(r"\s*\[[^\[\]]*$", "", title).strip()
    title = re.sub(r"^специалиста\b", "Специалист", title, flags=re.IGNORECASE)
    title = re.sub(r"^инженера\b", "Инженер", title, flags=re.IGNORECASE)
    title = re.sub(r"^тестировщика\b", "Тестировщик", title, flags=re.IGNORECASE)
    title = re.sub(r"^менеджера\b", "Менеджер", title, flags=re.IGNORECASE)
    if title and title[0].islower():
        title = title[0].upper() + title[1:]
    return clean_text(title, limit)


def normalize_type(value: Any) -> str:
    low = clean_text(value, 200).lower().replace("-", " ").replace("_", " ")
    if "part" in low:
        return "part-time"
    if "freelance" in low:
        return "freelance"
    if "contract" in low:
        return "contract"
    if "intern" in low:
        return "internship"
    if "tempor" in low:
        return "temporary"
    return "full-time" if "full" in low or not low else low[:80]


def make_job(
    *, source: str, external_id: Any, url: Any, title: Any, company: Any,
    description: Any = "", location: Any = "Remote", job_type: Any = "",
    posted_at: Any = "", salary: Any = "", remote: bool = True,
    tags: Any = None,
) -> dict[str, Any]:
    raw_description = str(description or "")
    employer_email, employer_contact = extract_contacts(raw_description)
    link = canonical_url(url)
    title_text = clean_job_title(title, 240)
    company_text = clean_text(company, 180) or "Unknown company"
    external = clean_text(external_id, 240) or stable_hash(source, link, company_text, title_text)[:24]
    posted = parse_datetime(posted_at)
    if isinstance(tags, (list, tuple, set)):
        tags_text = ", ".join(clean_text(tag, 80) for tag in tags if clean_text(tag, 80))
    else:
        tags_text = clean_text(tags, 600)
    job = {
        "source": source,
        "external_id": external,
        "url": link,
        "links": ([{"url": link, "source": source, "posted_at": iso(posted) if posted else ""}] if link else []),
        "title": title_text or "Untitled role",
        "company": company_text,
        "description": clean_text(raw_description, 9000),
        "location": clean_text(location, 240) or "Remote",
        "job_type": normalize_type(job_type),
        "posted_at": iso(posted) if posted else "",
        "salary": clean_text(salary, 300),
        "remote": bool(remote),
        "tags": tags_text,
        "employer_email": employer_email,
        "employer_contact": employer_contact,
    }
    job["fingerprint"] = job_fingerprint(job)
    return job


RU_MONTHS = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4,
    "мая": 5, "июня": 6, "июля": 7, "августа": 8,
    "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
    "янв": 1, "фев": 2, "мар": 3, "апр": 4, "май": 5, "июн": 6,
    "июл": 7, "авг": 8, "сен": 9, "окт": 10, "ноя": 11, "дек": 12,
}


def parse_russian_date(value: Any, now: datetime | None = None) -> datetime | None:
    text = clean_text(value, 240).lower().replace("ё", "е")
    current = (now or utcnow()).astimezone(UTC)
    match = re.search(r"(\d{1,2})\s+([а-я]+)(?:\s+(\d{4}))?", text)
    if match and match.group(2) in RU_MONTHS:
        year = int(match.group(3) or current.year)
        parsed = datetime(year, RU_MONTHS[match.group(2)], int(match.group(1)), tzinfo=UTC)
        if not match.group(3) and parsed > current + timedelta(days=2):
            parsed = parsed.replace(year=year - 1)
        return parsed
    relative = re.search(r"(\d+)\s+(минут|минуты|минуту|час|часа|часов|день|дня|дней)\s+назад", text)
    if relative:
        count = int(relative.group(1))
        unit = relative.group(2)
        return current - (timedelta(days=count) if unit.startswith("д") else timedelta(hours=count) if unit.startswith("час") else timedelta(minutes=count))
    if "вчера" in text:
        return current - timedelta(days=1)
    if "сегодня" in text or "только что" in text:
        return current
    return None


def parse_careerspace_listing(page: str) -> list[dict[str, str]]:
    soup = BeautifulSoup(page, "html.parser")
    rows: list[dict[str, str]] = []
    for card in soup.select(".job-card"):
        anchor = card.select_one("a.job-card__i[href^='/job/']")
        title = card.select_one(".job-card__title")
        company = card.select_one(".job-card__company-name")
        if not anchor or not title:
            continue
        labels = [clean_text(item.get_text(" ", strip=True), 240) for item in card.select(".job-lb__tx")]
        rows.append({
            "external_id": str(anchor.get("href") or "").rstrip("/").split("/")[-1],
            "url": canonical_url(f"https://careerspace.app{anchor.get('href')}"),
            "title": clean_job_title(title.get_text(" ", strip=True)),
            "company": clean_text(company.get_text(" ", strip=True) if company else "", 180),
            "location": " · ".join(labels[:-1]) if len(labels) > 1 else " · ".join(labels),
            "salary": labels[-1] if len(labels) >= 3 else "",
        })
    return rows


def parse_careerspace_detail(page: str, row: dict[str, str]) -> dict[str, Any] | None:
    soup = BeautifulSoup(page, "html.parser")
    description_node = soup.select_one(".j-d__dsc-vl")
    description = clean_multiline(description_node.get_text("\n", strip=True) if description_node else "", 9000)
    meta = soup.select_one("meta[name='description']")
    meta_text = clean_text(meta.get("content") if meta else "", 1000)
    published_match = re.search(r'published_at:\s*"([^"]+)"', page)
    posted = parse_datetime(published_match.group(1)) if published_match else parse_russian_date(meta_text)
    if not posted or not description:
        return None
    location = clean_text(row.get("location"), 240)
    if re.search(r"\bросси[яи]\b", meta_text, re.IGNORECASE) and "Россия" not in location:
        location = f"{location} · Россия" if location else "Россия · Удаленно"
    remote = bool(row.get("remote_hint")) or bool(re.search(
        r"remote|удал[её]н|из\s+дома|worldwide|anywhere",
        f"{location} {meta_text} {description}",
        re.IGNORECASE,
    ))
    return make_job(
        source="CareerSpace", external_id=row.get("external_id"), url=row.get("url"),
        title=row.get("title"), company=row.get("company"),
        description=f"{description}\n\nПроверка географии: {meta_text}",
        location=location or "Удаленно", job_type="full-time", posted_at=posted,
        salary=row.get("salary"), remote=remote,
        tags=["CareerSpace", "remote" if remote else "location-bound", row.get("collection")],
    )


def parse_setters_listing(page: str) -> list[dict[str, str]]:
    soup = BeautifulSoup(page, "html.parser")
    rows: list[dict[str, str]] = []
    for card in soup.select("a.card-item[href]"):
        employment = clean_text(card.select_one(".type-text").get_text(" ", strip=True) if card.select_one(".type-text") else "", 100)
        if not re.search(r"удален|remote", employment, re.IGNORECASE):
            continue
        href = str(card.get("href") or "")
        rows.append({
            "external_id": href.rstrip("/").split("/")[-1],
            "url": canonical_url(href if href.startswith("http") else f"https://www.setters.media{href}"),
            "title": clean_job_title(card.select_one(".name-vacanc").get_text(" ", strip=True) if card.select_one(".name-vacanc") else ""),
            "company": clean_text(card.select_one(".name-company").get_text(" ", strip=True) if card.select_one(".name-company") else "", 180),
            "location": clean_text(card.select_one(".location-text").get_text(" ", strip=True) if card.select_one(".location-text") else "", 180),
            "salary": clean_text(card.select_one(".payment-level").get_text(" ", strip=True) if card.select_one(".payment-level") else "", 240),
            "experience": clean_text(card.select_one(".experience-text").get_text(" ", strip=True) if card.select_one(".experience-text") else "", 120),
            "sector": clean_text(card.select_one(".sphere-text").get_text(" ", strip=True) if card.select_one(".sphere-text") else "", 120),
        })
    return rows[:32]


def parse_setters_detail(page: str, row: dict[str, str]) -> dict[str, Any] | None:
    soup = BeautifulSoup(page, "html.parser")
    published = soup.select_one(".publich-time-text")
    posted = parse_russian_date(published.get_text(" ", strip=True) if published else "")
    description_node = soup.select_one(".vacanc-rich-text")
    response_node = soup.select_one(".respond-text")
    description = clean_multiline("\n".join(
        node.get_text("\n", strip=True) for node in (response_node, description_node) if node
    ), 9000)
    if not posted or not description:
        return None
    international = bool(re.search(
        r"worldwide|anywhere|global\s+remote|international\s+remote|outside\s+russia|"
        r"из\s+любой\s+страны|за\s+пределами\s+рф|за\s+рубеж|международн",
        description,
        re.IGNORECASE,
    ))
    location = clean_text(row.get("location"), 180)
    # An empty location on this Russia-focused board is not evidence of a
    # worldwide role. Keep it reviewable only when the text says so directly.
    if not location:
        location = "Worldwide remote" if international else "Россия · удаленно · география не подтверждена"
    return make_job(
        source="SETTERS Media", external_id=row.get("external_id"), url=row.get("url"),
        title=row.get("title"), company=row.get("company"), description=description,
        location=f"{location} · удаленно", job_type="full-time", posted_at=posted,
        salary=row.get("salary"), remote=True,
        tags=", ".join(filter(None, ("SETTERS Media", row.get("sector"), row.get("experience")))),
    )


def parse_hirify_listing(
    page: str,
    now: datetime | None = None,
    *,
    allow_unspecified_remote: bool = False,
) -> list[dict[str, Any]]:
    soup = BeautifulSoup(page, "html.parser")
    rows: list[dict[str, Any]] = []
    for card in soup.select(".vacancy-card"):
        anchor = card.select_one("a.vacancy-card-link[href^='/jobs/']")
        title = card.select_one("h3.title")
        if not anchor or not title:
            continue
        common_tags = [clean_text(item.get_text(" ", strip=True), 120) for item in card.select(".common-tags .tag")]
        location_tag = next((tag for tag in common_tags if "remote" in tag.lower()), "")
        global_remote = bool(re.search(
            r"remote\s*\((?:global|worldwide|anywhere)\)|worldwide|anywhere",
            location_tag,
            re.IGNORECASE,
        ))
        unspecified_remote = location_tag.strip().lower() == "remote"
        # Country-bound remote roles remain blocked. On Hirify's dedicated
        # work-from-anywhere feeds, a bare "remote" card is useful as a review
        # candidate, but its hiring geography stays visibly unconfirmed.
        if not global_remote and not (allow_unspecified_remote and unspecified_remote):
            continue
        skills = [clean_text(item.get_text(" ", strip=True), 120) for item in card.select(".vacancy-tags .tag")]
        date_node = card.select_one(".date-full")
        posted = parse_russian_date(date_node.get_text(" ", strip=True) if date_node else "", now=now)
        company_node = card.select_one(".company")
        company = clean_text(company_node.get_text(" ", strip=True) if company_node else "", 180)
        if not posted:
            continue
        href = str(anchor.get("href") or "")
        geography_note = "worldwide remote" if global_remote else "remote with hiring geography to verify"
        description = (
            f"Public Hirify card for a current {geography_note} role. "
            f"Role: {title.get_text(' ', strip=True)}. Skills and requirements: {', '.join(skills)}. "
            f"Published: {date_node.get_text(' ', strip=True) if date_node else 'current listing'}. "
            "Open the original Hirify card to verify the employer, contract, hiring countries and application contact."
        )
        job_type = next((tag for tag in common_tags if tag.lower() in {"fulltime", "parttime", "contract", "freelance"}), "full-time")
        salary_node = card.select_one(".salary")
        rows.append(make_job(
            source="Hirify", external_id=card.get("data-vacancy-id") or href,
            url=f"https://hirify.me{href}", title=title.get_text(" ", strip=True),
            company=company or "Компания указана в Hirify", description=description,
            location=(location_tag if global_remote else "Remote · hiring geography to verify"), job_type=job_type,
            posted_at=posted, salary=salary_node.get_text(" ", strip=True) if salary_node else "",
            remote=True, tags=[*common_tags, *skills, "Hirify public card"],
        ))
    return rows


def _detail_pages(rows: list[dict[str, str]], *, limit: int) -> list[tuple[dict[str, str], str]]:
    selected = rows[:limit]

    def fetch(row: dict[str, str]) -> tuple[dict[str, str], str]:
        client = build_search_session(request_timeout=6.5, retries_enabled=False)
        response = client.get(row["url"], headers={"User-Agent": USER_AGENT, "Accept": "text/html"}, timeout=6.5)
        response.raise_for_status()
        return row, response.text

    detailed: list[tuple[dict[str, str], str]] = []
    with ThreadPoolExecutor(max_workers=min(8, max(1, len(selected)))) as pool:
        futures = {pool.submit(fetch, row): row for row in selected}
        for future in as_completed(futures):
            try:
                detailed.append(future.result())
            except Exception:
                continue
    return detailed


def annotate_checked_count(jobs: list[dict[str, Any]], checked_count: int) -> list[dict[str, Any]]:
    """Keep source scan depth next to cached jobs without inventing matches."""
    count = max(len(jobs), int(checked_count or 0))
    return [{**job, "_checked_count": count} for job in jobs]


def source_checked_count(jobs: list[dict[str, Any]]) -> int:
    counts = [int(job.get("_checked_count") or 0) for job in jobs if isinstance(job, dict)]
    return max([len(jobs), *counts])


def fetch_careerspace(session: requests.Session | None = None) -> list[dict[str, Any]]:
    client = session or build_search_session()
    collections = (
        ("remote", SOURCE_SPECS["CareerSpace"]["url"]),
        ("junior", "https://careerspace.app/collection/junior"),
        ("analytics", "https://careerspace.app/collection/analytics"),
        ("data_analyst", "https://careerspace.app/collection/data_analyst"),
        ("developing_testing", "https://careerspace.app/collection/developing_testing"),
        ("startups", "https://careerspace.app/collection/worlds_startups_rus_founders"),
    )
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for collection, url in collections:
        response = client.get(url, headers={"User-Agent": USER_AGENT}, timeout=18)
        response.raise_for_status()
        for row in parse_careerspace_listing(response.text):
            key = canonical_url(row.get("url"))
            if not key or key in seen:
                continue
            seen.add(key)
            rows.append({
                **row,
                "collection": collection,
                "remote_hint": "1" if collection == "remote" else "",
            })
    checked_count = min(50, len(rows))
    role_markers = re.compile(
        r"qa|test|тестиров|support|поддерж|help.?desk|service.?desk|"
        r"customer success|customer service|analyst|аналитик|data|developer|"
        r"разраб|intern|trainee|стаж|junior",
        re.IGNORECASE,
    )
    candidates = [
        row for row in rows
        if row.get("collection") == "remote" or role_markers.search(str(row.get("title") or ""))
    ]
    jobs = [
        parse_careerspace_detail(page, row)
        for row, page in _detail_pages(candidates, limit=28)
    ]
    return annotate_checked_count([job for job in jobs if job], checked_count)


def fetch_setters_media(session: requests.Session | None = None) -> list[dict[str, Any]]:
    client = session or build_search_session()
    response = client.get(SOURCE_SPECS["SETTERS Media"]["url"], headers={"User-Agent": USER_AGENT}, timeout=18)
    response.raise_for_status()
    checked_count = min(50, len(BeautifulSoup(response.text, "html.parser").select("a.card-item[href]")))
    jobs = [parse_setters_detail(page, row) for row, page in _detail_pages(parse_setters_listing(response.text), limit=32)]
    return annotate_checked_count([job for job in jobs if job], checked_count)


def fetch_hirify(session: requests.Session | None = None) -> list[dict[str, Any]]:
    client = session or build_search_session()
    pages = (
        "https://hirify.me/en/qa-jobs",
        "https://hirify.me/en/qa-remote-jobs",
        "https://hirify.me/en/junior-qa-testing-jobs",
        "https://hirify.me/en/junior-jobs",
        "https://hirify.me/en/customer-support-jobs",
        "https://hirify.me/en/customer-support-remote-jobs",
        "https://hirify.me/en/remote-jobs",
    )
    jobs: list[dict[str, Any]] = []
    checked_cards: set[str] = set()
    for url in pages:
        response = client.get(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html"}, timeout=18)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        for card in soup.select(".vacancy-card"):
            anchor = card.select_one("a.vacancy-card-link[href]")
            key = str(card.get("data-vacancy-id") or (anchor.get("href") if anchor else ""))
            if key:
                checked_cards.add(key)
        jobs.extend(parse_hirify_listing(
            response.text,
            allow_unspecified_remote=("-remote-jobs" in url or url.endswith("/remote-jobs")),
        ))
    return annotate_checked_count(dedupe_jobs(jobs)[:80], min(50, len(checked_cards)))


def parse_jobicy(payload: dict[str, Any], source_name: str = "Jobicy") -> list[dict[str, Any]]:
    return [
        make_job(
            source=source_name, external_id=row.get("id"), url=row.get("url"),
            title=row.get("jobTitle"), company=row.get("companyName"),
            description=row.get("jobDescription"), location=row.get("jobGeo") or "Remote",
            job_type=row.get("jobType"), posted_at=row.get("pubDate"),
            salary=row.get("annualSalaryMin") or row.get("salary"), remote=True,
            tags=[row.get("jobIndustry"), row.get("jobLevel")],
        )
        for row in (payload.get("jobs") or []) if isinstance(row, dict)
    ]


def parse_himalayas(payload: dict[str, Any], source_name: str) -> list[dict[str, Any]]:
    """Normalise the public Himalayas search API into direct job cards.

    The API's ``applicationLink`` is a unique vacancy page.  We retain the
    location restrictions in both the location and description so the normal
    eligibility gate can reject country-limited offers instead of presenting
    an attractive but impossible remote role.
    """
    jobs: list[dict[str, Any]] = []
    for row in (payload.get("jobs") or []):
        if not isinstance(row, dict):
            continue
        restrictions = row.get("locationRestrictions") or []
        if isinstance(restrictions, list):
            restriction_text = ", ".join(clean_text(item, 100) for item in restrictions if clean_text(item, 100))
        else:
            restriction_text = clean_text(restrictions, 500)
        location = restriction_text or "Worldwide remote"
        salary = ""
        minimum, maximum, currency = row.get("minSalary"), row.get("maxSalary"), clean_text(row.get("currency"), 20)
        if minimum or maximum:
            salary = f"{minimum or ''}-{maximum or ''} {currency}".strip()
        description = clean_text(row.get("description") or row.get("excerpt"), 9000)
        if restriction_text:
            description = f"{description}\nLocation restrictions: {restriction_text}"
        jobs.append(make_job(
            source=source_name,
            external_id=row.get("guid") or row.get("applicationLink"),
            url=row.get("applicationLink") or row.get("guid"),
            title=row.get("title"), company=row.get("companyName"),
            description=description, location=location,
            job_type=row.get("employmentType"), posted_at=row.get("pubDate"),
            salary=salary, remote=True,
            tags=[*(row.get("categories") or []), *(row.get("seniority") or []), *(row.get("parentCategories") or [])],
        ))
    return jobs


def parse_remotive(payload: dict[str, Any], source_name: str = "Remotive") -> list[dict[str, Any]]:
    return [
        make_job(
            source=source_name, external_id=row.get("id"), url=row.get("url"),
            title=row.get("title"), company=row.get("company_name"),
            description=row.get("description"),
            location=row.get("candidate_required_location") or "Remote",
            job_type=row.get("job_type"), posted_at=row.get("publication_date"),
            salary=row.get("salary"), remote=True, tags=row.get("tags"),
        )
        for row in (payload.get("jobs") or []) if isinstance(row, dict)
    ]


def parse_remoteok(payload: Any) -> list[dict[str, Any]]:
    rows = payload if isinstance(payload, list) else []
    jobs = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("position"):
            continue
        salary = ""
        if row.get("salary_min") or row.get("salary_max"):
            salary = f"{row.get('salary_min') or ''}-{row.get('salary_max') or ''}"
        jobs.append(make_job(
            source="Remote OK", external_id=row.get("id") or row.get("slug"),
            url=row.get("apply_url") or row.get("url"), title=row.get("position"),
            company=row.get("company"), description=row.get("description"),
            location=row.get("location") or "Remote", job_type=row.get("type"),
            posted_at=row.get("date") or row.get("epoch"), salary=salary,
            remote=True, tags=row.get("tags"),
        ))
    return jobs


def parse_arbeitnow(payload: dict[str, Any]) -> list[dict[str, Any]]:
    jobs = []
    for row in (payload.get("data") or []):
        if not isinstance(row, dict):
            continue
        jobs.append(make_job(
            source="Arbeitnow", external_id=row.get("slug"), url=row.get("url"),
            title=row.get("title"), company=row.get("company_name"),
            description=row.get("description"), location=row.get("location"),
            job_type=", ".join(row.get("job_types") or []),
            posted_at=row.get("created_at"), remote=bool(row.get("remote")),
            tags=row.get("tags"),
        ))
    return jobs


def parse_wwr(xml_text: str, source_name: str = "We Work Remotely") -> list[dict[str, Any]]:
    root = ET.fromstring(xml_text)
    jobs = []
    for item in root.findall(".//item"):
        values = {child.tag.split("}")[-1]: (child.text or "") for child in item}
        raw_title = clean_text(values.get("title"), 300)
        if ":" in raw_title:
            company, title = raw_title.split(":", 1)
        else:
            company, title = "Unknown company", raw_title
        location = values.get("region") or values.get("country") or "Remote"
        jobs.append(make_job(
            source=source_name, external_id=values.get("guid"),
            url=values.get("link"), title=title, company=company,
            description=values.get("description"), location=location,
            job_type=values.get("type"), posted_at=values.get("pubDate"),
            remote=True, tags=[values.get("skills"), values.get("category")],
        ))
    return jobs


def _salary_from_text(value: Any) -> str:
    text = clean_text(value, 9000)
    match = re.search(
        r"(?:от\s+|до\s+)?(?:[$€£]\s?\d[\d\s.,kKкК]*(?:\s*[-–—]\s*[$€£]?\s?\d[\d\s.,kKкК]*)?"
        r"|\d[\d\s.,]*(?:\s*[-–—]\s*\d[\d\s.,]*)?\s?(?:₽|руб(?:лей|ля)?|USD|EUR|gross|net))",
        text, flags=re.IGNORECASE,
    )
    return clean_text(match.group(0), 300) if match else ""


def parse_habr_rss(xml_text: str) -> list[dict[str, Any]]:
    root = ET.fromstring(xml_text)
    jobs = []
    for item in root.findall(".//item"):
        values = {child.tag.split("}")[-1]: (child.text or "") for child in item}
        raw_title = clean_text(values.get("title"), 400)
        quoted = re.search(r"[«\"]([^»\"]+)[»\"]", raw_title)
        title = clean_text(quoted.group(1) if quoted else raw_title, 240)
        description = clean_text(values.get("description"), 9000)
        location_match = re.search(r"\(([^()]{2,120})\)\s*$", raw_title)
        location = clean_text(location_match.group(1) if location_match else "Location from vacancy", 180)
        if re.search(r"\d|[$€£₽]|\b(?:usd|eur|руб)\b", location, flags=re.IGNORECASE):
            location = "Международная география не указана"
        blob = f"{raw_title} {description}".lower()
        remote = bool(re.search(r"\bremote\b|можно\s+удал[её]нно|удал[её]нн(?:ая|ую|о)|дистанцион", blob))
        job_type = "part-time" if re.search(r"неполный|частичн|part[ -]?time", blob) else "full-time"
        jobs.append(make_job(
            source="Habr Career", external_id=values.get("guid"), url=values.get("link"),
            title=title, company=values.get("author") or "Компания на Хабр Карьере",
            description=description, location=location, job_type=job_type,
            posted_at=values.get("pubDate"), salary=_salary_from_text(description),
            remote=remote, tags=re.findall(r"#[\w+.-]+", description),
        ))
    return jobs


def _telegram_title(text: str) -> str:
    flat = clean_text(text, 9000)
    searchable = re.sub(r"https?://\S+|(?:^|\s)#[\w+.-]+", " ", flat)
    title_patterns = (
        r"\b(?:(?:Senior|Middle|Junior|Lead|Head|Principal|Staff|Manual|Automation|Mobile|Web)"
        r"(?:\s*/\s*(?:Manual|Automation|Mobile|Web))?\s+){0,3}(?:A?QA|SDET)"
        r"(?:\s+(?:Automation|Manual|Engineer|Team\s+Lead|Lead|Analyst|Specialist|Tester|Manager|\([^)]+\))){0,4}",
        r"\b(?:Инженер\s+(?:по\s+тестировани[юя]|автоматизации\s+тестирования)|"
        r"автотестировщик|тестировщик)(?:\s+[A-Za-zА-Яа-яЁё0-9+#().-]+){0,5}",
    )
    for pattern in title_patterns:
        match = re.search(pattern, searchable, flags=re.IGNORECASE)
        if match and len(clean_text(match.group(0), 220)) >= 8:
            title = clean_job_title(match.group(0), 220)
            if title:
                return title
    lines = [clean_text(line, 260).strip(" •—–-\t") for line in clean_multiline(text, 9000).splitlines()]
    for line in lines:
        without_tags = re.sub(r"(?:^|\s)#[\w+.-]+", " ", line).strip(" :•—–-")
        if (
            len(without_tags) >= 5
            and not re.search(r"\b(?:years?|experience|requirements?|responsibilit|initiatives?)\b|опыт|обязанност|требован",
                              without_tags, flags=re.IGNORECASE)
        ):
            title = clean_job_title(re.sub(
                r"^(?:вакансия|позиция|ищем|открыта\s+позиция|position)\s*[:—–-]*\s*",
                "", without_tags, flags=re.IGNORECASE,
            ), 220)
            if title and not re.fullmatch(r"(?:требуется|ищем|hiring|open position)", title, flags=re.IGNORECASE):
                return title
    return clean_job_title(lines[0] if lines else "Вакансия", 220)


def _telegram_company(text: str, title: str, channel_label: str) -> str:
    text = clean_text(text, 9000)
    patterns = (
        r"(?:в\s+компани(?:ю|и))\s+[«\"']?([A-ZА-ЯЁ0-9][^\n,.;:]{1,70})",
        r"(?:компания|company)\s*[:—–-]\s*[«\"']?([A-ZА-ЯЁ0-9][^\n,.;:]{1,70})",
        r"\b(?:A?QA|SDET|тестировщик)[^\n,.;:]{0,80}\s+(?:в|at)\s+[«\"']?([A-ZА-ЯЁ0-9][^\n,.;:]{1,55})",
        r"\b(?:A?QA|SDET)[^\n,.;:]{0,60}\s*/\s*([A-Z0-9][A-Za-z0-9&_-]*\.[A-Za-z0-9._-]+)",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            company = clean_text(match.group(1), 100).strip(" »\"'")
            company = re.split(r"\s*[🔸🔹🚀📍🏢]\s*", company, maxsplit=1)[0]
            company = re.split(
                r"\s+(?:ищет|открыла|открывает|на\s+позицию|удал[её]н\w*|remote|зарплат|salary|location|"
                r"можно|помога|релокац)\b",
                company, maxsplit=1, flags=re.IGNORECASE,
            )[0]
            invalid = re.search(
                r"\b(?:online|middle|junior|senior|вакансия|отклик|геймдеве|команде|лимассоле|"
                r"будет|формат|предлагаем|требования|обязанности)\b",
                company, flags=re.IGNORECASE,
            )
            if company and not invalid and len(company.split()) <= 5:
                return company
    match = re.search(r"\s(?:в|at)\s+([A-ZА-ЯЁ][A-Za-zА-Яа-яЁё0-9&_.-]{1,45})\s*$", title)
    return clean_text(match.group(1), 100) if match else f"Компания не указана · {channel_label}"


def _telegram_location(text: str, remote: bool) -> str:
    places = (
        ("Вьетнам", ("вьетнам", "vietnam", "дананг", "da nang", "хо ши мин", "ho chi minh", "hanoi", "ханой")),
        ("Кипр", ("кипр", "cyprus", "лимассол", "limassol")),
        ("Армения", ("армения", "armenia", "ереван", "yerevan")),
        ("Грузия", ("грузия", "georgia", "тбилиси", "tbilisi")),
        ("Казахстан", ("казахстан", "kazakhstan", "алматы", "almaty", "астана", "astana")),
        ("Сербия", ("сербия", "serbia", "белград", "belgrade")),
        ("Черногория", ("черногория", "montenegro")),
        ("Турция", ("турция", "turkey")),
        ("ОАЭ", ("оаэ", "uae", "дубай", "dubai")),
        ("Европа", ("европа", "europe", "emea")),
    )
    low = text.lower()
    found = [label for label, variants in places if any(value in low for value in variants)]
    scope = ", ".join(found[:3])
    if remote:
        return f"Удалённо · {scope}" if scope else "Международная удалёнка"
    return f"{scope} · условия локации в тексте" if scope else "Локация указана в публикации"


def parse_telegram_page(
    page: str,
    channel: str,
    channel_label: str = "Telegram",
    source_name: str = "Telegram Abroad",
) -> list[dict[str, Any]]:
    soup = BeautifulSoup(page or "", "html.parser")
    jobs = []
    for message in soup.select(".tgme_widget_message"):
        body = message.select_one(".tgme_widget_message_text")
        if not body:
            continue
        text = clean_multiline(body.get_text("\n"), 9000)
        # Public channel feeds also contain digests and adverts for the channel
        # itself.  They are useful to a reader, but are not an individual job
        # and must never be represented as a vacancy in CareerMove.
        promo_markers = (
            "подборка", "больше ваканс", "наших канал", "ниже каналы",
            "подписывай", "канал с вакансия", "вакансии за рубежом",
            "подбор ваканс", "список канал", "больше работ",
        )
        links_in_post = re.findall(r"(?:https?://)?t\.me/[A-Za-z0-9_+/-]+", text, flags=re.IGNORECASE)
        if any(marker in text.lower() for marker in promo_markers) or len(links_in_post) >= 3:
            continue
        is_vacancy = bool(re.search(r"#(?:vacancy|ваканси[яи])\b|(?:ищем|требуется|открыта\s+позиция|hiring)\b", text, flags=re.IGNORECASE))
        if not (QA_TITLE_RE.search(text) or is_vacancy):
            continue
        title = _telegram_title(text)
        if not title:
            continue
        low = text.lower()
        remote = bool(re.search(r"\bremote\b|удал[её]н|дистанцион|из\s+любой\s+страны|вне\s+(?:рф|россии)", low))
        if any(phrase in low for phrase in (
            "только офис", "office only", "on-site only", "onsite only", "удаленного формата нет",
            "удалённого формата нет", "remote is not available", "no remote",
        )):
            remote = False
        date_link = message.select_one("a.tgme_widget_message_date")
        time_node = message.select_one("time")
        data_post = clean_text(message.get("data-post"), 180)
        url = (date_link.get("href") if date_link else "") or (f"https://t.me/{data_post}" if data_post else "")
        posted_at = time_node.get("datetime", "") if time_node else ""
        job_type = "part-time" if re.search(r"part[ -]?time|частичн", low) else "contract" if re.search(r"contract|контракт|аутстафф", low) else "full-time"
        jobs.append(make_job(
            source=source_name, external_id=data_post or stable_hash(channel, url, text)[:24], url=url,
            title=title, company=_telegram_company(text, title, channel_label), description=text,
            location=_telegram_location(text, remote), job_type=job_type, posted_at=posted_at,
            salary=_salary_from_text(text), remote=remote, tags=[channel, channel_label, "русскоязычная вакансия", text],
        ))
    return jobs


def concrete_vacancy_reason(job: dict[str, Any]) -> str:
    """Return why a record is a channel/digest rather than a job posting."""
    source = clean_text(job.get("source"), 120).lower()
    # Apply the strict post-vs-channel validation only to the public Telegram
    # feeds maintained in this catalog.  Imported records may legitimately
    # carry a generic "Telegram Abroad" label and already include an audited
    # direct post URL.
    if not source.startswith("telegram ·"):
        return ""
    url = clean_text(job.get("url"), 1200)
    # A public Telegram channel URL is not an application link.  A real post
    # always has the message id in its URL (for example t.me/channel/123).
    if not re.search(r"t\.me/(?:s/)?[A-Za-z0-9_]+/\d+(?:[/?#].*)?$", url, flags=re.IGNORECASE):
        return "Ссылка ведёт на канал/подборку, а не на конкретную вакансию"
    description = clean_text(job.get("description"), 9000).lower()
    promo_markers = (
        "подборка", "больше ваканс", "наших канал", "ниже каналы",
        "подписывай", "канал с вакансия", "вакансии за рубежом",
        "подбор ваканс", "список канал", "больше работ",
        "#webinar", "вебинар", "мастер-класс", "мастер класс", "бесплатный курс",
        "оставляй заявку", "бронируй место", "регистрация на",
    )
    if any(marker in description for marker in promo_markers) or len(re.findall(r"t\.me/", description)) >= 3:
        return "Публикация является подборкой ссылок, а не описанием вакансии"
    vacancy_markers = (
        "ваканси", "#вакансия", "#vacancy", "#job", "#работа",
        "hiring", "open position", "open roles", "openings", "position",
        "ищем", "требуется", "открыт набор", "открыты позиции", "send your cv",
        "send cv", "резюме на", "resume to", "apply now",
    )
    if not any(marker in description for marker in vacancy_markers):
        return "Публикация не содержит признаков конкретной вакансии"
    # A recruiter can deliberately keep the company confidential.  That is a
    # review signal, not proof that a post is a channel digest.  Such cards are
    # kept as non-golden candidates and explicitly marked for manual checking.
    return ""


def fetch_telegram_source(source_name: str, session: requests.Session | None = None) -> list[dict[str, Any]]:
    """Read one public Telegram preview feed and preserve its own identity."""
    spec = TELEGRAM_SOURCE_SPECS[source_name]
    channel = str(spec["channel"])
    headers = {"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"}
    client = session or build_search_session()
    response = client.get(
        f"https://t.me/s/{channel}",
        params=({"q": str(spec["query"])} if spec.get("query") else None),
        headers=headers,
        timeout=18,
    )
    response.raise_for_status()
    return parse_telegram_page(response.text, channel, str(spec["label"]), source_name)


def fetch_telegram_abroad(session: requests.Session | None = None) -> list[dict[str, Any]]:
    headers = {"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"}

    def load(channel: str, spec: dict[str, str]) -> list[dict[str, Any]]:
        client = session or build_search_session()
        response = client.get(
            f"https://t.me/s/{channel}", params=({"q": spec["query"]} if spec["query"] else None), headers=headers, timeout=18,
        )
        response.raise_for_status()
        return parse_telegram_page(response.text, channel, spec["label"])

    combined: list[dict[str, Any]] = []
    if session is not None:
        for channel, spec in TELEGRAM_ABROAD_CHANNELS.items():
            combined.extend(load(channel, spec))
        return dedupe_jobs(combined)
    with ThreadPoolExecutor(max_workers=len(TELEGRAM_ABROAD_CHANNELS)) as pool:
        futures = {
            pool.submit(load, channel, spec): channel
            for channel, spec in TELEGRAM_ABROAD_CHANNELS.items()
        }
        for future in as_completed(futures):
            try:
                combined.extend(future.result())
            except Exception:
                # One public channel changing or going offline must not hide the
                # posts collected from the remaining independent channels.
                continue
    if not combined:
        raise RuntimeError("Russian-language Telegram sources are temporarily unavailable")
    return dedupe_jobs(combined)


def extract_next_json(page: str, key: str) -> Any:
    """Extract a JSON value embedded in a Next.js flight payload."""
    decoded = str(page or "").replace('\\"', '"')
    marker = f'"{key}":'
    marker_at = decoded.find(marker)
    if marker_at < 0:
        raise ValueError(f"{key} was not found in the page payload")
    start = marker_at + len(marker)
    while start < len(decoded) and decoded[start].isspace():
        start += 1
    if start >= len(decoded) or decoded[start] not in "[{":
        raise ValueError(f"{key} does not contain a JSON object or array")
    opening = decoded[start]
    closing = "]" if opening == "[" else "}"
    depth, quoted, escaped = 0, False, False
    for index in range(start, len(decoded)):
        char = decoded[index]
        if escaped:
            escaped = False
            continue
        if char == "\\" and quoted:
            escaped = True
            continue
        if char == '"':
            quoted = not quoted
            continue
        if quoted:
            continue
        if char == opening:
            depth += 1
        elif char == closing:
            depth -= 1
            if depth == 0:
                return json.loads(decoded[start:index + 1])
    raise ValueError(f"{key} JSON payload is incomplete")


def _talanto_salary(row: dict[str, Any]) -> str:
    low, high = row.get("salary_min"), row.get("salary_max")
    currency = clean_text(row.get("salary_currency"), 12)
    if low is None and high is None:
        return ""
    if low is not None and high is not None:
        return f"{low}-{high} {currency}".strip()
    prefix = "from" if low is not None else "up to"
    return f"{prefix} {low if low is not None else high} {currency}".strip()


def parse_talanto_listing(page: str) -> list[dict[str, Any]]:
    rows = extract_next_json(page, "initialJobs")
    jobs = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        detail_url = f"https://talanto.work/jobs/{row.get('id')}"
        jobs.append(make_job(
            source="Talanto", external_id=row.get("id"), url=detail_url,
            title=row.get("title"), company=row.get("company"),
            description=" ".join(clean_text(value, 120) for value in (row.get("skills") or [])),
            location=row.get("location") or "Remote eligibility not stated",
            job_type=row.get("employment_type"), posted_at=row.get("published_at"),
            salary=_talanto_salary(row), remote=str(row.get("remote_type") or "").lower() == "remote",
            tags=[*(row.get("skills") or []), row.get("level"), row.get("remote_type"), "detail_unverified"],
        ))
    return jobs


def _talanto_jobposting(page: str) -> dict[str, Any]:
    soup = BeautifulSoup(page or "", "html.parser")
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            payload = json.loads(script.string or script.get_text() or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        candidates = payload if isinstance(payload, list) else [payload]
        for item in candidates:
            if isinstance(item, dict) and item.get("@type") == "JobPosting":
                return item
    return {}


def _schema_location(schema: dict[str, Any]) -> str:
    requirements = schema.get("applicantLocationRequirements")
    items = requirements if isinstance(requirements, list) else [requirements]
    names = [clean_text(item.get("name"), 120) for item in items if isinstance(item, dict) and item.get("name")]
    return ", ".join(dict.fromkeys(names))


def _schema_salary(schema: dict[str, Any]) -> str:
    salary = schema.get("baseSalary") if isinstance(schema.get("baseSalary"), dict) else {}
    value = salary.get("value") if isinstance(salary.get("value"), dict) else {}
    currency = clean_text(salary.get("currency"), 12)
    low, high = value.get("minValue"), value.get("maxValue")
    if low is not None and high is not None:
        return f"{low}-{high} {currency}".strip()
    amount = value.get("value")
    return f"{amount} {currency}".strip() if amount is not None else ""


def parse_talanto_detail(page: str, detail_url: str = "") -> dict[str, Any] | None:
    try:
        row = extract_next_json(page, "initialJob")
    except (ValueError, TypeError, json.JSONDecodeError):
        row = {}
    schema = _talanto_jobposting(page)
    if not isinstance(row, dict):
        row = {}
    if not row and not schema:
        return None
    if not isinstance(row, dict) or row.get("is_active") is False or row.get("closed_at"):
        return None
    contacts = row.get("contacts") if isinstance(row.get("contacts"), dict) else {}
    contact_text = " ".join(clean_text(value, 240) for value in contacts.values() if value)
    schema_description = clean_text(schema.get("description"), 8000)
    row_description = clean_text(row.get("description"), 8000)
    description = " ".join(filter(None, [
        schema_description or (row_description if len(row_description) >= 80 else ""),
        f"Contact: {contact_text}" if contact_text else "",
    ]))
    organization = schema.get("hiringOrganization") if isinstance(schema.get("hiringOrganization"), dict) else {}
    location = row.get("location") or _schema_location(schema) or "Remote eligibility not stated"
    remote_type = str(row.get("remote_type") or "").lower()
    remote = remote_type == "remote" or str(schema.get("jobLocationType") or "").upper() == "TELECOMMUTE"
    return make_job(
        source="Talanto", external_id=row.get("id") or stable_hash(detail_url)[:24], url=row.get("url") or detail_url or schema.get("url"),
        title=row.get("title") or schema.get("title"), company=row.get("company") or organization.get("name"), description=description,
        location=location,
        job_type=row.get("employment_type") or schema.get("employmentType"), posted_at=row.get("published_at") or schema.get("datePosted"),
        salary=_talanto_salary(row) or _schema_salary(schema), remote=remote,
        tags=[*(row.get("skills") or []), row.get("level"), row.get("remote_type")],
    )


def fetch_talanto(session: requests.Session | None = None) -> list[dict[str, Any]]:
    client = session or build_search_session()
    headers = {"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"}
    response = client.get(SOURCE_SPECS["Talanto"]["url"], headers=headers, timeout=18)
    response.raise_for_status()
    listing = parse_talanto_listing(response.text)
    detailed: list[dict[str, Any]] = []

    # The search page is structured but intentionally compact. Resolve its QA
    # detail records concurrently so contacts, active/closed state and the
    # original source URL are available without hammering the service.
    def load(job: dict[str, Any]) -> dict[str, Any] | None:
        detail_client = session or build_search_session()
        detail = detail_client.get(job["url"], headers=headers, timeout=15)
        detail.raise_for_status()
        return parse_talanto_detail(detail.text, job["url"])

    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(load, job): job for job in listing[:20]}
        for future in as_completed(futures):
            fallback = futures[future]
            try:
                item = future.result()
                if item:
                    detailed.append(item)
            except Exception:
                detailed.append(fallback)
    return detailed


def fetch_source(name: str, session: requests.Session | None = None) -> list[dict[str, Any]]:
    client = session or build_search_session()
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json, application/rss+xml, text/xml;q=0.9"}
    if name == "CareerSpace":
        return fetch_careerspace(session)
    if name == "SETTERS Media":
        return fetch_setters_media(session)
    if name == "Hirify":
        return fetch_hirify(session)
    if name == "Talanto":
        return fetch_talanto(session)
    if name in TELEGRAM_SOURCE_SPECS:
        return fetch_telegram_source(name, session)
    if name == "Habr Career":
        response = client.get(SOURCE_SPECS[name]["url"], headers=headers, timeout=18)
        response.raise_for_status()
        return parse_habr_rss(response.text)
    if name == "Arbeitnow":
        combined = []
        for page in range(1, 6):
            response = client.get(SOURCE_SPECS[name]["url"], params={"page": page}, headers=headers, timeout=18)
            response.raise_for_status()
            combined.extend(parse_arbeitnow(response.json()))
        return combined
    if name == "Remote OK":
        response = client.get(SOURCE_SPECS[name]["url"], headers=headers, timeout=18)
        response.raise_for_status()
        return parse_remoteok(response.json())
    if name == "We Work Remotely" or name.startswith("We Work Remotely ·"):
        response = client.get(SOURCE_SPECS[name]["url"], headers=headers, timeout=18)
        response.raise_for_status()
        return parse_wwr(response.text, name)
    if name == "Remotive" or name.startswith("Remotive ·"):
        response = client.get(
            SOURCE_SPECS[name]["url"], params={"limit": 100},
            headers=headers, timeout=18,
        )
        response.raise_for_status()
        return parse_remotive(response.json(), name)
    if name == "Jobicy" or name.startswith("Jobicy ·"):
        response = client.get(
            SOURCE_SPECS[name]["url"], params={"count": 100},
            headers=headers, timeout=18,
        )
        response.raise_for_status()
        return parse_jobicy(response.json(), name)
    if name.startswith("Himalayas ·"):
        response = client.get(SOURCE_SPECS[name]["url"], headers=headers, timeout=18)
        response.raise_for_status()
        return parse_himalayas(response.json(), name)
    raise ValueError(f"Unknown source: {name}")


def ensure_schema(execute: Callable[..., Any]) -> None:
    # Production uses a pre-migrated Postgres database. Repeating DDL inside a
    # search transaction is expensive and a harmless duplicate-column error
    # would abort the entire Postgres transaction even when caught in Python.
    if os.getenv("CAREERMOVE_SCHEMA_ON_START", "1").strip().lower() in {"0", "false", "no"}:
        return
    try:
        execute("""
            CREATE TABLE IF NOT EXISTS company_ratings(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER, company TEXT, country TEXT, industry_tag TEXT,
                rating INTEGER, stability INTEGER, remote_friendly INTEGER,
                b1_friendly INTEGER, official_score INTEGER, notes TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
    except Exception:
        # PostgreSQL uses identity/serial columns rather than SQLite's AUTOINCREMENT.
        execute("""
            CREATE TABLE IF NOT EXISTS company_ratings(
                id BIGSERIAL PRIMARY KEY,
                user_id INTEGER, company TEXT, country TEXT, industry_tag TEXT,
                rating INTEGER, stability INTEGER, remote_friendly INTEGER,
                b1_friendly INTEGER, official_score INTEGER, notes TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
    execute("""
        CREATE TABLE IF NOT EXISTS live_source_cache(
            source TEXT PRIMARY KEY,
            fetched_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            payload TEXT NOT NULL,
            error TEXT DEFAULT ''
        )
    """)
    execute("""
        CREATE TABLE IF NOT EXISTS live_job_index(
            user_id INTEGER NOT NULL,
            candidate_id INTEGER NOT NULL,
            source TEXT NOT NULL,
            external_id TEXT NOT NULL,
            vacancy_id INTEGER,
            fingerprint TEXT NOT NULL,
            payload_hash TEXT NOT NULL,
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            source_posted TEXT DEFAULT '',
            change_kind TEXT DEFAULT 'new',
            active INTEGER DEFAULT 1,
            PRIMARY KEY(user_id,candidate_id,source,external_id)
        )
    """)
    execute("CREATE INDEX IF NOT EXISTS idx_live_job_seen ON live_job_index(user_id,candidate_id,last_seen)")
    execute("CREATE INDEX IF NOT EXISTS idx_live_job_fingerprint ON live_job_index(user_id,candidate_id,fingerprint)")
    for migration in [
        "ALTER TABLE vacancies ADD COLUMN perk_match TEXT DEFAULT ''",
        "ALTER TABLE vacancies ADD COLUMN fit_type TEXT DEFAULT ''",
        "ALTER TABLE vacancies ADD COLUMN work_type TEXT DEFAULT ''",
        "ALTER TABLE vacancies ADD COLUMN final_salary_advice TEXT DEFAULT ''",
        "ALTER TABLE vacancies ADD COLUMN ai_analysis TEXT DEFAULT ''",
        "ALTER TABLE vacancies ADD COLUMN ai_review_status TEXT DEFAULT 'not_analyzed'",
        "ALTER TABLE vacancies ADD COLUMN employer_email TEXT DEFAULT ''",
        "ALTER TABLE vacancies ADD COLUMN employer_contact TEXT DEFAULT ''",
    ]:
        try:
            execute(migration)
        except Exception:
            pass


def _cached_source(query: Callable[..., Any], name: str) -> dict[str, Any] | None:
    frame = query("SELECT * FROM live_source_cache WHERE source=?", (name,))
    if frame.empty:
        return None
    row = frame.iloc[0].to_dict()
    try:
        row["jobs"] = json.loads(row.get("payload") or "[]")
    except (TypeError, json.JSONDecodeError):
        row["jobs"] = []
    return row


def _save_cache(
    execute: Callable[..., Any], name: str, jobs: list[dict[str, Any]], now: datetime,
    error: str = "",
) -> None:
    expires = now + timedelta(minutes=int(SOURCE_SPECS[name]["ttl_minutes"]))
    execute("""
        INSERT INTO live_source_cache(source,fetched_at,expires_at,payload,error)
        VALUES(?,?,?,?,?)
        ON CONFLICT(source) DO UPDATE SET
          fetched_at=excluded.fetched_at,
          expires_at=excluded.expires_at,
          payload=excluded.payload,
          error=excluded.error
    """, (name, iso(now), iso(expires), json.dumps(jobs, ensure_ascii=False), clean_text(error, 500)))


def collect_live_jobs(
    query: Callable[..., Any], execute: Callable[..., Any], *, force: bool = False,
    now: datetime | None = None, session: requests.Session | None = None,
    on_source: Callable[[str, list[dict[str, Any]], dict[str, Any]], None] | None = None,
    source_names: tuple[str, ...] | list[str] | None = None,
    max_wait_seconds: float | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    def verified(rows: list[dict[str, Any]], checked_at: Any) -> list[dict[str, Any]]:
        timestamp = parse_datetime(checked_at)
        return [
            {**item, "verified_at": iso(timestamp or current), "source_active": True}
            for item in rows
        ]

    ensure_schema(execute)
    current = (now or utcnow()).astimezone(UTC)
    all_jobs: list[dict[str, Any]] = []
    diagnostics_by_source: dict[str, dict[str, Any]] = {}
    pending: dict[str, dict[str, Any] | None] = {}

    def publish_source(name: str, jobs: list[dict[str, Any]]) -> None:
        """Expose a completed provider without making a slow source block the UI.

        The callback runs on the coordinator thread (never a fetch worker), so
        callers may safely persist a small first batch while other sources are
        still loading. A progress callback is deliberately best-effort: one
        consumer failure must not turn a search into a failed collection.
        """
        if on_source:
            try:
                on_source(name, jobs, dict(diagnostics_by_source[name]))
            except Exception:
                pass
    requested_names = tuple(source_names or tuple(SOURCE_SPECS))
    names = tuple(name for name in requested_names if name in SOURCE_SPECS)
    if not names:
        return [], []
    for name in names:
        spec = SOURCE_SPECS[name]
        cached = _cached_source(query, name)
        fetched_at = parse_datetime(cached.get("fetched_at")) if cached else None
        expires_at = parse_datetime(cached.get("expires_at")) if cached else None
        cache_is_fresh = bool(expires_at and current < expires_at)
        # «Обновить» is an explicit user action, not the background scheduler.
        # Give it a small debounce so a double click cannot hammer a board,
        # then allow a real re-check even when the normal 20-minute cache TTL
        # has not elapsed.  Otherwise a failed first packet can trap a user at
        # two or three cards for the whole TTL.
        # Ten seconds is enough to protect public boards from an accidental
        # double-click, while still making a deliberate second refresh useful
        # during an active job search.  A one-minute debounce was long enough
        # to keep a partial first packet (two or three jobs) on screen and
        # made the UI look as if the updated source catalogue was ignored.
        manual_refresh_ready = not fetched_at or current >= fetched_at + timedelta(seconds=10)
        # A successful cache may safely be reused until its official refresh
        # window expires.  An *empty cache produced by an error* is different:
        # keeping it for the whole TTL makes a transient timeout look like
        # "there are no vacancies". Retry it after a short cool-down instead.
        cached_error = clean_text(cached.get("error"), 240) if cached else ""
        retry_empty_error = bool(
            cached_error
            and not (cached or {}).get("jobs")
            and fetched_at
            and current >= fetched_at + timedelta(minutes=min(3, int(spec["ttl_minutes"])))
        )
        # A click on «Обновить» must show the last verified packet immediately.
        # Public vacancy boards are often slower than a free serverless request
        # allows.  For the interactive pass we therefore use a non-empty cache
        # even after its refresh window and label it as such; the scheduled
        # full run performs the network refresh.  This is deliberately never
        # used for an empty/error cache, so an outage cannot masquerade as an
        # empty market.
        # A regular interactive visit may use the last verified packet for an
        # instant first screen.  A deliberate click on «Обновить», however,
        # must actually re-check an expired source; otherwise a small stale
        # cache can make the market look as if it contains only two jobs.
        fast_cached_packet = bool(
            max_wait_seconds is not None and not force and cached and cached.get("jobs")
        )
        use_cached_packet = (
            (not force and (fast_cached_packet or cache_is_fresh))
            or (force and not manual_refresh_ready)
        )
        if cached and not retry_empty_error and use_cached_packet:
            jobs = verified(cached["jobs"], cached.get("fetched_at"))
            all_jobs.extend(jobs)
            diagnostics_by_source[name] = {
                "source": name,
                "status": ("stale" if jobs else "error") if cached_error or (fast_cached_packet and not cache_is_fresh) else "cached",
                "count": len(jobs),
                "message": cached_error or (
                    "Быстрый старт: показан последний проверенный пакет; фоновая полная проверка обновит источник."
                    if fast_cached_packet and not cache_is_fresh
                    else ("Повторное обновление будет доступно через несколько секунд" if force and not manual_refresh_ready else "Fresh cache")
                ),
                "fetched_at": cached.get("fetched_at", ""),
            }
            publish_source(name, jobs)
            continue
        pending[name] = cached

    # Providers are independent.  The interactive route has a hard deadline:
    # a slow public feed must never make the whole search spinner run forever.
    # We intentionally don't wait for unfinished workers when returning a fast
    # batch; the next scheduled run retries those sources and the cached cards
    # remain available immediately.
    worker_limit = INTERACTIVE_MAX_WORKERS if max_wait_seconds is not None else 8
    pool = ThreadPoolExecutor(max_workers=max(1, min(worker_limit, len(pending))))

    # Three concurrent role queries keep the expanded support catalogue inside
    # the interactive deadline without sending an abusive burst to Himalayas.
    # Other sources retain a single-request lock.
    provider_locks: dict[str, Any] = {}

    def provider_key(name: str) -> str:
        if name.startswith("Himalayas ·"):
            return "himalayas"
        return name

    def fetch_provider(name: str) -> list[dict[str, Any]]:
        # A fast interactive batch must finish even when a public board has a
        # slow DNS/TLS path.  Each worker gets its own short-lived client: a
        # requests.Session is not shared across threads, and there are no
        # retry sleeps that could keep a serverless request alive.
        key = provider_key(name)
        lock = provider_locks.setdefault(key, BoundedSemaphore(3) if key == "himalayas" else Lock())
        with lock:
            if max_wait_seconds is not None and session is None:
                timeout = max(2.5, min(4.5, float(max_wait_seconds) - 1.0))
                return fetch_source(name, build_search_session(request_timeout=timeout, retries_enabled=False))
            return fetch_source(name, session)

    futures = {pool.submit(fetch_provider, name): name for name in pending}
    pending_futures = set()
    try:
        if max_wait_seconds is None:
            completed_futures = set(futures)
        else:
            completed_futures, pending_futures = wait(futures, timeout=max(0.1, max_wait_seconds))
        for future in as_completed(completed_futures):
            name = futures[future]
            cached = pending[name]
            try:
                jobs = verified(future.result(), current)
                _save_cache(execute, name, jobs, current)
                all_jobs.extend(jobs)
                diagnostics_by_source[name] = {
                    "source": name, "status": "updated", "count": len(jobs),
                    "message": "Live feed updated", "fetched_at": iso(current),
                }
                publish_source(name, jobs)
            except Exception as error:  # one provider must never break the full search
                stale = verified(cached["jobs"], cached.get("fetched_at")) if cached else []
                message = safe_network_error(error)
                # Failed attempts are cached too, so a provider outage or a manual
                # refresh cannot accidentally hammer a fair-use endpoint.
                _save_cache(execute, name, stale, current, error=message)
                all_jobs.extend(stale)
                diagnostics_by_source[name] = {
                    "source": name, "status": "stale" if stale else "error", "count": len(stale),
                    "message": message, "fetched_at": cached.get("fetched_at", "") if cached else "",
                }
                publish_source(name, stale)
        for future in pending_futures:
            name = futures[future]
            cached = pending[name]
            stale = verified(cached["jobs"], cached.get("fetched_at")) if cached else []
            # Do not overwrite a usable cache with a timeout.  It is still a
            # real previously checked result, and makes the first screen useful
            # while a full scheduled collection can retry the feed later.
            all_jobs.extend(stale)
            diagnostics_by_source[name] = {
                "source": name,
                "status": "stale" if stale else "deferred",
                "count": len(stale),
                "message": "Источник отвечает дольше быстрого лимита; повторная проверка запланирована.",
                "fetched_at": cached.get("fetched_at", "") if cached else "",
            }
            publish_source(name, stale)
            future.cancel()
    finally:
        # shutdown(wait=False) is essential for serverless: the HTTP response
        # must not be held hostage by an upstream endpoint's 18-second timeout.
        pool.shutdown(wait=False, cancel_futures=True)
    diagnostics = [diagnostics_by_source[name] for name in names]
    return dedupe_jobs(all_jobs), diagnostics


def dedupe_jobs(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def sort_key(job: dict[str, Any]) -> datetime:
        return parse_datetime(job.get("posted_at")) or datetime(1970, 1, 1, tzinfo=UTC)

    unique: dict[str, dict[str, Any]] = {}
    for job in sorted(jobs, key=sort_key, reverse=True):
        job = dict(job)
        link_key = canonical_url(job.get("url"))
        key = job.get("fingerprint") or job_fingerprint(job)
        duplicate_key = next((saved for saved, item in unique.items() if link_key and canonical_url(item.get("url")) == link_key), None)
        final_key = duplicate_key or str(key)
        if final_key not in unique:
            job["links"] = [
                {
                    "url": canonical_url(link.get("url")),
                    "source": clean_text(link.get("source") or job.get("source"), 90),
                    "posted_at": clean_text(link.get("posted_at") or job.get("posted_at"), 80),
                }
                for link in (job.get("links") or [{"url": job.get("url"), "source": job.get("source")}])
                if canonical_url(link.get("url"))
            ]
            unique[final_key] = job
            continue
        saved = unique[final_key]
        links = list(saved.get("links") or [])
        candidates = job.get("links") or [{"url": job.get("url"), "source": job.get("source"), "posted_at": job.get("posted_at")}]
        known_urls = {canonical_url(item.get("url")) for item in links}
        for link in candidates:
            url = canonical_url(link.get("url"))
            if url and url not in known_urls:
                links.append({
                    "url": url,
                    "source": clean_text(link.get("source") or job.get("source"), 90),
                    "posted_at": clean_text(link.get("posted_at") or job.get("posted_at"), 80),
                })
                known_urls.add(url)
        saved["links"] = links
        saved["sources"] = list(dict.fromkeys([
            clean_text(item.get("source"), 90) for item in links if clean_text(item.get("source"), 90)
        ]))
        saved_verified = parse_datetime(saved.get("verified_at"))
        incoming_verified = parse_datetime(job.get("verified_at"))
        if incoming_verified and (not saved_verified or incoming_verified > saved_verified):
            saved["verified_at"] = iso(incoming_verified)
        if len(clean_text(job.get("description"), 9000)) > len(clean_text(saved.get("description"), 9000)):
            saved["description"] = job.get("description")
    return list(unique.values())


def candidate_profile(query: Callable[..., Any], user_id: int, candidate_id: int) -> dict[str, Any]:
    frame = query("SELECT * FROM candidates WHERE user_id=? AND id=?", (user_id, candidate_id))
    if frame.empty:
        raise ValueError("Candidate profile was not found")
    profile = frame.iloc[0].to_dict()
    skills = query("SELECT skill FROM skills WHERE user_id=? AND candidate_id=?", (user_id, candidate_id))
    profile["skills"] = [clean_text(value, 100) for value in skills.get("skill", []).tolist()] if not skills.empty else []
    resumes = query("SELECT content FROM resumes WHERE user_id=? AND candidate_id=?", (user_id, candidate_id))
    profile["resume_text"] = "\n".join(clean_text(value, 10000) for value in resumes.get("content", []).tolist()) if not resumes.empty else ""
    preferences = query(
        """
        SELECT key,value FROM settings
        WHERE user_id=? AND key IN (
          'search_stop_companies','search_stop_countries','search_salary_min',
          'search_serbia_hybrid','search_vietnam_hybrid','search_base_country'
        )
        """,
        (user_id,),
    )
    settings = {str(row["key"]): str(row["value"] or "") for _, row in preferences.iterrows()}
    global_exclusions = "; ".join(filter(None, (
        settings.get("search_stop_companies", ""),
        settings.get("search_stop_countries", ""),
    )))
    if global_exclusions:
        profile["hard_exclude"] = "; ".join(filter(None, (
            clean_text(profile.get("hard_exclude"), 2000), global_exclusions,
        )))
    try:
        profile["salary_min"] = max(
            int(profile.get("salary_min") or 0),
            int(settings.get("search_salary_min") or 0),
        )
    except ValueError:
        pass
    # A profile may still carry the older Serbian setting, but the current
    # migration uses Vietnam.  Keep the old key readable so no account fails
    # while its preferences are being updated.
    profile["allow_vietnam_hybrid"] = settings.get("search_vietnam_hybrid", settings.get("search_serbia_hybrid", "1")) == "1"
    profile["base_country"] = settings.get("search_base_country", "Vietnam")
    return profile


def role_matches(job: dict[str, Any], profile: dict[str, Any]) -> bool:
    return role_match_kind(job, profile) != "none"


def role_match_kind(job: dict[str, Any], profile: dict[str, Any]) -> str:
    target = clean_text(profile.get("target_title"), 500).lower()
    title = clean_text(job.get("title"), 300).lower()
    qa_target = "qa" in target or "test" in target or "quality" in target
    support_target = any(word in target for word in ("support", "help desk", "service desk", "техподдерж", "поддержк"))
    # "Quality Assurance" can mean industrial ISO/production quality rather
    # than software testing.  Never label those roles as a QA match.
    industrial_quality = any(marker in title for marker in (
        "quality management", "quality systems", "quality control", "quality specialist",
        "iso 9001", "manufacturing quality", "production quality",
    ))
    if qa_target and industrial_quality:
        return "none"
    if qa_target and support_target:
        if QA_TITLE_RE.search(title):
            return "target"
        if QA_ADJACENT_TITLE_RE.search(title) or ENTRY_LEVEL_ADJACENT_TITLE_RE.search(title):
            return "adjacent"
        return "none"
    if qa_target:
        if QA_TITLE_RE.search(title):
            return "target"
        if QA_ADJACENT_TITLE_RE.search(title) or ENTRY_LEVEL_ADJACENT_TITLE_RE.search(title):
            return "adjacent"
        return "none"
    if support_target:
        if QA_ADJACENT_TITLE_RE.search(title) or ENTRY_LEVEL_ADJACENT_TITLE_RE.search(title):
            return "target"
        return "none"
    aliases = {
        "повар": ("повар", "chef", "cook", "sous chef", "kitchen"),
        "chef": ("повар", "chef", "cook", "sous chef", "kitchen"),
        "водитель": ("водитель", "driver", "chauffeur"),
        "дизайнер": ("дизайнер", "designer", "ux", "ui"),
        "бухгалтер": ("бухгалтер", "accountant", "bookkeeper"),
    }
    for marker, variants in aliases.items():
        if marker in target:
            return "target" if any(variant in title for variant in variants) else "none"
    stop = {"junior", "middle", "senior", "lead", "engineer", "specialist", "remote", "and", "the", "работа"}
    tokens = [token for token in re.findall(r"[a-zа-яё0-9+#.]{3,}", target) if token not in stop]
    return "target" if tokens and any(token in title for token in tokens) else "none"


def russian_language_international(job: dict[str, Any]) -> bool:
    blob = " ".join(clean_text(job.get(key), 9000) for key in ("title", "description", "location", "tags")).lower()
    cyrillic = len(re.findall(r"[а-яё]", blob)) >= 12
    international = any(marker in blob for marker in INTERNATIONAL_MARKERS)
    source = clean_text(job.get("source"), 120)
    return cyrillic and international and (
        source.startswith("Telegram ·") or source.startswith("Telegram ") or source in {"Habr Career", "Talanto"}
    )


def russian_speaking_job(job: dict[str, Any]) -> bool:
    blob = " ".join(clean_text(job.get(key), 9000) for key in ("title", "description", "location", "tags", "source")).lower()
    cyrillic = len(re.findall(r"[а-яё]", blob)) >= 18
    source = clean_text(job.get("source"), 120)
    return bool(
        RUSSIAN_SPEAKING_RE.search(blob)
        or russian_language_international(job)
        or (cyrillic and (source.startswith("Telegram ·") or source in {"Habr Career", "Talanto"}))
    )


def _russia_scope_required(job: dict[str, Any], blob: str) -> bool:
    location = clean_text(job.get("location"), 500).lower()
    explicit_abroad = any(marker in blob for marker in (
        "вне рф", "вне россии", "за пределами рф", "за пределами россии", "outside russia",
        "worldwide", "anywhere", "global remote", "remote globally", "international remote",
    ))
    if explicit_abroad:
        return False
    if re.search(r"\b(?:россия|russia|russian federation)\b|(?:^|\W)рф(?:\W|$)", location):
        return True
    if job.get("source") == "Habr Career" and re.search(r"\(россия\)|\brussia\b", blob):
        return True
    patterns = (
        r"(?:только|обязательно|необходимо|required)[^.;\n]{0,55}(?:росси|\bрф\b|russia)",
        r"(?:локаци|место\s+работы|офис|оформлен|находиться|проживать|работать)[^.;\n]{0,55}(?:в\s+росси|\bрф\b|russia)",
        r"(?:удал[её]нно|remote)[^.;\n]{0,45}(?:по\s+россии|из\s+россии|russia\s+only)",
        r"(?:россия|russia|\bрф\b)[^.;\n]{0,25}(?:only|только)",
    )
    return any(re.search(pattern, blob, flags=re.IGNORECASE) for pattern in patterns)


def _russian_office_required(job: dict[str, Any], blob: str) -> bool:
    location = clean_text(job.get("location"), 500)
    if RUSSIAN_CITY_RE.search(location):
        return True
    city = RUSSIAN_CITY_RE.pattern
    office = r"(?:office|on[ -]?site|onsite|hybrid|офис|гибрид|место\s+работы)"
    return bool(
        re.search(rf"{office}[^.;\n]{{0,70}}{city}|{city}[^.;\n]{{0,70}}{office}", blob, re.IGNORECASE)
    )


def serbia_hybrid_job(job: dict[str, Any]) -> bool:
    blob = " ".join(clean_text(job.get(key), 9000) for key in ("title", "description", "location", "tags")).lower()
    serbia_city = any(place in blob for place in (
        "belgrade", "белград", "novi sad", "novi-sad", "нови-сад", "нови сад",
    ))
    hybrid = any(marker in blob for marker in (
        "hybrid", "гибрид", "2 days in office", "3 days in office",
        "дня в офис", "дней в офис",
    ))
    office_only = any(marker in blob for marker in (
        "office only", "on-site only", "onsite only", "только офис",
    ))
    return serbia_city and hybrid and not office_only


def vietnam_local_job(job: dict[str, Any]) -> bool:
    """Office/hybrid is allowed only in the current target country, Vietnam."""
    blob = " ".join(clean_text(job.get(key), 9000) for key in ("title", "description", "location", "tags")).lower()
    vietnam_city = any(place in blob for place in (
        "vietnam", "вьетнам", "da nang", "дананг", "danang", "hanoi", "ханой", "ho chi minh", "хо ши мин",
    ))
    return vietnam_city


def candidate_local_job(job: dict[str, Any], profile: dict[str, Any]) -> bool:
    base = clean_text(profile.get("base_country"), 80).lower()
    if "vietnam" in base or "вьетнам" in base:
        return bool(profile.get("allow_vietnam_hybrid", True)) and vietnam_local_job(job)
    return bool(profile.get("allow_serbia_hybrid", False)) and serbia_hybrid_job(job)


def explicitly_remote_job(job: dict[str, Any]) -> bool:
    """Recognise a remote vacancy even when a board omitted its boolean flag.

    Several public boards place ``Remote`` only in the location label.  Treat
    that as evidence, but never infer remote work from a generic company page.
    """
    if bool(job.get("remote")):
        return True
    evidence = " ".join(
        clean_text(job.get(key), 3000)
        for key in ("location", "title", "description", "tags")
    ).lower()
    return bool(re.search(
        r"(?:^|[\s,;/|()])(?:remote|fully\s+remote|worldwide|work\s+from\s+home|"
        r"удал[её]н(?:но|ная|ный)?|из\s+любой\s+точки)(?:$|[\s,;/|().:])",
        evidence,
        flags=re.IGNORECASE,
    ))


def hard_block(job: dict[str, Any], profile: dict[str, Any]) -> str:
    concrete_reason = concrete_vacancy_reason(job)
    if concrete_reason:
        return concrete_reason
    blob = " ".join(
        clean_text(job.get(key), 9000)
        for key in ("company", "title", "description", "location", "tags")
    ).lower()
    tag_tokens = {
        token.strip().lower()
        for token in re.split(r"[,;|/]", clean_text(job.get("tags"), 4000))
        if token.strip()
    }
    title = clean_text(job.get("title"), 300).lower()
    target = clean_text(profile.get("target_title"), 500).lower()
    profile_level = seniority(target)
    job_level = seniority(title)
    # This is a hard eligibility gate, not merely a score penalty.  A first
    # IT role must never receive lead/senior/director cards as recommendations.
    if job_level == "lead":
        return "Lead/Head/Director роли исключены из стратегии поиска"
    if profile_level == "junior" and job_level in {"middle", "senior", "lead"}:
        return "Уровень вакансии выше стартового профиля кандидата"
    if re.search(r"\b(?:c1|c2)\b.{0,30}english|english.{0,30}\b(?:c1|c2)\b|native english", blob):
        if str(profile.get("english_level") or "").upper() in {"A1", "A2", "B1"}:
            return "Требуемый английский выше уровня кандидата"
    local_vietnam = candidate_local_job(job, profile)
    office_in_vietnam = vietnam_local_job(job)
    if not office_in_vietnam and any(phrase in blob for phrase in ("office only", "on-site only", "onsite only", "work from office")):
        return "Обязательны офис или гибрид"
    if "hybrid only" in blob and not local_vietnam:
        return "Гибрид разрешён только во Вьетнаме для текущей стратегии"
    remote_choice = any(phrase in blob for phrase in (
        "remote or", "fully remote", "choose remote", "удаленная работа либо", "удалённая работа либо",
        "на выбор удал", "🏢 remote", "полностью удал",
    ))
    if not remote_choice and not local_vietnam and not office_in_vietnam and (
        "#office" in blob or "#onsite" in blob or
        re.search(r"(?:full[ -]?time|location|format|формат|🏢)[^.;]{0,55}\b(?:on[ -]?site|office|гибрид)\b", blob)
    ):
        return "Публикация указывает офисный или гибридный формат"
    if not local_vietnam and (
        "hybrid" in tag_tokens or "office" in tag_tokens or "on-site" in tag_tokens or "onsite" in tag_tokens
    ):
        return "Источник отмечает вакансию как офисную или гибридную"
    # The collection uses remote_only=False to allow Vietnam office/hybrid
    # roles.  For the current Vietnam strategy, a non-remote role outside the
    # country is ineligible even when the board omitted an explicit office tag.
    # Keep the legacy Serbia profile behavior intact so stored older cabinets
    # and compatibility tests retain their documented migration path.
    base_country = clean_text(profile.get("base_country"), 80).lower()
    if ("vietnam" in base_country or "вьетнам" in base_country) and not explicitly_remote_job(job) and not office_in_vietnam:
        return "Не подтверждён удалённый формат или работа во Вьетнаме"
    vietnam_relocation = office_in_vietnam
    if not vietnam_relocation and any(phrase in blob for phrase in ("relocation required", "must relocate", "обязательная релокация", "релокация обязательна")):
        return "Обязательная релокация не подходит стратегии поиска"
    if not vietnam_relocation and re.search(
        r"(?:релокац|переезд|переехать)[^.;\n]{0,45}(?:обязател|требуется|необходим)"
        r"|(?:обязател|требуется|необходимо|нужно)[^.;\n]{0,45}(?:релокац|переезд|переехать)"
        r"|(?:затем|после\s+старта|в\s+конечном\s+итоге)[^.;\n]{0,70}(?:офис|on[ -]?site)",
        blob,
    ):
        return "После удалённого старта требуется переезд или офис"
    if any(place in blob for place in STRICT_OFFICE_LOCATIONS) and not any(
        signal in blob for signal in ("worldwide", "anywhere", "remote globally", "global remote")
    ):
        return "Москва/Санкт-Петербург: офис, онбординг или ограничение по локации"
    if _russian_office_required(job, blob):
        return "Офис или гибрид в городе России исключён"
    if RUSSIAN_EMPLOYMENT_RE.search(blob):
        return "Оформление по ТК РФ или через российское юрлицо исключено"
    if _russia_scope_required(job, blob):
        return "Работа или оформление ограничены Россией"
    if vacancy_presentation(job)["sector"] == "betting/gambling":
        return "Betting/gambling не показывается в основной подборке"
    description = clean_text(job.get("description"), 9000)
    link = canonical_url(job.get("url") or job.get("link"))
    if not link:
        return "Нет прямой ссылки на конкретную вакансию"
    if len(description) < 70:
        return "В объявлении слишком мало описания для корректной проверки"
    presentation = vacancy_presentation(job)
    russian_fit = russian_speaking_job(job)
    condition_signals = sum(bool(value) for value in (
        presentation["schedule"],
        presentation["equipment"],
        presentation["benefits"],
        clean_text(job.get("salary"), 300),
        presentation["contacts"]["emails"] or presentation["contacts"]["phones"] or presentation["contacts"]["telegram"],
        len(description) >= 520,
    ))
    if russian_fit and condition_signals >= 1 and (
        len(description) >= 320
        or presentation["contacts"]["emails"]
        or presentation["contacts"]["telegram"]
    ):
        condition_signals = 2
    if condition_signals < 2:
        reviewable_remote_role = (
            role_matches(job, profile)
            and (explicitly_remote_job(job) or candidate_local_job(job, profile) or any(marker in blob for marker in INTERNATIONAL_MARKERS))
            and len(description) >= 90
        )
        if not reviewable_remote_role:
            return "В объявлении недостаточно условий: нужен график, техника, оплата, контакты или подробное описание"
    # Gambling/betting products are a separate consent-sensitive sector.  Do
    # not let a broad exclusion such as "букмекерские компании" miss an
    # English-only iGaming label from a public job board.
    exclusion_blob = clean_text(profile.get("hard_exclude"), 2000).lower()
    gambling_markers = ("букмек", "ставк", "казино", "gambl", "betting", "sportsbook", "igaming", "i-gaming")
    if any(marker in exclusion_blob for marker in gambling_markers) and any(marker in blob for marker in gambling_markers):
        return "Личное исключение кандидата: betting/gambling"
    if any(marker in exclusion_blob for marker in gambling_markers) and vacancy_presentation(job)["sector"] == "betting/gambling":
        return "Личное исключение кандидата: betting/gambling"
    # A person's explicit stop-list has precedence over a generic market
    # classification so the card explains the actual choice they configured.
    for item in re.split(r"[,;\n]+", clean_text(profile.get("hard_exclude"), 2000)):
        blocked = item.strip().lower()
        # Geography and contract phrases need context.  A raw substring check
        # used to reject valid text such as "outside Russia" merely because a
        # profile contained "РФ"; the dedicated gates above handle these.
        if blocked in {"россия", "рф", "russia", "тк рф", "оформление по тк рф"}:
            continue
        if blocked and blocked in blob:
            return f"Личное исключение кандидата: {blocked}"
    company_blob = " ".join((clean_text(job.get("company"), 300), clean_text(job.get("description"), 1500))).lower()
    russian_company_markers = ("сбер", "sber", "yandex", "яндекс", "vkontakte", "вконтакте", "ozon", "тинькофф", "tinkoff", "газпром", "российская компания")
    if any(marker in company_blob for marker in russian_company_markers):
        return "Компания относится к исключённому российскому рынку"
    if job.get("source") == "Habr Career" and not any(marker in blob for marker in INTERNATIONAL_MARKERS):
        return "Хабр Карьера: доступность удалённой работы за пределами РФ не подтверждена"
    if any(phrase in blob for phrase in STRICT_COUNTRY_ONLY):
        return "Удалённая работа ограничена страной, для которой право работы не подтверждено"
    allowed_base = clean_text(profile.get("base_country"), 80).lower()
    allowed_pattern = re.escape(allowed_base) if allowed_base else "vietnam"
    if re.search(
        rf"(?:must|should|need to)\s+(?:be\s+)?(?:physically\s+)?(?:live|reside|located|based|living|residing)\s+in\s+(?!{allowed_pattern}\b)[a-z ]{{2,45}}",
        blob,
    ):
        return "Требование жить в определённой стране не подтверждено для кандидата"
    if re.search(
        rf"(?:looking for|seeking)\s+[^.;\n]{{0,90}}\b(?:based|located)\s+in\s+(?!{allowed_pattern}\b)[a-z ]{{2,45}}",
        blob,
    ) or re.search(
        rf"\b(?:candidates?|testers?|applicants?|employees?)\s+(?:who are\s+)?(?:based|located)\s+in\s+(?!{allowed_pattern}\b)[a-z ]{{2,45}}",
        blob,
    ):
        return "Вакансия требует находиться в определённой стране"
    if not vietnam_relocation and re.search(
        r"(?:кандидат|рассматрива)[^.;\n]{0,90}(?:прожива|находя)[^.;\n]{0,45}(?:на\s+кипре|в\s+[а-яё-]{3,30})",
        blob,
    ):
        return "Вакансия требует уже находиться в определённой стране"
    # Short projects remain visible in the part-time feed. They are not stable
    # employment, but they are useful leads when side work is requested.
    only_country = [
        "ukrainian citizens only", "only ukrainian citizens", "ukraine residents only",
        "must be located in ukraine", "candidates from ukraine only",
    ]
    if any(phrase in blob for phrase in only_country):
        return "Ограничение по гражданству или стране проживания"
    # Do not turn a junior/entry search into an aspirational feed.  A role is
    # only allowed to be managerial when the candidate explicitly asked for a
    # leadership role in their target, not merely because its title mentions QA.
    leadership_requested = bool(re.search(r"\b(?:lead|head|manager|director|principal|staff)\b|руковод|тимлид", target))
    # A support role is not automatically entry-level. Demo QA's experienced
    # QA/support profile used to be treated like a first-job profile solely
    # because its title contained "Support", hiding valid 2-4 year roles.
    entry_target = bool(re.search(r"\b(?:junior|entry(?:[ -]?level)?|intern|trainee)\b|джун|стаж[её]р", target))
    elevated_role = bool(re.search(r"\b(?:senior|lead|head|manager|director|principal|staff|architect)\b|старш|руковод|тимлид|архитект", title))
    student_only = bool(re.search(r"\b(?:working\s+student|student|werkstudent)\b|студент", title))
    if elevated_role and not leadership_requested:
        return "Уровень вакансии выше текущей цели кандидата"
    if student_only and "student" not in target and "студент" not in target:
        return "Вакансия рассчитана только на студентов"
    if entry_target and required_years(job) >= 3:
        return "Для стартового профиля требуется слишком большой подтверждённый стаж"
    if not leadership_requested and required_years(job) >= 5:
        return "Требуется опыт уровня senior/lead, который не соответствует цели профиля"
    if ("automation" in title or re.search(r"\b(?:sdet|aqa)\b|автоматизац", title)) and not any(term in target for term in ("automation", "sdet", "aqa", "автоматизац")):
        return "Роль с основным упором на автоматизацию не совпадает с целью кандидата"
    return ""


SKILL_ALIASES = {
    "api": ["api", "rest", "swagger", "openapi"],
    "sql": ["sql", "postgres", "database"],
    "postgresql": ["postgres", "postgresql"],
    "postman": ["postman"],
    "mobile": ["mobile", "ios", "android"],
    "charles proxy": ["charles", "proxy"],
    "jira": ["jira"],
    "testit": ["testit", "test it"],
    "docker": ["docker", "container"],
    "git": ["git", "github", "gitlab"],
    "manual testing": ["manual", "functional testing"],
    "regression testing": ["regression"],
}

REQUIREMENT_SIGNALS = {
    "API testing": ["api testing", "api", "rest api", "restful", "swagger", "openapi"],
    "SQL/databases": ["sql", "postgres", "mysql", "database testing"],
    "Postman": ["postman"],
    "mobile testing": ["mobile testing", "mobile", "ios", "android"],
    "test documentation": ["test case", "test plan", "checklist", "bug report"],
    "regression testing": ["regression"],
    "Charles Proxy": ["charles proxy", "charles"],
    "Git": ["git", "github", "gitlab"],
    "Docker": ["docker", "container"],
    "CI/CD": ["ci/cd", "continuous integration", "jenkins", "github actions"],
    "test automation": ["test automation", "automation testing", "automated tests"],
    "Playwright": ["playwright"],
    "Selenium": ["selenium"],
    "Cypress": ["cypress"],
    "Python": ["python"],
    "Java": ["java"],
    "JavaScript/TypeScript": ["javascript", "typescript"],
    "Kubernetes": ["kubernetes", "k8s"],
}


def extract_contacts(value: Any) -> tuple[str, str]:
    raw = html.unescape(str(value or "")).replace("\\/", "/")
    emails = re.findall(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", raw, flags=re.IGNORECASE)
    telegram = re.findall(r"(?:https?://)?t\.me/[A-Za-z0-9_+/-]+|(?<!\w)@[A-Za-z][A-Za-z0-9_]{4,}", raw)
    linkedin = re.findall(r"https?://(?:www\.)?linkedin\.com/[A-Za-z0-9_?&=./%-]+", raw, flags=re.IGNORECASE)
    email = emails[0] if emails else ""
    contacts = []
    for item in telegram[:2] + linkedin[:1]:
        cleaned = item.rstrip(".,);]")
        if cleaned and cleaned not in contacts:
            contacts.append(cleaned)
    return email, ", ".join(contacts)


def vacancy_presentation(job: dict[str, Any]) -> dict[str, Any]:
    """Extract only facts explicitly present in a public vacancy text.

    These labels intentionally distinguish an absent fact from a negative one;
    a missing mention of a laptop, for example, must never be shown as "no
    equipment".  This helper is also used by the API preview, so cards and the
    expanded view cannot contradict each other.
    """
    raw = " ".join(clean_text(job.get(key), 9000) for key in ("title", "description", "tags", "location", "salary"))
    text = raw.lower()
    emails = list(dict.fromkeys(re.findall(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", raw, flags=re.IGNORECASE)))
    phones = list(dict.fromkeys(re.findall(r"(?<!\w)(?:\+?\d[\d\s().-]{7,}\d)(?!\w)", raw)))
    phones = [re.sub(r"\s+", " ", value).strip(" .,-") for value in phones][:4]
    telegram = list(dict.fromkeys(re.findall(r"(?:https?://)?t\.me/[A-Za-z0-9_+/-]+|(?<!\w)@[A-Za-z][A-Za-z0-9_]{4,}", raw)))[:3]
    benefits: list[str] = []
    equipment: list[str] = []
    if any(token in text for token in ("laptop", "macbook", "ноутбук", "equipment", "workstation", "техник")):
        equipment.append("Техника/ноутбук упомянуты — уточните модель и условия выдачи")
        benefits.append("техника")
    if any(token in text for token in ("health insurance", "medical insurance", "дмс", "страховк")):
        benefits.append("медицинская страховка")
    if any(token in text for token in ("learning budget", "training budget", "education budget", "обучен", "сертификат")):
        benefits.append("обучение/сертификация")
    if any(token in text for token in ("paid leave", "vacation", "отпуск")):
        benefits.append("оплачиваемый отпуск")
    relocation = any(token in text for token in ("relocation", "visa sponsorship", "переезд", "релокац", "виза"))
    schedule = ""
    if any(token in text for token in ("flexible hours", "flexible schedule", "гибкий график")):
        schedule = "Гибкий график упомянут"
    elif any(token in text for token in ("9 to 18", "9am", "40 hours", "full-time", "полный день", "пн–пт", "пн-пт")):
        schedule = "Стандартная полная занятость упомянута; часы уточните"
    sector = "general"
    risk = ""
    if any(token in text for token in ("betting", "sportsbook", "casino", "gambling", "букмекер", "ставк", "казино")):
        sector = "betting/gambling"
        risk = "Есть признаки букмекерского/gambling-проекта. Проверьте продукт, лицензию, юрисдикцию и личную приемлемость сферы."
    elif any(token in text for token in ("fintech", "bank", "banking", "payment", "payments", "финтех", "банк", "платеж")):
        sector = "fintech"
    elif any(token in text for token in ("crypto", "web3", "blockchain", "крипто")):
        sector = "crypto/web3"
        risk = "Есть признаки crypto/Web3-проекта. Перед откликом проверьте юрисдикцию, стабильность и формат контракта."
    tags = []
    for label, condition in (("Техника", bool(equipment)), ("Релокация/виза", relocation), ("Нормированный график", bool(schedule)), ("Финтех", sector == "fintech"), ("Betting/Gambling", sector == "betting/gambling")):
        if condition:
            tags.append(label)
    return {
        "contacts": {"emails": emails[:5], "phones": phones, "telegram": telegram},
        "benefits": list(dict.fromkeys(benefits)), "equipment": equipment,
        "relocation": relocation, "schedule": schedule, "sector": sector,
        "risk": risk, "tags": tags,
    }


def job_priority(job: dict[str, Any], presentation: dict[str, Any] | None = None) -> dict[str, Any]:
    """Describe review order without pretending perks are skill matches."""
    facts = presentation or vacancy_presentation(job)
    score = int(job.get("score") or 0)
    equipment = bool(facts.get("equipment"))
    relocation = bool(facts.get("relocation"))
    match_tier = 2 if score >= GOLDEN_SCORE else 1 if score >= REVIEW_SCORE else 0
    perk_tier = 3 if equipment and relocation else 2 if relocation else 1 if equipment else 0
    if match_tier == 2 and equipment and relocation:
        label = "Топ: совпадение + релокация + техника"
    elif match_tier == 2 and relocation:
        label = "Топ: совпадение + релокация"
    elif match_tier == 2 and equipment:
        label = "Топ: совпадение + техника"
    elif match_tier == 2:
        label = "Золотая"
    elif match_tier == 1:
        label = "Высокая"
    else:
        label = "Проверить"
    return {
        "priority_label": label,
        "priority_rank": match_tier * 10 + perk_tier,
        "equipment_priority": equipment,
        "relocation_priority": relocation,
    }


def moonlight_fit(job: dict[str, Any]) -> tuple[bool, str]:
    """Return true only when combining with another job is explicitly supported."""
    raw = " ".join(clean_text(job.get(key), 9000) for key in ("title", "description", "tags", "job_type", "location"))
    text = raw.lower()
    blockers = (
        "exclusive employment", "full dedication", "full-time only", "no other employment",
        "not allowed to work elsewhere", "не совмещ", "запрещено совмещ", "полная вовлеченность",
    )
    if any(item in text for item in blockers):
        return False, ""
    signals: list[tuple[str, str]] = [
        (r"\bpart[ -]?time\b|неполная занятость|частичная занятость", "part-time / неполная занятость"),
        (r"\bfreelance\b|фриланс|самозанят", "freelance / проектный формат"),
        (r"\bcontract(?:or)?\b|контракт|project[- ]based|проектная работа", "contract/project-based"),
        (r"(?:от|до|around|about|up to)\s*\d{1,2}\s*(?:час|hours?)|(?:\d{1,2}\s*(?:час|hours?)\s*(?:в|per)\s*(?:день|day|week|недел))", "маленькая почасовая нагрузка указана в вакансии"),
        (r"flexible (?:hours|schedule)|гибкий график|flexible working", "гибкий график"),
        (r"asynchronous|async work|асинхрон", "асинхронная работа"),
        (r"non[- ]exclusive|no exclusivity|without exclusivity|можно совмещать|совмещение разрешено|не запрещено совмещать", "нет эксклюзивности / совмещение разрешено"),
        (r"side project|side job|moonlight|moonlighting|подработка", "side project / подработка"),
    ]
    for pattern, reason in signals:
        if re.search(pattern, text, flags=re.IGNORECASE):
            return True, reason
    return False, ""


def profile_years(profile: dict[str, Any]) -> int:
    blob = " ".join([
        clean_text(profile.get("target_title"), 500),
        clean_text(profile.get("notes"), 1000),
        clean_text(profile.get("resume_text"), 12000),
    ])
    patterns = [
        r"\b(\d{1,2})\s*\+?\s*(?:years?|yrs?)\s+(?:of\s+)?(?:hands-on\s+)?(?:qa|quality assurance|software testing|manual testing)",
        r"(?:qa|quality assurance|software testing|manual testing)[^.;\n]{0,55}\b(\d{1,2})\s*\+?\s*(?:years?|yrs?)",
        r"\b(\d{1,2})\s*\+?\s*(?:лет|года|год)\s+(?:опыта\s+)?(?:в\s+)?(?:qa|тестирован)",
    ]
    values = [int(value) for pattern in patterns for value in re.findall(pattern, blob, flags=re.IGNORECASE)]
    inferred = max(values) if values else 0
    target = clean_text(profile.get("target_title"), 300).lower()
    if "middle" in target:
        inferred = max(inferred, 2)
    elif "junior+" in target or "strong junior" in target:
        inferred = max(inferred, 1)
    return min(inferred, 20)


def required_years(job: dict[str, Any]) -> int:
    blob = " ".join([clean_text(job.get("title"), 300), clean_text(job.get("description"), 9000)])
    values = [int(value) for value in re.findall(r"\b(\d{1,2})\s*\+?\s*(?:years?|yrs?|лет|года|год)\b", blob, flags=re.IGNORECASE)]
    realistic = [value for value in values if 0 < value <= 10]
    return max(realistic) if realistic else 0


def seniority(value: Any) -> str:
    text = clean_text(value, 500).lower()
    if re.search(r"\b(?:lead|head|manager|director|principal|staff|architect)\b|руковод|тимлид|архитект", text):
        return "lead"
    if re.search(r"\bsenior\b|старш", text):
        return "senior"
    if re.search(r"\b(?:middle|mid-level|mid level|mid)\b|мидл", text):
        return "middle"
    if re.search(r"\b(?:junior|entry|trainee|intern)\b|джун|стаж", text):
        return "junior"
    return "unspecified"


def requirement_fit(job: dict[str, Any], profile: dict[str, Any]) -> tuple[list[str], list[str]]:
    job_blob = " ".join(clean_text(job.get(key), 9000) for key in ("title", "description", "tags")).lower()
    profile_blob = " ".join([
        *(clean_text(skill, 120) for skill in (profile.get("skills") or [])),
        clean_text(profile.get("resume_text"), 12000),
        clean_text(profile.get("target_title"), 500),
    ]).lower()
    present, matched = [], []
    for label, variants in REQUIREMENT_SIGNALS.items():
        if any(variant in job_blob for variant in variants):
            present.append(label)
            if any(variant in profile_blob for variant in variants):
                matched.append(label)
    return matched, [label for label in present if label not in matched]


def skill_fit(job: dict[str, Any], profile: dict[str, Any]) -> tuple[list[str], list[str]]:
    job_blob = " ".join(clean_text(job.get(key), 9000) for key in ("title", "description", "tags")).lower()
    profile_skills = [clean_text(skill, 120) for skill in (profile.get("skills") or [])]
    profile_blob = " ".join(profile_skills + [clean_text(profile.get("resume_text"), 12000)]).lower()
    matched = []
    for skill in profile_skills:
        low = skill.lower()
        variants = SKILL_ALIASES.get(low, [low])
        if low and any(variant in job_blob for variant in variants):
            matched.append(skill)
    gaps = []
    for label, variants in REQUIREMENT_SIGNALS.items():
        if any(variant in job_blob for variant in variants) and not any(variant in profile_blob for variant in variants):
            gaps.append(label)
    return list(dict.fromkeys(matched))[:8], gaps[:6]


def experience_fit(job: dict[str, Any], profile: dict[str, Any]) -> str:
    have, required = profile_years(profile), required_years(job)
    level = seniority(profile.get("target_title"))
    scope = "уровень Junior+ QA" if level == "junior" else "уровень Middle QA" if level == "middle" else "QA-профиль"
    if not required:
        return f"В вакансии не указан точный стаж. В профиле подтверждено примерно {have}+ лет и {scope}." if have else "В вакансии не указан точный стаж — уровень нужно уточнить у работодателя."
    if have >= required:
        return f"Опыт совпадает: требуется {required}+ лет, в профиле примерно {have}+ лет релевантной QA-работы."
    return f"Пробел по стажу: требуется {required}+ лет, в профиле примерно {have}+ лет. При отклике важно опереться на fintech, API/backend и ответственность за качество продукта."


def salary_guidance(job: dict[str, Any], profile: dict[str, Any]) -> str:
    disclosed = clean_text(job.get("salary"), 300)
    target = clean_text(profile.get("target_title"), 300).lower()
    minimum = int(profile.get("salary_min") or 0)
    base = max(minimum, 1600 if "middle" in target else 1000)
    blob = " ".join(clean_text(job.get(key), 6000) for key in ("title", "description", "tags")).lower()
    if any(term in blob for term in ("api", "backend", "fintech", "payment", "mobile", "ai product")):
        base += 200
    high = base + (800 if int(job.get("score") or 0) >= 85 else 500)
    kind = normalize_type(job.get("job_type"))
    presentation = vacancy_presentation(job)
    equipment_note = (
        " Техника указана: можно начинать с нижней части диапазона, если условия подтверждены."
        if presentation["equipment"] else
        " Техника не упомянута: заложите в разговоре запас около $150–250/мес. либо запросите ноутбук отдельно."
    )
    if disclosed and disclosed.lower() not in {"not specified", "salary not disclosed", "none"}:
        return f"Опубликованная зарплата: {disclosed}. Сверьте валюту и период с ориентиром около ${base}–${high} в месяц для этого профиля.{equipment_note}"
    if kind in {"freelance", "contract", "part-time", "temporary"}:
        hourly_low = max(15, round(base / 160))
        hourly_high = max(hourly_low + 5, round(high / 140))
        return f"Зарплата не указана. Ориентир для переговоров: ${hourly_low}–${hourly_high} в час или эквивалент ${base}–${high} в месяц — зависит от гарантированной загрузки и контракта."
    return f"Зарплата не указана. Ориентир для переговоров: ${base}–${high} в месяц. До согласия уточните валюту, налоги, оформление, отпуск и оборудование.{equipment_note}"


def salary_ceiling_usd(value: Any) -> int:
    text = clean_text(value, 300).lower().replace(",", "")
    if not text or not any(marker in text for marker in ("$", "usd")):
        return 0
    numbers = []
    for raw, suffix in re.findall(r"(\d+(?:\.\d+)?)\s*([kк]?)", text):
        amount = float(raw) * (1000 if suffix else 1)
        if 100 <= amount <= 100000:
            numbers.append(int(amount))
    return max(numbers) if numbers else 0


def default_cover_letter(job: dict[str, Any], profile: dict[str, Any], matched: list[str]) -> str:
    name = clean_text(profile.get("name"), 120) or "Candidate"
    target_role = clean_text(profile.get("target_title"), 120) or "specialist"
    skills = ", ".join(matched[:5]) or target_role
    years = profile_years(profile)
    experience = f"{years}+ years of" if years else "hands-on"
    vacancy_text = " ".join(str(job.get(key) or "") for key in ("title", "description", "location"))
    cyrillic = len(re.findall(r"[А-Яа-яЁё]", vacancy_text))
    vietnam_context = (
        "Переезжаю во Вьетнам (Дананг) 1 сентября, документы для легального оформления уже в процессе. "
        "Рассматриваю remote, hybrid или office-формат во Вьетнаме; буду благодарна за подтверждение помощи с work permit/визой и релокацией, если она предусмотрена."
    )
    vietnam_context_en = (
        "I am relocating to Da Nang, Vietnam on 1 September and my legal-work documents are in progress. "
        "I am open to remote, hybrid, or Vietnam office work and would appreciate confirmation of any work-permit, visa, or relocation support."
    )
    russian_team_note = (
        " Мой текущий английский — A1, я активно учусь; русскоязычный onboarding или daily были бы большим плюсом."
        if str(profile.get("english_level") or "").upper() == "A1" and cyrillic >= 12 else ""
    )
    if cyrillic >= 12:
        return (
            f"Здравствуйте, команда {job.get('company') or 'компании'}!\n\n"
            f"Хочу откликнуться на позицию {job.get('title') or target_role}. "
            f"У меня {experience} релевантного опыта; сильные стороны: {skills}. "
            "Готов(а) подробно рассказать о практических результатах на интервью.\n\n"
            f"{vietnam_context}{russian_team_note}\n\n"
            f"С уважением,\n{name}"
        )
    return (
        f"Dear {job.get('company') or 'Hiring'} Team,\n\n"
        f"I am applying for the {job.get('title') or target_role} position. I bring {experience} relevant experience "
        f"with practical strengths in {skills}. I would be glad to share concrete results in an interview.\n\n"
        f"{vietnam_context_en}\n\n"
        f"Best regards,\n{name}"
    )


def enrich_job_for_profile(job: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(job)
    matched, gaps = skill_fit(enriched, profile)
    exp_fit = experience_fit(enriched, profile)
    salary = salary_guidance(enriched, profile)
    email, contact = extract_contacts(" ".join([
        str(enriched.get("description") or ""), str(enriched.get("url") or "")
    ]))
    reasons = list(enriched.get("reasons") or [])
    strengths = "Совпавшие навыки: " + ", ".join(matched) if matched else "; ".join(reasons) or "Подходящая QA-роль и сигнал удалённой работы"
    weaknesses = "Нужно проверить или усилить: " + ", ".join(gaps) if gaps else "Критических пробелов по опубликованному описанию не найдено; детали всё равно нужно сверить с работодателем."
    recommendation = "Сильная цель: проверьте оригинал и готовьте отклик с акцентом на API/backend/mobile QA." if int(enriched.get("score") or 0) >= GOLDEN_SCORE else "Возможное совпадение: перед откликом проверьте географию, оформление и недостающие требования."
    presentation = vacancy_presentation(enriched)
    industry = presentation["sector"] if presentation["sector"] != "general" else ("AI" if "artificial intelligence" in (enriched.get("description") or "").lower() or " ai " in f" {(enriched.get('description') or '').lower()} " else "QA")
    work_type = normalize_type(enriched.get("job_type"))
    enriched.update({
        "strengths": strengths,
        "weaknesses": weaknesses,
        "positioning": exp_fit,
        "recommendation": recommendation,
        "final_salary_advice": salary,
        "cover_letter": default_cover_letter(enriched, profile, matched),
        "employer_email": enriched.get("employer_email") or email,
        "employer_contact": enriched.get("employer_contact") or contact,
        "work_type": work_type,
        "fit_type": "target" if int(enriched.get("score") or 0) >= 75 else "backup",
        "industry_tag": industry,
        "perk_match": "; ".join(presentation["benefits"] + presentation["equipment"]),
        "presentation": presentation,
        "risk": presentation["risk"] or enriched.get("risk") or "",
        "experience_fit": exp_fit,
        "skill_matches": matched,
        "skill_gaps": gaps,
        "ai_analysis": f"### Навыки\n{strengths}\n\n### Опыт\n{exp_fit}\n\n### Зарплата\n{salary}\n\n### Рекомендация\n{recommendation}",
        "ai_review_status": "local",
    })
    return enriched


def score_job(job: dict[str, Any], profile: dict[str, Any], now: datetime | None = None) -> tuple[int, list[str], str]:
    match_kind = role_match_kind(job, profile)
    if match_kind == "none":
        return 0, [], "Role title does not match the candidate target"
    blocked = hard_block(job, profile)
    if blocked:
        return 0, [], blocked
    current = (now or utcnow()).astimezone(UTC)
    blob = " ".join(clean_text(job.get(key), 9000) for key in ("title", "description", "tags", "location")).lower()
    score = 32 if match_kind == "target" else 30
    reasons = ["совпадает целевая QA/тестовая роль"] if match_kind == "target" else ["смежная роль: подходит по навыкам QA/support"]
    if QA_TITLE_RE.search(clean_text(job.get("title"), 300)):
        score += 5
        reasons.append("название прямо указывает QA/тестирование")
    elif QA_ADJACENT_TITLE_RE.search(clean_text(job.get("title"), 300)):
        score += 6
        reasons.append("подходит для перехода через support/onboarding/customer success")
    if explicitly_remote_job(job) or candidate_local_job(job, profile) or any(word in blob for word in ("remote", "worldwide", "anywhere")):
        score += 18
        reasons.append("доступна удалённая работа или допустимый формат во Вьетнаме")
    if any(word in blob for word in ("worldwide", "anywhere", "global", "europe", "emea")):
        score += 4
        reasons.append("подходит международная география")
    if russian_language_international(job):
        score += 5
        reasons.append("приоритет: русскоязычная компания или рекрутер за рубежом")
    elif str(profile.get("english_level") or "").upper() in {"A1", "A2", "B1"}:
        score -= 4
        reasons.append("международная вакансия без русскоязычного onboarding — язык нужно проверить")

    profile_level = seniority(profile.get("target_title"))
    job_level = seniority(job.get("title"))
    level_points = {
        "junior": {"junior": 12, "unspecified": 6, "middle": -12, "senior": -30, "lead": -35},
        "middle": {"junior": -8, "unspecified": 7, "middle": 12, "senior": -6, "lead": -15},
    }.get(profile_level, {}).get(job_level, 4)
    score += level_points
    reasons.append(f"уровень: профиль {profile_level or 'не указан'} / вакансия {job_level}")

    have_years, needs_years = profile_years(profile), required_years(job)
    if not needs_years:
        score += 8
        reasons.append("нет жёсткого требования по стажу")
    elif have_years >= needs_years:
        score += 12
        reasons.append(f"стаж совпадает ({have_years}+ / {needs_years}+ лет)")
    elif needs_years - have_years == 1:
        score -= 8
        reasons.append(f"требуется на один год больше ({have_years}+ / {needs_years}+)")
    else:
        score -= 22
        reasons.append(f"пробел по стажу ({have_years}+ / {needs_years}+)")

    requirement_matches, requirement_gaps = requirement_fit(job, profile)
    requirement_total = len(requirement_matches) + len(requirement_gaps)
    critical_gaps: list[str] = []
    if requirement_total:
        score += round(24 * len(requirement_matches) / requirement_total)
        critical = {"test automation", "Playwright", "Selenium", "Cypress", "Python", "Java", "JavaScript/TypeScript", "Kubernetes"}
        critical_gaps = [gap for gap in requirement_gaps if gap in critical]
        score -= min(28, len(critical_gaps) * 7)
        if requirement_matches:
            reasons.append("совпали требования: " + ", ".join(requirement_matches[:5]))
        if critical_gaps:
            reasons.append("критические пробелы: " + ", ".join(critical_gaps[:4]))
    else:
        score += 8
        reasons.append("мало опубликованных требований к стеку")

    if normalize_type(job.get("job_type")) in {"full-time", "contract", "part-time", "freelance"}:
        score += 4
        reasons.append("формат: " + normalize_type(job.get("job_type")))
    posted = parse_datetime(job.get("posted_at"))
    if posted:
        days = max(0, (current - posted).days)
        if days <= 3:
            score += 6
            reasons.append("опубликована за последние 3 дня")
        elif days <= 7:
            score += 4
            reasons.append("опубликована за последние 7 дней")
        elif days <= 14:
            score += 2
            reasons.append("опубликована за последние 14 дней")
    profile_blob = clean_text(profile.get("resume_text"), 12000).lower()
    if any(domain in blob for domain in ("fintech", "banking", "payment")) and any(domain in profile_blob for domain in ("fintech", "bank", "payment")):
        score += 5
        reasons.append("совпадает опыт в fintech/banking")
    if job.get("salary"):
        score += 2
        reasons.append("зарплата указана")
    salary_cap = salary_ceiling_usd(job.get("salary"))
    target_salary = int(profile.get("salary_min") or 0)
    below_target = bool(salary_cap and target_salary and salary_cap < target_salary)
    if below_target:
        reasons.append(f"верхняя граница ${salary_cap} ниже цели профиля ${target_salary}")
    elevated_english = bool(HIGH_ENGLISH_RE.search(blob)) and str(profile.get("english_level") or "").upper() in {"A1", "A2", "B1"}
    if elevated_english:
        score -= 10
        reasons.append("английский может быть выше текущего уровня — проверить формат коммуникации")
    risk_parts = []
    if requirement_gaps:
        risk_parts.append("проверить пробелы: " + ", ".join(requirement_gaps[:5]))
    if needs_years > have_years:
        risk_parts.append(f"требуется {needs_years}+ лет, в профиле примерно {have_years}+")
    if below_target:
        risk_parts.append(f"верхняя граница зарплаты ${salary_cap} ниже минимума профиля ${target_salary}")
    if elevated_english:
        risk_parts.append("вакансия может требовать B2/fluent English; уточнить письменный формат, onboarding и язык команды")
    risk_parts.append("Проверить на оригинальной странице локацию, право удалённой работы, контракт и оплату")
    final_score = max(0, min(100, score))
    if "detail_unverified" in blob:
        final_score = min(final_score, GOLDEN_SCORE - 1)
    if critical_gaps:
        final_score = min(final_score, GOLDEN_SCORE - 1)
    if below_target:
        final_score = min(final_score, GOLDEN_SCORE - 1)
    if profile_level == "junior" and job_level in {"middle", "senior", "lead"}:
        final_score = min(final_score, GOLDEN_SCORE - 1)
    if profile_level == "middle" and job_level in {"senior", "lead"}:
        final_score = min(final_score, GOLDEN_SCORE - 1)
    if match_kind == "adjacent":
        final_score = min(final_score, 74)
        reasons.append("смежная сфера ограничена ниже прямых QA-вакансий")
    # A main match means eligibility first, then skills. Compensation and
    # perks are still surfaced as review notes, but missing perks alone must
    # not hide a legitimate 60%+ QA/support lead.
    benefits_blob = " ".join(clean_text(job.get(key), 9000) for key in ("title", "description", "tags")).lower()
    explicit_conditions = explicitly_remote_job(job) or candidate_local_job(job, profile) or any(term in benefits_blob for term in (
        "remote", "worldwide", "anywhere", "гибрид", "удал", "вьетнам", "vietnam",
        "relocation", "visa sponsorship", "релокац", "виза",
    ))
    if not explicit_conditions:
        final_score = min(final_score, GOLDEN_SCORE - 1)
        reasons.append("для главного совпадения нужен подтверждённый подходящий формат или локация")
    return final_score, reasons, "; ".join(risk_parts)


def filter_and_score(
    jobs: list[dict[str, Any]], profile: dict[str, Any], *, min_score: int = REVIEW_SCORE,
    max_age_days: int = 30, remote_only: bool = True,
    job_types: set[str] | None = None, now: datetime | None = None,
) -> list[dict[str, Any]]:
    current = (now or utcnow()).astimezone(UTC)
    allowed = {normalize_type(value) for value in (job_types or set())}
    ranked = []
    for original in jobs:
        job = dict(original)
        # Public boards do not consistently expose a boolean `remote` field:
        # Himalayas and several Telegram/partner feeds often put "Remote" only
        # in the location text.  Use the same evidence-based check as the hard
        # eligibility gate, otherwise valid worldwide roles disappear after
        # being correctly parsed and scored.
        if remote_only and not explicitly_remote_job(job) and not candidate_local_job(job, profile):
            continue
        posted = parse_datetime(job.get("posted_at"))
        verified_at = parse_datetime(job.get("verified_at"))
        if posted and posted > current + timedelta(days=1):
            continue
        if posted and current - posted > timedelta(days=max_age_days):
            continue
        if not posted and (
            not verified_at or current - verified_at > timedelta(hours=48)
        ):
            # A live-feed presence may prove activity when a board omits the
            # publication date, but only for a recently refreshed source.
            continue
        if allowed and normalize_type(job.get("job_type")) not in allowed:
            continue
        score, reasons, risk = score_job(job, profile, now=current)
        if score < int(min_score):
            continue
        job.update({"score": score, "local_score": score, "reasons": reasons, "risk": risk})
        ranked.append(enrich_job_for_profile(job, profile))
    return sorted(ranked, key=lambda item: (item["score"], parse_datetime(item.get("posted_at")) or datetime(1970, 1, 1, tzinfo=UTC)), reverse=True)


def extend_with_review_reserve(
    jobs: list[dict[str, Any]], profile: dict[str, Any], ranked: list[dict[str, Any]], *,
    target: int = VISIBLE_REVIEW_TARGET_PER_CANDIDATE, max_age_days: int = 30,
    remote_only: bool = True, job_types: set[str] | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Keep a broad manual-review pool without inflating golden matches.

    Recruiters and job boards omit details unevenly.  A card that clearly has a
    matching role, a fresh public source and a valid remote/Vietnam format
    should remain visible for manual review even when the published text lacks
    enough stack/benefit signals for a 60%+ recommendation.
    """
    if len(ranked) >= target:
        return ranked
    current = (now or utcnow()).astimezone(UTC)
    allowed = {normalize_type(value) for value in (job_types or set())}
    known = {clean_text(item.get("fingerprint") or job_fingerprint(item), 160) for item in ranked}
    reserve: list[dict[str, Any]] = []
    for original in jobs:
        job = dict(original)
        fingerprint = clean_text(job.get("fingerprint") or job_fingerprint(job), 160)
        if not fingerprint or fingerprint in known:
            continue
        if remote_only and not explicitly_remote_job(job) and not candidate_local_job(job, profile):
            continue
        posted = parse_datetime(job.get("posted_at"))
        verified_at = parse_datetime(job.get("verified_at"))
        if posted and posted > current + timedelta(days=1):
            continue
        if posted and current - posted > timedelta(days=max_age_days):
            continue
        if not posted and (not verified_at or current - verified_at > timedelta(hours=48)):
            continue
        if allowed and normalize_type(job.get("job_type")) not in allowed:
            continue
        score, reasons, risk = score_job(job, profile, now=current)
        if score <= 0:
            continue
        review_score = score
        if review_score < BROAD_REVIEW_SCORE:
            match_kind = role_match_kind(job, profile)
            review_score = 30 if match_kind == "target" else 26
            if explicitly_remote_job(job) or candidate_local_job(job, profile):
                review_score += 4
            if posted and current - posted <= timedelta(days=14):
                review_score += 3
            if job.get("salary"):
                review_score += 2
            review_score = min(GOLDEN_SCORE - 1, max(BROAD_REVIEW_SCORE, review_score))
            reasons = [
                *reasons[:5],
                "резерв ревью: свежая роль подходит по формату, но часть условий нужно проверить вручную",
            ]
            risk = risk or "Перед откликом обязательно проверить оригинал, договор, географию и оплату."
        job.update({
            "score": min(100, review_score),
            "local_score": score,
            "reasons": reasons,
            "risk": risk,
            "fingerprint": fingerprint,
        })
        reserve.append(enrich_job_for_profile(job, profile))
        known.add(fingerprint)
    reserve.sort(
        key=lambda item: (
            int(item.get("score") or 0),
            parse_datetime(item.get("posted_at")) or parse_datetime(item.get("verified_at")) or datetime(1970, 1, 1, tzinfo=UTC),
        ),
        reverse=True,
    )
    return [*ranked, *reserve[:max(0, target - len(ranked))]]


def explain_exclusions(
    jobs: list[dict[str, Any]], profile: dict[str, Any], *, remote_only: bool = True,
    max_age_days: int = 30, now: datetime | None = None, limit: int = 12,
) -> list[dict[str, str]]:
    current = (now or utcnow()).astimezone(UTC)
    rows = []
    for job in jobs:
        reason = ""
        if not role_matches(job, profile):
            continue
        if remote_only and not explicitly_remote_job(job) and not candidate_local_job(job, profile):
            reason = "Вакансия офисная или гибридная вне Вьетнама"
        posted = parse_datetime(job.get("posted_at"))
        if not reason and posted and current - posted > timedelta(days=max_age_days):
            reason = f"Вакансия старше {max_age_days} дней"
        if not reason:
            reason = hard_block(job, profile)
        if reason:
            rows.append({
                "candidate": clean_text(profile.get("name"), 120),
                "company": clean_text(job.get("company"), 160),
                "position": clean_text(job.get("title"), 220),
                "reason": reason,
            })
    return rows[:limit]


def _extract_json(text: str) -> Any:
    raw = str(text or "").strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.IGNORECASE)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start, end = raw.find("["), raw.rfind("]")
        if start >= 0 and end > start:
            return json.loads(raw[start:end + 1])
        raise


def open_model_configs() -> list[dict[str, str]]:
    """Return up to three configured model providers in failover order."""
    providers = [
        {
            "id": "vercel_gateway",
            "provider": "Vercel AI Gateway",
            "endpoint": "https://ai-gateway.vercel.sh/v1/chat/completions",
            "model": os.getenv("AI_GATEWAY_MODEL", "openai/gpt-oss-20b"),
            "key_name": "AI_GATEWAY_API_KEY",
            "protocol": "openai",
        },
        {
            "id": "openai",
            "provider": "OpenAI",
            "endpoint": "https://api.openai.com/v1/chat/completions",
            "model": os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
            "key_name": "OPENAI_API_KEY",
            "protocol": "openai",
        },
        {
            "id": "gemini",
            "provider": "Gemini",
            "endpoint": "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            "model": os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
            "key_name": "GEMINI_API_KEY",
            "protocol": "gemini",
        },
        {
            "id": "anthropic",
            "provider": "Claude",
            "endpoint": "https://api.anthropic.com/v1/messages",
            "model": os.getenv("ANTHROPIC_MODEL", "claude-3-5-haiku-latest"),
            "key_name": "ANTHROPIC_API_KEY",
            "protocol": "anthropic",
        },
        {
            "id": "groq",
            "provider": "Groq",
            "endpoint": "https://api.groq.com/openai/v1/chat/completions",
            "model": os.getenv("GROQ_MODEL", "openai/gpt-oss-20b"),
            "key_name": "GROQ_API_KEY",
            "protocol": "openai",
        },
        {
            "id": "openrouter",
            "provider": "OpenRouter",
            "endpoint": "https://openrouter.ai/api/v1/chat/completions",
            "model": os.getenv("OPENROUTER_MODEL", "openrouter/free"),
            "key_name": "OPENROUTER_API_KEY",
            "protocol": "openai",
        },
    ]
    preferred = os.getenv("OPEN_MODEL_PROVIDER", "auto").strip().lower()
    aliases = {
        "gateway": "vercel_gateway", "vercel": "vercel_gateway",
        "gpt": "openai", "google": "gemini", "claude": "anthropic",
    }
    preferred = aliases.get(preferred, preferred)
    if preferred not in {"", "auto", "ensemble", "consensus"}:
        providers = [item for item in providers if item["id"] == preferred]
    configured = []
    for item in providers:
        key = os.getenv(item["key_name"], "").strip()
        if item["id"] == "vercel_gateway":
            key = key or os.getenv("VERCEL_OIDC_TOKEN", "").strip()
        if item["id"] == "gemini":
            key = key or os.getenv("GOOGLE_API_KEY", "").strip()
        if key:
            configured.append(item)
    try:
        limit = int(os.getenv("AI_MAX_PROVIDERS", "3"))
    except ValueError:
        limit = 3
    return configured[:max(1, min(limit, 5))]


def open_model_config() -> tuple[str, str, str] | None:
    """Backward-compatible primary provider used by the legacy UI."""
    configs = open_model_configs()
    if not configs:
        return None
    selected = configs[0]
    return selected["provider"], selected["endpoint"], selected["model"]


def _ai_status(
    code: str, title: str, detail: str, *, provider: str = "", model: str = "",
    level: str = "info", retryable: bool = False,
) -> dict[str, Any]:
    return {
        "code": code,
        "title": title,
        "detail": detail,
        "provider": provider,
        "model": model,
        "level": level,
        "retryable": retryable,
        "local_ranking": True,
    }


def _open_model_http_status(response: requests.Response, provider: str, model: str) -> dict[str, Any]:
    try:
        payload = response.json()
    except (TypeError, ValueError):
        payload = {}
    error = payload.get("error", {}) if isinstance(payload, dict) else {}
    error_code = clean_text(error.get("code") or error.get("type"), 120).lower()
    error_message = clean_text(error.get("message"), 500).lower()
    combined = f"{error_code} {error_message}"

    if response.status_code == 429:
        quota_markers = (
            "insufficient_quota", "billing", "credit", "current quota",
            "monthly spend", "usage limit", "hard limit",
        )
        if any(marker in combined for marker in quota_markers):
            return _ai_status(
                "quota_exceeded",
                f"Нужно проверить лимит {provider}",
                "Ключ распознан, но у проекта закончились кредиты или достигнут месячный лимит. "
                "Другие AI-провайдеры будут проверены автоматически; локальный отбор уже сохранён.",
                provider=provider, model=model, level="warning",
            )
        return _ai_status(
            "rate_limited",
            f"{provider} временно занят",
            "Провайдер получил слишком много запросов. CareerMove переключается на следующую модель, "
            "а локальный рейтинг продолжает работать.",
            provider=provider, model=model, level="warning", retryable=True,
        )
    if response.status_code >= 500:
        return _ai_status(
            "provider_unavailable",
            f"{provider} временно недоступен",
            "CareerMove переключается на следующую модель; основной поиск и строгие фильтры уже завершены.",
            provider=provider, model=model, level="warning", retryable=True,
        )
    return _ai_status(
        "request_rejected",
        f"{provider} отклонил запрос",
        "CareerMove проверит резервные модели. Основной локальный рейтинг сохранён.",
        provider=provider, model=model, level="warning",
    )


def ai_pause_from_statuses(
    statuses: list[dict[str, Any]], now: datetime | None = None,
) -> dict[str, Any] | None:
    current = (now or utcnow()).astimezone(UTC)
    selected = next((item for item in statuses if item.get("code") == "quota_exceeded"), None)
    selected = selected or next((item for item in statuses if item.get("code") == "rate_limited"), None)
    if not selected:
        return None
    until = ""
    if selected.get("code") == "rate_limited":
        until = iso(current + timedelta(minutes=AI_RATE_LIMIT_COOLDOWN_MINUTES))
    return {
        "code": selected.get("code"),
        "provider": clean_text(selected.get("provider"), 80),
        "model": clean_text(selected.get("model"), 120),
        "created_at": iso(current),
        "until": until,
    }


def active_ai_pause(value: Any, now: datetime | None = None) -> dict[str, Any] | None:
    if not value:
        return None
    try:
        pause = json.loads(value) if isinstance(value, str) else dict(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if pause.get("code") not in {"quota_exceeded", "rate_limited"}:
        return None
    if pause.get("code") == "rate_limited":
        until = parse_datetime(pause.get("until"))
        current = (now or utcnow()).astimezone(UTC)
        if not until or current >= until:
            return None
    return pause


def _retry_delay(response: requests.Response, attempt: int) -> float:
    raw = str(response.headers.get("Retry-After", "") or "").strip()
    try:
        base = float(raw)
    except ValueError:
        base = float(2 ** attempt)
    return min(6.0, max(1.0, base)) + random.uniform(0.15, 0.65)


def _model_api_key(config: dict[str, str]) -> str:
    key = os.getenv(config["key_name"], "").strip()
    if config["id"] == "vercel_gateway":
        key = key or os.getenv("VERCEL_OIDC_TOKEN", "").strip()
    if config["id"] == "gemini":
        key = key or os.getenv("GOOGLE_API_KEY", "").strip()
    return key


def _model_request(
    config: dict[str, str], prompt: str, client: requests.Session,
) -> tuple[list[dict[str, Any]] | None, dict[str, Any]]:
    provider, model = config["provider"], config["model"]
    key = _model_api_key(config)
    system = "You are a conservative job relevance ranker. Output valid JSON only. Explain results in Russian."
    headers = {"Content-Type": "application/json", "User-Agent": USER_AGENT}
    endpoint = config["endpoint"].format(model=model)
    if config["protocol"] == "openai":
        headers["Authorization"] = f"Bearer {key}"
        if provider == "OpenRouter":
            headers["HTTP-Referer"] = os.getenv("APP_URL", "http://localhost:5173")
            headers["X-Title"] = "CareerMove AI"
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.1,
        }
    elif config["protocol"] == "gemini":
        headers["x-goog-api-key"] = key
        payload = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.1, "responseMimeType": "application/json"},
        }
    else:
        headers["x-api-key"] = key
        headers["anthropic-version"] = "2023-06-01"
        payload = {
            "model": model,
            "max_tokens": 4096,
            "temperature": 0.1,
            "system": system,
            "messages": [{"role": "user", "content": prompt}],
        }
    try:
        response = None
        for attempt in range(2):
            response = client.post(endpoint, headers=headers, json=payload, timeout=45)
            if response.status_code == 429:
                status = _open_model_http_status(response, provider, model)
                if status["code"] == "rate_limited" and attempt == 0:
                    time.sleep(_retry_delay(response, attempt))
                    continue
                return None, status
            if response.status_code >= 400:
                return None, _open_model_http_status(response, provider, model)
            break
        if response is None:
            raise RuntimeError("Model response is missing")
        body = response.json()
        if config["protocol"] == "openai":
            content = body["choices"][0]["message"]["content"]
        elif config["protocol"] == "gemini":
            content = "".join(
                str(part.get("text") or "")
                for part in body["candidates"][0]["content"]["parts"]
                if isinstance(part, dict)
            )
        else:
            content = "".join(
                str(block.get("text") or "")
                for block in body["content"]
                if isinstance(block, dict) and block.get("type") == "text"
            )
        rows = _extract_json(content)
        if not isinstance(rows, list):
            raise TypeError("Model output must be a JSON array")
        return [row for row in rows if isinstance(row, dict)], _ai_status(
            "completed", f"{provider} завершил проверку",
            f"{provider} · {model} вернул структурированный анализ.",
            provider=provider, model=model, level="success",
        )
    except (requests.RequestException, KeyError, TypeError, ValueError, RuntimeError):
        return None, _ai_status(
            "unavailable", f"{provider} временно недоступен",
            "Ответ модели не прошёл проверку; CareerMove переключается на следующего провайдера.",
            provider=provider, model=model, level="warning", retryable=True,
        )


def rerank_with_open_model(
    jobs: list[dict[str, Any]], profile: dict[str, Any], session: requests.Session | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    configs = open_model_configs()
    if not jobs:
        return jobs, _ai_status(
            "not_needed", "AI-проверка не понадобилась",
            "После строгих фильтров нет вакансий для дополнительного ранжирования.",
        )
    if not configs:
        return jobs, _ai_status(
            "local_only", "Основной анализ готов",
            "API-ключи моделей не настроены. CareerMove использовал прозрачный локальный рейтинг — поиск остаётся рабочим.",
        )
    compact_jobs = [
        {
            "id": f"{job['source']}:{job['external_id']}", "title": job["title"],
            "company": job["company"], "location": job["location"],
            "type": job["job_type"], "local_score": job["local_score"],
            "summary": clean_text(job.get("description"), 700),
        }
        for job in jobs[:25]
    ]
    safe_profile = {
        "target_role": clean_text(profile.get("target_title"), 400),
        "english_level": clean_text(profile.get("english_level"), 30),
        "skills": (profile.get("skills") or [])[:30],
        "requirements": clean_text(profile.get("hard_require"), 600),
        "exclusions": clean_text(profile.get("hard_exclude"), 600),
    }
    prompt = (
        "Analyze only the supplied real vacancies for this candidate. Never create jobs, URLs, companies, contacts, or IDs. "
        "Return a JSON array only. Each item must contain: id, score (0-100 integer), reason (max 18 words), "
        "skill_matches (array), skill_gaps (array), experience_fit (max 35 words), salary_advice (max 45 words), "
        "recommendation (max 30 words). If compensation is absent, label the range as an estimate. "
        "Write every explanatory field in Russian; keep technology names unchanged. "
        "Be strict about role relevance, remote eligibility, English level, skills and experience.\nPROFILE:\n"
        + json.dumps(safe_profile, ensure_ascii=False)
        + "\nVACANCIES:\n" + json.dumps(compact_jobs, ensure_ascii=False)
    )
    client = session or requests.Session()
    successful: list[tuple[dict[str, str], dict[str, dict[str, Any]]]] = []
    attempts = []
    for config in configs:
        rows, status = _model_request(config, prompt, client)
        attempts.append(status)
        if rows is not None:
            successful.append((
                config,
                {str(row.get("id")): row for row in rows if row.get("id")},
            ))
    if not successful:
        if len(attempts) == 1:
            return jobs, attempts[0]
        status = _ai_status(
            "all_providers_unavailable", "AI-провайдеры временно недоступны",
            "Все настроенные модели пропущены, но локальный рейтинг и строгие фильтры полностью сохранены.",
            provider=", ".join(item["provider"] for item in configs), level="warning", retryable=True,
        )
        status["attempts"] = attempts
        return jobs, status

    provider_names = [config["provider"] for config, _ in successful]
    for job in jobs[:25]:
        item_id = f"{job['source']}:{job['external_id']}"
        reviews = [
            (config, rows[item_id])
            for config, rows in successful
            if item_id in rows
        ]
        if not reviews:
            continue
        scores = []
        matches, gaps = [], []
        for config, row in reviews:
            try:
                scores.append(max(0, min(100, int(row.get("score", job["local_score"])))))
            except (TypeError, ValueError):
                pass
            reason = clean_text(row.get("reason"), 240)
            if reason:
                job["reasons"] = job["reasons"] + [f"AI {config['provider']}: {reason}"]
            matches.extend(
                clean_text(value, 100) for value in (row.get("skill_matches") or [])
                if clean_text(value, 100)
            )
            gaps.extend(
                clean_text(value, 100) for value in (row.get("skill_gaps") or [])
                if clean_text(value, 100)
            )
        lead = reviews[0][1]
        ai_score = round(sum(scores) / len(scores)) if scores else int(job["local_score"])
        job["ai_score"] = ai_score
        blended_score = round(job["local_score"] * 0.7 + ai_score * 0.3)
        # Models may explain and reorder eligible matches, but cannot overrule
        # deterministic experience, salary or critical-gap gates.
        job["score"] = min(blended_score, GOLDEN_SCORE - 1) if job["local_score"] < GOLDEN_SCORE else blended_score
        matches = list(dict.fromkeys(matches))
        gaps = list(dict.fromkeys(gaps))
        exp_fit = clean_text(lead.get("experience_fit"), 500) or job.get("positioning", "")
        salary = clean_text(lead.get("salary_advice"), 600) or job.get("final_salary_advice", "")
        recommendation = clean_text(lead.get("recommendation"), 500) or job.get("recommendation", "")
        providers_label = ", ".join(config["provider"] for config, _ in reviews)
        if matches:
            job["strengths"] = f"AI ({providers_label}) подтвердил навыки: " + ", ".join(matches[:8])
        if gaps:
            job["weaknesses"] = f"AI ({providers_label}) отметил пробелы: " + ", ".join(gaps[:6])
        job["positioning"] = exp_fit
        job["experience_fit"] = exp_fit
        job["final_salary_advice"] = salary
        job["recommendation"] = recommendation
        job["ai_analysis"] = (
            f"### AI-проверка\nМодели: {providers_label}\n\n"
            f"### Навыки\n{job.get('strengths','')}\n\n### Пробелы\n{job.get('weaknesses','')}\n\n"
            f"### Опыт\n{exp_fit}\n\n### Зарплата\n{salary}\n\n### Рекомендация\n{recommendation}"
        )
        job["ai_review_status"] = "done"
    jobs.sort(key=lambda item: item["score"], reverse=True)
    failed_count = len(attempts) - len(successful)
    status = _ai_status(
        "partial" if failed_count else "completed",
        "AI-проверка завершена с резервом" if failed_count else "AI-проверка несколькими моделями завершена",
        (
            f"Результат сформирован: {', '.join(provider_names)}. "
            + (f"Резервных провайдеров пропущено: {failed_count}." if failed_count else "Ответы моделей сведены в общий рейтинг.")
        ),
        provider=", ".join(provider_names),
        model=", ".join(config["model"] for config, _ in successful),
        level="success",
    )
    status["attempts"] = attempts
    return jobs, status


def _payload_hash(job: dict[str, Any]) -> str:
    fields = [job.get(key) for key in ("title", "company", "description", "location", "job_type", "posted_at", "salary", "url")]
    return stable_hash(*fields)


def salary_floor(value: Any) -> int:
    text = clean_text(value, 300)
    amounts: list[int] = []
    for match in re.finditer(r"(?<![\w.])[$€£]?\s*(\d+(?:[.,]\d+)?)\s*([kK]?)(?!\w)", text):
        try:
            amount = float(match.group(1).replace(",", "."))
        except ValueError:
            continue
        if match.group(2):
            amount *= 1000
        rounded = int(amount)
        if 5 <= rounded <= 500000:
            amounts.append(rounded)
    return min(amounts) if amounts else 0


def company_rating(job: dict[str, Any]) -> dict[str, int | str]:
    blob = " ".join(clean_text(job.get(key), 7000) for key in ("description", "location", "tags")).lower()
    kind = normalize_type(job.get("job_type"))
    stability = {"full-time": 82, "contract": 66, "part-time": 52, "freelance": 44, "temporary": 35}.get(kind, 55)
    if any(term in blob for term in ("paid leave", "health insurance", "medical insurance", "equipment", "learning budget")):
        stability = min(100, stability + 8)
    if any(term in blob for term in ("worldwide", "anywhere", "global remote")):
        remote_friendly = 94
    elif any(term in blob for term in ("europe", "emea", "cis")):
        remote_friendly = 72
    elif job.get("remote"):
        remote_friendly = 58
    else:
        remote_friendly = 10
    b1_friendly = 35 if re.search(r"\b(?:c1|c2)\b.{0,25}english|native english", blob) else 60 if re.search(r"\bb2\b.{0,25}english|english.{0,25}\bb2\b", blob) else 78
    source = clean_text(job.get("source"), 90)
    url_host = urlsplit(str(job.get("url") or "")).netloc.lower()
    official_score = 82 if source in SOURCE_SPECS else 68
    if any(host in url_host for host in ("linkedin.com", "t.me", "telegram.me")):
        official_score -= 8
    if not job.get("url"):
        official_score = 25
    rating = round((stability + remote_friendly + b1_friendly + official_score) / 4)
    return {
        "rating": rating, "stability": stability, "remote_friendly": remote_friendly,
        "b1_friendly": b1_friendly, "official_score": official_score,
        "notes": f"auto · {source} · verify employer reviews and legal entity before applying",
    }


def save_company_rating(
    query: Callable[..., Any], execute: Callable[..., Any], user_id: int, job: dict[str, Any],
) -> dict[str, int | str]:
    company = clean_text(job.get("company"), 180) or "Unknown company"
    values = company_rating(job)
    existing = query(
        "SELECT id,notes FROM company_ratings WHERE user_id=? AND lower(company)=lower(?) ORDER BY id DESC LIMIT 1",
        (user_id, company),
    )
    if not existing.empty and str(existing.iloc[0].get("notes") or "").lower().startswith("manual"):
        manual = query(
            "SELECT * FROM company_ratings WHERE id=? AND user_id=?",
            (int(existing.iloc[0]["id"]), user_id),
        ).iloc[0].to_dict()
        return {key: manual.get(key, values[key]) for key in values}
    params = (
        clean_text(job.get("location"), 180), clean_text(job.get("industry_tag"), 80),
        int(values["rating"]), int(values["stability"]), int(values["remote_friendly"]),
        int(values["b1_friendly"]), int(values["official_score"]), str(values["notes"]),
    )
    if existing.empty:
        execute("""
            INSERT INTO company_ratings(
              user_id,company,country,industry_tag,rating,stability,remote_friendly,
              b1_friendly,official_score,notes
            ) VALUES(?,?,?,?,?,?,?,?,?,?)
        """, (user_id, company, *params))
    else:
        execute("""
            UPDATE company_ratings SET country=?,industry_tag=?,rating=?,stability=?,remote_friendly=?,
              b1_friendly=?,official_score=?,notes=?,updated_at=CURRENT_TIMESTAMP WHERE id=? AND user_id=?
        """, (*params, int(existing.iloc[0]["id"]), user_id))
    return values


def save_job(
    query: Callable[..., Any], execute: Callable[..., Any], user_id: int,
    candidate_id: int, job: dict[str, Any], now: datetime | None = None,
) -> dict[str, Any]:
    current = (now or utcnow()).astimezone(UTC)
    source, external = clean_text(job.get("source"), 90), clean_text(job.get("external_id"), 240)
    fingerprint, payload_hash = job.get("fingerprint") or job_fingerprint(job), _payload_hash(job)
    previous = query(
        "SELECT * FROM live_job_index WHERE user_id=? AND candidate_id=? AND source=? AND external_id=?",
        (user_id, candidate_id, source, external),
    )
    related = query(
        "SELECT * FROM live_job_index WHERE user_id=? AND candidate_id=? AND fingerprint=? ORDER BY last_seen DESC LIMIT 1",
        (user_id, candidate_id, fingerprint),
    ) if previous.empty else previous
    prior = previous.iloc[0].to_dict() if not previous.empty else None
    vacancy_id = int(related.iloc[0]["vacancy_id"]) if not related.empty and related.iloc[0].get("vacancy_id") else None
    if vacancy_id is None and job.get("url"):
        same_link = query(
            "SELECT id FROM vacancies WHERE user_id=? AND candidate_id=? AND link=? ORDER BY id DESC LIMIT 1",
            (user_id, candidate_id, job["url"]),
        )
        vacancy_id = int(same_link.iloc[0]["id"]) if not same_link.empty else None
    reason_text = clean_text(job.get("strengths"), 2000) or "; ".join(job.get("reasons") or [])[:2000]
    weaknesses = clean_text(job.get("weaknesses"), 2000)
    positioning = clean_text(job.get("positioning") or job.get("experience_fit"), 2000)
    recommendation = clean_text(job.get("recommendation"), 2000)
    salary_advice = clean_text(job.get("final_salary_advice"), 2000)
    ai_analysis = clean_multiline(job.get("ai_analysis"), 5000)
    if not ai_analysis:
        ai_analysis = (
            f"### Skills\n{reason_text}\n\n### Skill gaps\n{weaknesses}\n\n"
            f"### Experience\n{positioning}\n\n### Compensation\n{salary_advice}\n\n"
            f"### Recommendation\n{recommendation}"
        )
    cover = clean_multiline(job.get("cover_letter"), 5000)
    salary_min = salary_floor(job.get("salary"))
    rating = save_company_rating(query, execute, user_id, job)
    snapshot = json.dumps({
        "external_id": external, "description": clean_text(job.get("description"), 3500),
        "tags": job.get("tags", ""), "first_party_url": job.get("url", ""),
        "links": [
            {
                "url": canonical_url(item.get("url")),
                "source": clean_text(item.get("source"), 90),
                "posted_at": clean_text(item.get("posted_at"), 80),
            }
            for item in (job.get("links") or [])
            if canonical_url(item.get("url"))
        ][:8],
        "verified_at": clean_text(job.get("verified_at"), 80),
    }, ensure_ascii=False)[:5000]
    if vacancy_id:
        execute("""
            UPDATE vacancies SET fetched_at=?,posted_at=?,source=?,service=?,company=?,company_country=?,company_rating=?,
              position=?,industry_tag=?,link=?,language=?,remote_location=?,worker_country=?,salary_text=?,salary_min=?,
              score=?,category=?,strengths=?,weaknesses=?,positioning=?,recommendation=?,risk=?,
              employer_email=?,employer_contact=?,perk_match=?,fit_type=?,work_type=?,final_salary_advice=?,
              ai_analysis=?,ai_review_status=?,
              status=CASE WHEN COALESCE(status,'found')='archived' THEN 'found' ELSE status END,
              cover_letter=CASE WHEN COALESCE(cover_letter,'')='' THEN ? ELSE cover_letter END,
              source_snapshot=?
            WHERE id=? AND user_id=?
        """, (
            iso(current), job.get("posted_at", ""), source, SOURCE_SPECS.get(source, {}).get("attribution", source),
            job.get("company", ""), job.get("location", ""), int(rating["rating"]), job.get("title", ""),
            job.get("industry_tag", "QA"), job.get("url", ""), "English", job.get("location", ""),
            "Remote/International", job.get("salary", ""), salary_min, int(job.get("score", 0)),
            "very suitable" if int(job.get("score", 0)) >= 85 else "suitable",
            reason_text, weaknesses, positioning, recommendation, job.get("risk", ""),
            job.get("employer_email", ""), job.get("employer_contact", ""), job.get("perk_match", ""),
            job.get("fit_type", "target" if int(job.get("score", 0)) >= 75 else "backup"),
            job.get("work_type", normalize_type(job.get("job_type"))), salary_advice,
            ai_analysis, job.get("ai_review_status", "local"), cover, snapshot, vacancy_id, user_id,
        ))
    else:
        execute("""
            INSERT INTO vacancies(
              user_id,candidate_id,fetched_at,posted_at,source,service,company,company_country,
              position,industry_tag,company_rating,link,language,remote_location,worker_country,
              salary_text,salary_min,score,category,status,strengths,weaknesses,positioning,
              recommendation,risk,cover_letter,employer_email,employer_contact,source_snapshot,
              perk_match,fit_type,work_type,final_salary_advice,ai_analysis,ai_review_status
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            user_id, candidate_id, iso(current), job.get("posted_at", ""), source,
            SOURCE_SPECS.get(source, {}).get("attribution", source), job.get("company", ""),
            job.get("location", ""), job.get("title", ""), job.get("industry_tag", "QA"), int(rating["rating"]),
            job.get("url", ""), "English", job.get("location", ""), "Remote/International",
            job.get("salary", ""), salary_min,
            int(job.get("score", 0)), "very suitable" if int(job.get("score", 0)) >= 85 else "suitable",
            "found", reason_text, weaknesses, positioning, recommendation, job.get("risk", ""),
            cover, job.get("employer_email", ""), job.get("employer_contact", ""), snapshot,
            job.get("perk_match", ""),
            job.get("fit_type", "target" if int(job.get("score", 0)) >= 75 else "backup"),
            job.get("work_type", normalize_type(job.get("job_type"))), salary_advice,
            ai_analysis, job.get("ai_review_status", "local"),
        ))
        created = query(
            "SELECT id FROM vacancies WHERE user_id=? AND candidate_id=? AND link=? ORDER BY id DESC LIMIT 1",
            (user_id, candidate_id, job.get("url", "")),
        )
        vacancy_id = int(created.iloc[0]["id"]) if not created.empty else None
    change = "new" if prior is None else ("updated" if prior.get("payload_hash") != payload_hash else "seen")
    first_seen = prior.get("first_seen") if prior else iso(current)
    execute("""
        INSERT INTO live_job_index(
          user_id,candidate_id,source,external_id,vacancy_id,fingerprint,payload_hash,
          first_seen,last_seen,source_posted,change_kind,active
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(user_id,candidate_id,source,external_id) DO UPDATE SET
          vacancy_id=excluded.vacancy_id,fingerprint=excluded.fingerprint,payload_hash=excluded.payload_hash,
          last_seen=excluded.last_seen,source_posted=excluded.source_posted,
          change_kind=excluded.change_kind,active=1
    """, (
        user_id, candidate_id, source, external, vacancy_id, fingerprint, payload_hash,
        first_seen, iso(current), job.get("posted_at", ""), change, 1,
    ))
    saved = dict(job)
    saved.update({"vacancy_id": vacancy_id, "change_kind": change, "first_seen": first_seen, "last_seen": iso(current)})
    return saved


def sync_live_job_actuality(
    query: Callable[..., Any], execute: Callable[..., Any], user_id: int,
    candidate_ids: list[int], jobs: list[dict[str, Any]], diagnostics: list[dict[str, Any]],
    *, max_age_days: int = 30, now: datetime | None = None,
) -> int:
    """Deactivate closed or too-old cards after a source was really checked."""
    if not candidate_ids:
        return 0
    current = (now or utcnow()).astimezone(UTC)
    checked_sources = {
        clean_text(item.get("source"), 90)
        for item in diagnostics
        if str(item.get("status") or "") == "updated" and clean_text(item.get("source"), 90)
    }
    seen_external: dict[str, set[str]] = {}
    seen_fingerprints: dict[str, set[str]] = {}
    for job in jobs:
        source = clean_text(job.get("source"), 90)
        if not source:
            continue
        seen_external.setdefault(source, set()).add(clean_text(job.get("external_id"), 240))
        seen_fingerprints.setdefault(source, set()).add(clean_text(job.get("fingerprint") or job_fingerprint(job), 160))
    placeholders = ",".join("?" for _ in candidate_ids)
    frame = query(f"""
        SELECT i.candidate_id,i.source,i.external_id,i.fingerprint,i.vacancy_id,
          i.source_posted,i.last_seen,COALESCE(v.status,'found') status,v.posted_at
        FROM live_job_index i
        LEFT JOIN vacancies v ON v.id=i.vacancy_id
        WHERE i.user_id=? AND i.candidate_id IN ({placeholders}) AND i.active=1
    """, tuple([user_id, *candidate_ids]))
    if frame.empty:
        return 0
    protected = {"sent", "approved", "applied", "archived"}
    index_marks: set[tuple[int, str, str]] = set()
    affected_vacancies: set[int] = set()
    cutoff = timedelta(days=max_age_days)
    for row in frame.to_dict("records"):
        if str(row.get("status") or "found") in protected:
            continue
        candidate_id = int(row.get("candidate_id") or 0)
        source = clean_text(row.get("source"), 90)
        external_id = clean_text(row.get("external_id"), 240)
        fingerprint = clean_text(row.get("fingerprint"), 160)
        posted = parse_datetime(row.get("posted_at")) or parse_datetime(row.get("source_posted"))
        last_seen = parse_datetime(row.get("last_seen"))
        too_old = bool(
            (posted and current - posted > cutoff)
            or (not posted and last_seen and current - last_seen > cutoff)
        )
        absent_from_checked_source = (
            source in checked_sources
            and external_id not in seen_external.get(source, set())
            and fingerprint not in seen_fingerprints.get(source, set())
        )
        if not too_old and not absent_from_checked_source:
            continue
        index_marks.add((candidate_id, source, external_id))
        try:
            vacancy_id = int(row.get("vacancy_id") or 0)
        except (TypeError, ValueError):
            vacancy_id = 0
        if vacancy_id:
            affected_vacancies.add(vacancy_id)
    for candidate_id, source, external_id in index_marks:
        execute(
            "UPDATE live_job_index SET active=0 WHERE user_id=? AND candidate_id=? AND source=? AND external_id=?",
            (user_id, candidate_id, source, external_id),
        )
    archived = 0
    for vacancy_id in affected_vacancies:
        active = query(
            "SELECT 1 FROM live_job_index WHERE user_id=? AND vacancy_id=? AND active=1 LIMIT 1",
            (user_id, vacancy_id),
        )
        if not active.empty:
            continue
        before = query(
            "SELECT id FROM vacancies WHERE user_id=? AND id=? AND COALESCE(status,'found') NOT IN ('sent','approved','applied','archived')",
            (user_id, vacancy_id),
        )
        if before.empty:
            continue
        execute(
            "UPDATE vacancies SET status='archived' WHERE user_id=? AND id=?",
            (user_id, vacancy_id),
        )
        archived += 1
    return archived


def archive_legacy_page_fragments(query: Callable[..., Any], execute: Callable[..., Any], user_id: int) -> int:
    legacy = [
        "https://remoteok.com/remote-qa-jobs",
        "https://remotive.com/remote-jobs/qa",
        "https://weworkremotely.com/remote-jobs/search?term=qa",
    ]
    count = 0
    for link in legacy:
        frame = query("SELECT COUNT(*) AS count FROM vacancies WHERE user_id=? AND status='found' AND link=?", (user_id, link))
        count += int(frame.iloc[0]["count"]) if not frame.empty else 0
        execute(
            "UPDATE vacancies SET status='skip',blocked_reason=? WHERE user_id=? AND status='found' AND link=?",
            ("Archived legacy page fragment: not a unique vacancy URL", user_id, link),
        )
    return count


def run_search(
    query: Callable[..., Any], execute: Callable[..., Any], user_id: int, candidate_ids: list[int],
    *, min_score: int = REVIEW_SCORE, max_age_days: int = 30, remote_only: bool = True,
    job_types: set[str] | None = None, limit_per_candidate: int = 50,
    force_refresh: bool = False, use_open_model: bool = True,
    session: requests.Session | None = None,
    progress: Callable[[str, str], None] | None = None,
    source_names: tuple[str, ...] | list[str] | None = None,
    max_source_wait_seconds: float | None = None,
) -> dict[str, Any]:
    def emit(stage: str, detail: str) -> None:
        if progress:
            progress(stage, detail)

    ensure_schema(execute)
    # Load candidate context before the network pass. This lets the first
    # completed source create useful cards immediately rather than waiting for
    # every provider (or an optional AI pass) to finish.
    profiles: list[tuple[int, str, dict[str, Any]]] = []
    for candidate_id in candidate_ids:
        profile = candidate_profile(query, user_id, candidate_id)
        candidate_name = clean_text(profile.get("name"), 120) or f"Кандидат {candidate_id}"
        profiles.append((candidate_id, candidate_name, profile))

    active_sources = tuple(source_names or tuple(SOURCE_SPECS))
    active_sources = tuple(name for name in active_sources if name in SOURCE_SPECS)
    if not active_sources:
        raise ValueError("Не выбраны доступные источники вакансий.")
    saved_by_key: dict[tuple[int, str], dict[str, Any]] = {}
    streamed_fingerprints: dict[int, set[str]] = {candidate_id: set() for candidate_id, _, _ in profiles}
    streamed_count: dict[int, int] = {candidate_id: 0 for candidate_id, _, _ in profiles}
    source_count = 0
    batch_count = 0

    def record_saved(candidate_id: int, candidate_name: str, item: dict[str, Any]) -> None:
        item["candidate_id"] = candidate_id
        item["candidate"] = candidate_name
        fingerprint = clean_text(item.get("fingerprint") or job_fingerprint(item), 160)
        saved_by_key[(candidate_id, fingerprint)] = item

    def stream_source(_name: str, source_jobs: list[dict[str, Any]], _diagnostic: dict[str, Any]) -> None:
        """Persist the next visible batch as soon as a feed is ready.

        The first pass is deterministic/local by design. The final pass below
        still merges duplicate links and optionally asks the configured AI
        providers, but neither operation removes the already visible cards.
        """
        nonlocal source_count, batch_count
        source_count += 1
        candidates: list[tuple[int, str, dict[str, Any]]] = []
        queued_fingerprints: set[tuple[int, str]] = set()
        for candidate_id, candidate_name, profile in profiles:
            if streamed_count[candidate_id] >= limit_per_candidate:
                continue
            ranked_now = filter_and_score(
                source_jobs, profile, min_score=min_score, max_age_days=max_age_days,
                remote_only=remote_only, job_types=job_types,
            )
            for job in ranked_now:
                fingerprint = clean_text(job.get("fingerprint") or job_fingerprint(job), 160)
                key = (candidate_id, fingerprint)
                if fingerprint and fingerprint not in streamed_fingerprints[candidate_id] and key not in queued_fingerprints:
                    candidates.append((candidate_id, candidate_name, job))
                    queued_fingerprints.add(key)
        candidates.sort(key=lambda row: int(row[2].get("score") or 0), reverse=True)
        batch = candidates[:20]
        for candidate_id, candidate_name, job in batch:
            fingerprint = clean_text(job.get("fingerprint") or job_fingerprint(job), 160)
            streamed_fingerprints[candidate_id].add(fingerprint)
            streamed_count[candidate_id] += 1
            record_saved(candidate_id, candidate_name, save_job(query, execute, user_id, candidate_id, job))
        if batch:
            batch_count += 1
            emit(
                "streaming",
                f"Пачка {batch_count}: добавлено {len(batch)} вакансий с локальным рейтингом. "
                f"Готово источников: {source_count}/{len(active_sources)}.",
            )
        else:
            emit("streaming", f"Источник {source_count}/{len(active_sources)} проверен; подходящих карточек пока нет.")

    emit(
        "sources",
        f"Подключаю {len(active_sources)} источников для этого запуска…",
    )
    # A bounded serverless batch is persisted once after collection. Streaming
    # every provider used to run the same matching and upsert path twice and
    # could exhaust the request before its final status was saved.
    source_callback = stream_source if max_source_wait_seconds is None else None
    jobs, diagnostics = collect_live_jobs(
        query, execute, force=force_refresh, session=session, on_source=source_callback,
        source_names=active_sources, max_wait_seconds=max_source_wait_seconds,
    )
    emit("received", f"Получено {len(jobs)} уникальных вакансий. Применяю eligibility-фильтры и мягкий review-порог.")
    ai_statuses = []
    excluded = []
    candidate_stats = []
    model_stop_status = None
    for candidate_id, candidate_name, profile in profiles:
        emit("matching", f"Сопоставляю требования с опытом: {candidate_name}…")
        excluded.extend(explain_exclusions(
            jobs, profile, remote_only=remote_only, max_age_days=max_age_days, limit=8,
        ))
        ranked = filter_and_score(
            jobs, profile, min_score=min_score, max_age_days=max_age_days,
            remote_only=remote_only, job_types=job_types,
        )
        if min_score <= BROAD_REVIEW_SCORE:
            ranked = extend_with_review_reserve(
                jobs, profile, ranked,
                target=min(limit_per_candidate, VISIBLE_REVIEW_TARGET_PER_CANDIDATE),
                max_age_days=max_age_days,
                remote_only=remote_only,
                job_types=job_types,
            )
        if use_open_model and model_stop_status:
            emit("model", "AI-провайдеры уже проверены; продолжаю локальный рейтинг без повторных запросов.")
            ai_status = model_stop_status
        elif use_open_model:
            emit("model", f"Сверяю короткий список через доступные AI-модели: {candidate_name}…")
            ranked, ai_status = rerank_with_open_model(ranked, profile, session=session)
            if ai_status.get("code") in {
                "quota_exceeded", "rate_limited", "provider_unavailable",
                "request_rejected", "unavailable", "all_providers_unavailable",
            }:
                model_stop_status = ai_status
        else:
            ai_status = _ai_status(
                "disabled", "AI-проверка выключена",
                "Использован основной локальный рейтинг и строгие фильтры.",
            )
        ai_statuses.append(ai_status)
        candidate_stats.append({
            "candidate": candidate_name,
            "review": len(ranked),
            "golden": sum(1 for job in ranked if int(job.get("score") or 0) >= GOLDEN_SCORE),
        })
        emit("saving", f"Сохраняю подходящие карточки: {candidate_name}…")
        for job in ranked[:limit_per_candidate]:
            record_saved(candidate_id, candidate_name, save_job(query, execute, user_id, candidate_id, job))
    archived_count = sync_live_job_actuality(
        query,
        execute,
        user_id,
        candidate_ids,
        jobs,
        diagnostics,
        max_age_days=max_age_days,
    )
    unique_statuses = []
    seen_statuses = set()
    for status in ai_statuses:
        key = (status.get("code"), status.get("provider"), status.get("model"))
        if key not in seen_statuses:
            seen_statuses.add(key)
            unique_statuses.append(status)
    emit("done", f"Готово. Результаты сохранены, устаревшие карточки обновлены: {archived_count}.")
    saved = list(saved_by_key.values())
    new_count = sum(1 for item in saved if str(item.get("change_kind") or "") == "new")
    updated_count = sum(1 for item in saved if str(item.get("change_kind") or "") == "updated")
    rechecked_count = sum(1 for item in saved if str(item.get("change_kind") or "") == "seen")
    return {
        "raw_count": len(jobs), "saved": saved, "diagnostics": diagnostics,
        "ai_statuses": unique_statuses, "excluded": excluded,
        "candidate_stats": candidate_stats,
        "golden_count": sum(1 for item in saved if int(item.get("score") or 0) >= GOLDEN_SCORE),
        "new_count": new_count,
        "updated_count": updated_count,
        "rechecked_count": rechecked_count,
        "archived_count": archived_count,
    }


def latest_jobs(query: Callable[..., Any], user_id: int, candidate_ids: list[int], limit: int = 120) -> list[dict[str, Any]]:
    if not candidate_ids:
        return []
    placeholders = ",".join("?" for _ in candidate_ids)
    frame = query(f"""
        SELECT v.id,v.candidate_id,c.name candidate,v.company,v.position,v.source,v.link,v.remote_location,
          v.service,v.industry_tag,v.work_type,v.fit_type,v.salary_text,v.score,v.status,
          v.company_rating,
          v.strengths,v.weaknesses,v.positioning,v.recommendation,v.risk,v.posted_at,
          v.employer_email,v.employer_contact,v.final_salary_advice,v.cover_letter,
          v.ai_analysis,v.ai_review_status,v.source_snapshot,COALESCE(v.favorite,0) favorite,
          c.target_title,c.hard_exclude,c.hard_require,c.english_level,c.notes,c.salary_min,
          i.first_seen,i.last_seen,i.change_kind,i.source_posted,i.external_id
        FROM vacancies v
        JOIN live_job_index i ON i.vacancy_id=v.id
        JOIN candidates c ON c.id=v.candidate_id
        WHERE v.user_id=? AND v.candidate_id IN ({placeholders}) AND i.active=1
          AND COALESCE(v.status,'found') <> 'archived'
        ORDER BY i.last_seen DESC,v.score DESC
        LIMIT ?
    """, tuple([user_id, *candidate_ids, int(limit)]))
    if frame.empty:
        return []
    skills_frame = query(
        f"SELECT candidate_id,skill FROM skills WHERE user_id=? AND candidate_id IN ({placeholders}) ORDER BY id",
        tuple([user_id, *candidate_ids]),
    )
    profile_skills: dict[int, list[str]] = {candidate_id: [] for candidate_id in candidate_ids}
    for item in ([] if skills_frame.empty else skills_frame.to_dict("records")):
        candidate_id = int(item.get("candidate_id") or 0)
        skill = clean_text(item.get("skill"), 100)
        if candidate_id in profile_skills and skill:
            profile_skills[candidate_id].append(skill)
    rows = frame.to_dict("records")
    seen = set()
    unique = []
    current = utcnow().astimezone(UTC)
    for row in rows:
        if row["id"] in seen:
            continue
        seen.add(row["id"])
        try:
            snapshot = json.loads(str(row.get("source_snapshot") or "{}"))
        except (TypeError, json.JSONDecodeError):
            snapshot = {}
        row["links"] = snapshot.get("links") or (
            [{"url": row.get("link"), "source": row.get("source"), "posted_at": row.get("posted_at")}]
            if row.get("link") else []
        )
        row["verified_at"] = snapshot.get("verified_at") or row.get("last_seen")
        posted_at = parse_datetime(row.get("posted_at")) or parse_datetime(row.get("source_posted"))
        verified_at = parse_datetime(row.get("verified_at")) or parse_datetime(row.get("last_seen"))
        if posted_at and current - posted_at > timedelta(days=30):
            continue
        if not posted_at and verified_at and current - verified_at > timedelta(days=30):
            continue
        remote_location = str(row.get("remote_location") or "")
        # Stored cards use both English and Russian labels.  Previously
        # "Международная удалёнка" was re-read as an office role because only
        # the English word "remote" was recognised, so valid cards vanished
        # from the dashboard after a successful search.
        is_remote = bool(re.search(
            r"\bremote\b|удал[её]н|из\s+дома|anywhere|worldwide",
            remote_location,
            flags=re.IGNORECASE,
        ))
        raw_job = {
            "title": row.get("position"),
            "company": row.get("company"),
            "description": snapshot.get("description") or "",
            "tags": snapshot.get("tags") or "",
            "location": remote_location,
            "source": row.get("source"),
            "url": row.get("link"),
            "remote": is_remote,
            "posted_at": row.get("posted_at") or row.get("source_posted") or "",
            "verified_at": row.get("verified_at") or row.get("last_seen") or "",
            "job_type": row.get("work_type") or "",
            "salary": row.get("salary_text") or "",
        }
        # Re-scoring an already saved card must use the same profile evidence
        # as the initial search.  Previously this compact dashboard query
        # omitted skills, so an API/Manual-QA job could pass collection and
        # disappear again after a page reload.  Keep it compact with one
        # correlated aggregation rather than issuing a query per card.
        profile = {
            "target_title": row.get("target_title") or "",
            "hard_exclude": row.get("hard_exclude") or "",
            "hard_require": row.get("hard_require") or "",
            "english_level": row.get("english_level") or "",
            "notes": row.get("notes") or "",
            "salary_min": row.get("salary_min") or 0,
            "skills": profile_skills.get(int(row.get("candidate_id") or 0), []),
            "allow_vietnam_hybrid": True,
            "base_country": "Vietnam",
        }
        if hard_block(raw_job, profile):
            continue
        # Re-score cached cards on every read.  A strategy can change between
        # searches; retaining yesterday's high score for a lead/director or a
        # channel digest would be misleading.
        fresh_score, fresh_reasons, fresh_risk = score_job(raw_job, profile)
        stored_score = int(row.get("score") or 0)
        if fresh_score < BROAD_REVIEW_SCORE <= stored_score:
            fresh_score = min(stored_score, GOLDEN_SCORE - 1)
            fresh_reasons = [
                *fresh_reasons[:5],
                "карточка оставлена в review-пуле после полной проверки источника",
            ]
        # Keep more current leads visible. Golden cards are still 60%+, but
        # review-worthy international QA/support roles should not disappear
        # because a live board omitted one skill or language detail.
        if fresh_score < BROAD_REVIEW_SCORE:
            continue
        row["score"] = fresh_score
        row["strengths"] = "; ".join(fresh_reasons)
        row["risk"] = fresh_risk
        presentation = vacancy_presentation(raw_job)
        priority = job_priority({**raw_job, "score": fresh_score}, presentation)
        moonlight_ok, moonlight_reason = moonlight_fit(raw_job)
        row["moonlight_compatible"] = 1 if moonlight_ok else 0
        row["moonlight_reason"] = moonlight_reason
        row["contacts"] = presentation["contacts"]
        row["equipment"] = presentation["equipment"]
        row["benefits"] = presentation["benefits"]
        row["schedule"] = presentation["schedule"]
        row["sector"] = presentation["sector"]
        row.update(priority)
        row["hot"] = bool(priority["priority_rank"] >= 22)
        row["company_rating_verified"] = False
        row["company_rating_note"] = "Рейтинг компании не проверен — проверьте отзывы, юрлицо и условия самостоятельно."
        if not int(row.get("company_rating") or 0):
            # Zero in legacy rows means "not rated", not a bad company.
            row["company_rating"] = None
        row.pop("source_snapshot", None)
        row.pop("target_title", None)
        row.pop("hard_exclude", None)
        row.pop("hard_require", None)
        row.pop("english_level", None)
        row.pop("notes", None)
        row.pop("salary_min", None)
        unique.append(row)
    unique.sort(
        key=lambda row: (
            int(row.get("favorite") or 0),
            int(row.get("priority_rank") or 0),
            int(row.get("score") or 0),
            1 if str(row.get("change_kind") or "") in {"new", "updated"} else 0,
            parse_datetime(row.get("last_seen")) or datetime(1970, 1, 1, tzinfo=UTC),
        ),
        reverse=True,
    )
    return unique


def _candidate_options(query: Callable[..., Any], user_id: int) -> tuple[list[Any], dict[Any, str]]:
    frame = query("SELECT id,name,target_title FROM candidates WHERE user_id=? ORDER BY id", (user_id,))
    options: list[Any] = ["all"] + [int(row["id"]) for _, row in frame.iterrows()]
    labels = {"all": "Все кандидаты"}
    for _, row in frame.iterrows():
        labels[int(row["id"])] = f"{row['name']} · {row.get('target_title', '')}"
    return options, labels


def digest_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = canonical_url(row.get("link")) or stable_hash(row.get("company"), row.get("position"))
        item = grouped.setdefault(key, {
            "company": row.get("company", ""), "position": row.get("position", ""),
            "source": row.get("source", ""), "link": row.get("link", ""),
            "remote": row.get("remote_location", ""), "salary": row.get("salary_text", ""),
            "company_rating": int(row.get("company_rating") or 0), "scores": {},
            "contacts": [], "max_score": 0,
        })
        candidate = clean_text(row.get("candidate"), 120) or f"Candidate {row.get('candidate_id')}"
        score = int(row.get("score") or 0)
        item["scores"][candidate] = max(score, int(item["scores"].get(candidate, 0)))
        item["max_score"] = max(int(item["max_score"]), score)
        for contact in (row.get("employer_email"), row.get("employer_contact")):
            cleaned = clean_text(contact, 240)
            if cleaned and cleaned not in item["contacts"]:
                item["contacts"].append(cleaned)
    return sorted(grouped.values(), key=lambda item: (item["max_score"], item["company_rating"]), reverse=True)


def _render_brand_header(st: Any) -> None:
    st.markdown("""
        <section class="cm-cyber-head">
          <div><span class="cm-eyebrow">CAREERMOVE // DAILY SIGNAL</span>
          <h1>Золотые вакансии без шума</h1>
          <p>Сначала география и жёсткие фильтры, затем фактический опыт каждого кандидата, и только после этого — свежесть и бонусы.</p></div>
          <div class="cm-signal-grid" aria-hidden="true"><i></i><i></i><i></i><i></i><i></i><i></i><i></i><i></i><i></i></div>
        </section>
    """, unsafe_allow_html=True)


def _render_search_flow(st: Any) -> None:
    steps = [
        ("01", "Источники", "Собираем свежие QA-вакансии"),
        ("02", "Строгий фильтр", "Убираем офис, релокацию и чужую географию"),
        ("03", "Опыт", "Сравниваем отдельно с каждым резюме"),
        ("04", "GPT — дополнительно", "Уточняем порядок, если API доступен"),
        ("05", "Ваше решение", "Показываем, но ничего не отправляем"),
    ]
    cards = "".join(
        f"<div class='cm-flow-step'><b>{number}</b><strong>{title}</strong><span>{detail}</span></div>"
        for number, title, detail in steps
    )
    st.markdown(
        "<div class='cm-flow' aria-label='Этапы поиска'>" + cards + "</div>",
        unsafe_allow_html=True,
    )


def _render_service_tour(
    st: Any, user_id: int, get_setting: Callable[..., Any], set_setting: Callable[..., Any], nav: str,
) -> None:
    with st.expander("Как пользоваться · 4 шага", expanded=False):
        st.caption("Четыре шага от профиля до ручного отклика. Тур всегда можно открыть снова.")
        tab_profile, tab_search, tab_scores, tab_reply = st.tabs([
            "1 · Профили", "2 · Поиск", "3 · Оценки", "4 · Отклик",
        ])
        with tab_profile:
            st.markdown(
                "**Что проверить:** актуальный опыт, желаемую роль, английский, личные исключения и PDF-резюме "
                "для QA-кандидата и Support-кандидата. От этих данных зависит весь рейтинг."
            )
            if st.button("Открыть профили", key=f"tour_profiles_{nav}"):
                st.session_state["_requested_nav"] = "Profiles"
                st.rerun()
        with tab_search:
            st.markdown(
                "Нажмите **«Обновить подборку»**. Обычно поиск занимает 15–60 секунд. "
                "На экране появятся этапы: источники → фильтры → опыт → необязательный GPT → сохранение."
            )
            st.info("Можно оставить вкладку открытой: CareerMove сам покажет, когда работа закончена.")
        with tab_scores:
            score_cols = st.columns(3)
            score_cols[0].success("**80–100% · Золотая**\n\nМожно готовить отклик.")
            score_cols[1].info("**60–79% · Проверить**\n\nЕсть совпадения и заметные пробелы.")
            score_cols[2].warning("**45-59% · Review**\n\nПоказываем как запасные лиды с рисками.")
            st.caption("Ноль золотых вакансий означает строгий успешный отбор, а не поломку поиска.")
        with tab_reply:
            st.markdown(
                "Откройте вакансию, проверьте оригинал и нажмите **«Подготовить отклик»**. "
                "В Центре откликов выберите правильное резюме, проверьте письмо и отправьте его лично."
            )
            if st.button("Открыть Центр откликов", key=f"tour_email_{nav}"):
                st.session_state["_requested_nav"] = "Email Center"
                st.rerun()
        if not completed and st.button("Понятно, свернуть обучение", key=f"tour_done_{nav}"):
            set_setting(user_id, "guided_search_tour_done", "1")
            st.rerun()


def _render_ai_status(st: Any, status: dict[str, Any]) -> None:
    title = clean_text(status.get("title"), 240) or "Статус дополнительной проверки"
    detail = clean_text(status.get("detail"), 900)
    level = status.get("level", "info")
    message = f"**{title}**\n\n{detail}"
    if level == "success":
        st.success(message)
    elif level == "warning":
        st.warning(message)
    else:
        st.info(message)
    if status.get("code") == "quota_exceeded":
        with st.expander("Что сделать с лимитом OpenAI"):
            st.write(
                "1. Проверьте расход API и остаток кредитов. 2. Проверьте месячный лимит проекта. "
                "3. После пополнения нажмите «Разрешить одну GPT-проверку», а затем обновите подборку."
            )
            links = st.columns(2)
            links[0].link_button("Расход API ↗", "https://platform.openai.com/usage", use_container_width=True)
            links[1].link_button("Лимиты проекта ↗", "https://platform.openai.com/settings/organization/limits", use_container_width=True)


def _render_search_summary(st: Any, result: dict[str, Any]) -> None:
    st.success("Поиск завершён. Можно просматривать результаты — ждать больше не нужно.")
    metrics = st.columns(3)
    metrics[0].metric("Уникальных вакансий в источниках", int(result.get("raw_count") or 0))
    metrics[1].metric("Карточек для ручной проверки", len(result.get("saved") or []))
    metrics[2].metric("Подходящих совпадений 60%+", int(result.get("golden_count") or 0))
    if not int(result.get("golden_count") or 0):
        st.info(
            "Сегодня порог 60% не прошла ни одна вакансия. Это не ошибка: лучше пустой список, "
            "чем нерелевантные роли ниже минимального совпадения."
        )
    stats = result.get("candidate_stats") or []
    if stats:
        with st.expander("Результат отдельно по каждому кандидату"):
            for item in stats:
                st.write(
                    f"**{item.get('candidate', 'Кандидат')}** · для проверки: {int(item.get('review') or 0)} · "
                    f"золотых: {int(item.get('golden') or 0)}"
                )


def _render_notification_opt_in(st: Any, golden_count: int) -> None:
    import streamlit.components.v1 as components

    day_key = utcnow().date().isoformat()
    message = f"CareerMove: {golden_count} золотых вакансий готовы для проверки"
    components.html(f"""
      <div style="font:600 13px Inter,system-ui;color:#cbd5e1;display:flex;gap:10px;align-items:center;flex-wrap:wrap">
        <button id="cmNotify" style="border:1px solid #22d3ee;background:#07111f;color:#e6fbff;border-radius:10px;padding:9px 13px;cursor:pointer">Включить уведомления macOS</button>
        <span id="cmNotifyState">Уведление приходит один раз в день, когда страница открыта.</span>
      </div>
      <script>
        const btn = document.getElementById('cmNotify');
        const state = document.getElementById('cmNotifyState');
        const N = window.parent.Notification;
        const storage = window.parent.localStorage;
        const key = {json.dumps('careermove-digest-' + day_key)};
        const count = {int(golden_count)};
        const notify = () => {{
          if (N && N.permission === 'granted' && count > 0 && !storage.getItem(key)) {{
            new N('CareerMove · золотая подборка', {{body: {json.dumps(message)}, tag: key}});
            storage.setItem(key, '1');
            state.textContent = 'Уведомления включены.';
          }}
        }};
        if (!N) {{ btn.disabled = true; state.textContent = 'Этот браузер не поддерживает системные уведомления.'; }}
        else {{
          if (N.permission === 'granted') notify();
          btn.onclick = async () => {{ const result = await N.requestPermission(); state.textContent = result === 'granted' ? 'Уведомления включены.' : 'Разрешение не выдано.'; notify(); }};
        }}
      </script>
    """, height=48)


def _render_daily_digest(st: Any, rows: list[dict[str, Any]]) -> None:
    digest = digest_rows(rows)
    golden = [item for item in digest if int(item["max_score"]) >= GOLDEN_SCORE]
    candidate_names = sorted({name for item in digest for name in item["scores"]})
    metric_cols = st.columns(max(1, min(4, len(candidate_names) + 1)))
    metric_cols[0].metric("Золотых вакансий", len(golden))
    for index, name in enumerate(candidate_names[:3], start=1):
        if index >= len(metric_cols):
            break
        metric_cols[index].metric(name, sum(1 for item in golden if int(item["scores"].get(name, 0)) >= GOLDEN_SCORE))
    _render_notification_opt_in(st, len(golden))
    if not golden:
        st.info(
            "Поиск завершён успешно, но сегодня нет вакансий, прошедших порог 60%. "
            "60%+ остаются золотыми, а роли 45-59% показываются как review-кандидаты с рисками."
        )
        return
    st.markdown("### Главные кандидаты дня")
    for item in golden[:8]:
        scores = " · ".join(f"{name}: **{score}%**" for name, score in sorted(item["scores"].items()))
        with st.container(border=True):
            left, right = st.columns([5, 1])
            with left:
                st.markdown(f"#### {item['company']} · {item['position']}")
                st.markdown(scores)
                st.caption(
                    f"{item['source']} · {item['remote'] or 'remote terms not stated'} · "
                    f"{display_text(item['salary']) or 'salary not disclosed'} · company {item['company_rating']}/100"
                )
                if item["contacts"]:
                    st.write("**Контакт:** " + " · ".join(item["contacts"]))
            with right:
                if item["link"]:
                    st.link_button("Открыть ↗", item["link"], use_container_width=True)


def manual_job(text: str, *, title: str, company: str, link: str = "", source: str = "Manual/Social") -> dict[str, Any]:
    blob = clean_text(text, 12000)
    low = blob.lower()
    remote = bool(re.search(r"\bremote\b|удален|удалён", low)) and not any(
        phrase in low for phrase in ("hybrid only", "office only", "только офис", "гибридный формат")
    )
    job_type = "part-time" if "part-time" in low or "частичная занятость" in low else "contract" if "contract" in low or "аутстафф" in low else "full-time"
    salary_match = re.search(
        r"(?:[$€£]\s?\d[\d\s.,kK-]*|\d[\d\s.,-]*\s?(?:₽|руб(?:лей)?|USD|EUR|gross|net))",
        blob, flags=re.IGNORECASE,
    )
    return make_job(
        source=source, external_id=stable_hash(title, company, link, blob)[:24], url=link,
        title=title, company=company, description=blob, location="Remote" if remote else "Location from vacancy text",
        job_type=job_type, posted_at=iso(), salary=salary_match.group(0) if salary_match else "", remote=remote, tags=blob,
    )


def _render_manual_import(
    st: Any, query: Callable[..., Any], execute: Callable[..., Any], user_id: int,
    candidate_ids: list[int], labels: dict[Any, str],
) -> None:
    with st.expander("Добавить сильную вакансию из Telegram / соцсетей вручную"):
        st.caption("Ручные находки проходят тот же строгий профильный score и становятся частью рейтинга — этот канал не теряется.")
        col1, col2 = st.columns(2)
        title = col1.text_input("Название вакансии", key="manual_job_title")
        company = col2.text_input("Компания", key="manual_job_company")
        link = st.text_input("Ссылка на источник", key="manual_job_link")
        target_ids = st.multiselect(
            "Проверить для кандидатов", candidate_ids, default=candidate_ids,
            format_func=lambda value: labels.get(value, str(value)), key="manual_job_candidates",
        )
        body = st.text_area("Полный текст вакансии", height=260, key="manual_job_body")
        if st.button("Проверить совпадение и добавить", key="manual_job_save", disabled=not (title and company and body and target_ids)):
            item = manual_job(body, title=title, company=company, link=link)
            saved, rejected = [], []
            for candidate_id in target_ids:
                profile = candidate_profile(query, user_id, int(candidate_id))
                score, reasons, risk = score_job(item, profile)
                if score <= 0:
                    rejected.append(f"{profile.get('name')}: {risk}")
                    continue
                ranked = dict(item)
                ranked.update({"score": score, "local_score": score, "reasons": reasons, "risk": risk})
                ranked = enrich_job_for_profile(ranked, profile)
                save_job(query, execute, user_id, int(candidate_id), ranked)
                saved.append(f"{profile.get('name')}: {score}%")
            if saved:
                st.success("Добавлено · " + " · ".join(saved))
            for reason in rejected:
                st.warning(reason)


def _render_results(
    st: Any, rows: list[dict[str, Any]], execute: Callable[..., Any], user_id: int,
) -> None:
    if not rows:
        st.info("Сохранённых вакансий пока нет. Нажмите «Обновить подборку» и дождитесь сообщения «Поиск завершён».")
        return
    golden_rows = [row for row in rows if int(row.get("score") or 0) >= GOLDEN_SCORE]
    review_rows = [row for row in rows if REVIEW_SCORE <= int(row.get("score") or 0) < GOLDEN_SCORE]
    view_options = [
        "Подходящие 60%+",
        "Проверить вручную",
        "Все сохранённые",
    ]
    default_index = 0 if golden_rows else 1 if review_rows else 2
    selected_view = st.radio(
        "Какие результаты показать", view_options, index=default_index, horizontal=True,
        key="live_result_view", help="60%+ — можно готовить отклик после ручной проверки; ниже 60% скрывается.",
    )
    st.caption(
        f"Золотых: {len(golden_rows)} · перспективных: {len(review_rows)} · всего сохранено: {len(rows)}"
    )
    if selected_view == view_options[0]:
        visible_rows = golden_rows
    elif selected_view == view_options[1]:
        visible_rows = review_rows
    else:
        visible_rows = rows
    if not visible_rows:
        st.info("В этой категории пока ничего нет. Выберите соседнюю вкладку результатов.")
        return
    for row in visible_rows[:40]:
        score = int(row.get("score") or 0)
        posted = parse_datetime(row.get("source_posted") or row.get("posted_at"))
        if posted:
            days = max(0, (utcnow() - posted).days)
            age = "сегодня" if days == 0 else f"{days} дн. назад"
        else:
            age = "дата не указана"
        change = {"new": "НОВАЯ", "updated": "ОБНОВЛЕНА", "seen": "ПРОВЕРЕНА"}.get(str(row.get("change_kind")), "АКТИВНА")
        score_state = "ЗОЛОТАЯ · можно готовить отклик" if score >= GOLDEN_SCORE else "ПРОВЕРИТЬ · есть заметные пробелы"
        with st.container(border=True):
            left, right = st.columns([5, 1])
            with left:
                st.caption(score_state)
                st.markdown(f"### {score}% · {row.get('position', 'Вакансия без названия')}")
                st.write(f"**{row.get('company', 'Компания не указана')}** · {row.get('candidate', '')} · {row.get('source', '')} · {row.get('remote_location', 'Удалённо')}")
                st.caption(
                    f"{change} · {age} · {display_text(row.get('salary_text')) or 'зарплата не указана'} · "
                    f"рейтинг компании {int(row.get('company_rating') or 0)}/100"
                )
                if row.get("strengths"):
                    st.write(f"**Почему вакансия показана:** {display_text(row['strengths'])}")
                if row.get("weaknesses"):
                    st.write(f"**Что обязательно проверить:** {display_text(row['weaknesses'])}")
                if row.get("positioning"):
                    st.write(f"**Совпадение по опыту:** {display_text(row['positioning'])}")
                if row.get("final_salary_advice"):
                    st.write(f"**Ориентир по зарплате:** {display_text(row['final_salary_advice'])}")
                if row.get("recommendation"):
                    st.write(f"**Следующий шаг:** {display_text(row['recommendation'])}")
                contact_bits = [
                    clean_text(value, 300) for value in (row.get("employer_email"), row.get("employer_contact"))
                    if clean_text(value, 300)
                ]
                if contact_bits:
                    st.markdown("**Контакты работодателя**  \n" + "  \n".join(contact_bits))
                else:
                    st.caption("Контакты не опубликованы — используйте форму на исходной странице вакансии.")
            with right:
                if row.get("link"):
                    st.link_button("Открыть вакансию ↗", row["link"], use_container_width=True)
            actions = st.columns(5)
            vacancy_id = int(row["id"])
            if actions[0].button("Одобрить", key=f"live_approve_{vacancy_id}", use_container_width=True):
                execute("UPDATE vacancies SET status='approved' WHERE id=? AND user_id=?", (vacancy_id, user_id))
                st.toast("Добавлено в «Одобренные»")
                st.rerun()
            if actions[1].button("Позже", key=f"live_later_{vacancy_id}", use_container_width=True):
                execute("UPDATE vacancies SET status='later' WHERE id=? AND user_id=?", (vacancy_id, user_id))
                st.toast("Сохранено на потом")
                st.rerun()
            if actions[2].button("Подготовить отклик", key=f"live_cover_{vacancy_id}", use_container_width=True):
                st.session_state["_email_vacancy_id"] = vacancy_id
                st.session_state["_requested_nav"] = "Email Center"
                st.rerun()
            if actions[3].button("Полная карточка", key=f"live_card_{vacancy_id}", use_container_width=True):
                st.session_state["_requested_nav"] = "Job Cards"
                st.rerun()
            if actions[4].button("Скрыть", key=f"live_skip_{vacancy_id}", use_container_width=True):
                execute("UPDATE vacancies SET status='skip' WHERE id=? AND user_id=?", (vacancy_id, user_id))
                st.toast("Вакансия скрыта")
                st.rerun()
            if row.get("ai_analysis"):
                with st.expander("Полный разбор вакансии"):
                    st.markdown(row["ai_analysis"])


def _render_directory(st: Any) -> None:
    with st.expander("Дополнительные международные площадки"):
        st.caption(f"{len(SOURCE_SPECS)} источников обновляются автоматически. Остальные ссылки открываются для выборочного ручного поиска.")
        for group, services in PLATFORM_DIRECTORY.items():
            group_title = {
                "Live feeds": "Автоматические источники",
                "Employment": "Работа в штате",
                "Freelance and part-time": "Фриланс и частичная занятость",
            }.get(group, group)
            st.markdown(f"#### {group_title}")
            for name, url, note in services:
                st.markdown(f"[{name}]({url}) — {note}")


def render_page(
    nav: str, user_id: int, query: Callable[..., Any], execute: Callable[..., Any],
    get_setting: Callable[..., Any], set_setting: Callable[..., Any],
) -> None:
    import streamlit as st
    import streamlit.components.v1 as components

    ensure_schema(execute)
    if nav == "Today":
        # A lightweight rerun keeps the open Streamlit session alive and lets
        # the six-hour due check run without asking the user to refresh.
        components.html(
            """
            <script>
              const key = 'careermove-today-autorefresh';
              if (!window.parent[key]) {
                window.parent[key] = window.parent.setTimeout(
                  () => window.parent.location.reload(), 10 * 60 * 1000
                );
              }
            </script>
            """,
            height=0,
        )
    if get_setting(user_id, "live_jobs_legacy_archived", "") != "1":
        archived = archive_legacy_page_fragments(query, execute, user_id)
        set_setting(user_id, "live_jobs_legacy_archived", "1")
        if archived:
            st.toast(f"Archived {archived} duplicate legacy page fragments")

    options, labels = _candidate_options(query, user_id)
    candidate_ids = [value for value in options if value != "all"]
    _render_brand_header(st)
    st.caption("Русскоязычные компании за рубежом + международные источники · строгая география · опыт из текущего CV · два кандидата отдельно")
    st.markdown(
        f"<div class='cm-network-note'><b>Сеть:</b> {search_network_label()}. "
        "VPN на телефоне или компьютере пользователя не требуется.</div>",
        unsafe_allow_html=True,
    )
    _render_search_flow(st)
    _render_service_tour(st, user_id, get_setting, set_setting, nav)
    if nav == "Today":
        selected = "all"
        st.info("Сегодня CareerMove сравнивает каждую вакансию с QA- и Support-профилями отдельно. Результаты не смешиваются.")
    else:
        selected = st.selectbox("Кандидат", options, format_func=lambda value: labels[value], key=f"live_candidate_{nav}")
    selected_ids = [value for value in options if value != "all"] if selected == "all" else [int(selected)]

    ai_pause_raw = get_setting(user_id, AI_PAUSE_SETTING, "")
    ai_pause = active_ai_pause(ai_pause_raw)
    if ai_pause_raw and not ai_pause:
        set_setting(user_id, AI_PAUSE_SETTING, "")
    ai_widget_key = f"live_ai_{nav}_{'paused' if ai_pause else 'active'}"

    with st.expander("Фильтры поиска", expanded=nav != "Today"):
        col1, col2 = st.columns(2)
        with col1:
            min_default = 70 if nav == "AI Market Search" else 60
            min_score = st.slider(
                "Минимальное совпадение для сохранения", 40, 95, min_default, key=f"live_min_{nav}",
                help="Основной порог CareerMove — 60%. Более низкое значение стоит использовать только для диагностики источников.",
            )
            try:
                age_default = max(1, min(60, int(get_setting(user_id, "search_max_age_days", "14") or 14)))
            except ValueError:
                age_default = 14
            max_age = st.slider("Не старше, дней", 1, 60, age_default, key=f"live_age_{nav}")
        with col2:
            allow_vietnam_hybrid = get_setting(user_id, "search_vietnam_hybrid", "1") == "1"
            remote_label = "Remote + офис/гибрид во Вьетнаме" if allow_vietnam_hybrid else "Только удалённая работа"
            remote_only = st.checkbox(remote_label, value=True, key=f"live_remote_{nav}")
            types = st.multiselect(
                "Формат занятости", ["full-time", "contract", "part-time", "freelance", "internship", "temporary"],
                default=["full-time", "contract", "part-time", "freelance"], key=f"live_types_{nav}",
            )
        use_ai = st.checkbox(
            "Дополнительно уточнить рейтинг через GPT", value=not bool(ai_pause), key=ai_widget_key,
            disabled=bool(ai_pause),
            help="Необязательный этап. После 429 он автоматически ставится на паузу, а локальный рейтинг продолжает работать.",
        )
        if ai_pause:
            use_ai = False

    if ai_pause:
        if ai_pause.get("code") == "quota_exceeded":
            st.warning(
                "**Основной подбор работает · GPT на паузе**\n\n"
                "OpenAI сообщил об исчерпанной квоте или месячном лимите. CareerMove больше не повторяет запросы "
                "автоматически и продолжает искать, фильтровать и оценивать вакансии локально."
            )
        else:
            until = parse_datetime(ai_pause.get("until"))
            until_text = until.astimezone(timezone(timedelta(hours=3))).strftime("%H:%M МСК") if until else "через несколько минут"
            st.warning(
                "**Основной подбор работает · GPT временно на паузе**\n\n"
                f"После ограничения частоты новые запросы к модели отключены до {until_text}. "
                "Вакансии и рейтинг опыта продолжают обновляться без GPT."
            )
        pause_cols = st.columns([2, 3])
        if pause_cols[0].button("Разрешить одну GPT-проверку", key=f"live_ai_resume_{nav}", use_container_width=True):
            set_setting(user_id, AI_PAUSE_SETTING, "")
            st.rerun()
        pause_cols[1].caption("Кнопка только снимает паузу. Письма и отклики она не отправляет.")
    elif open_model_config():
        provider, _, model = open_model_config() or ("", "", "")
        st.info(
            f"Дополнительная проверка включена: **{provider} · {model}**. "
            "Это необязательный этап — основной отбор работает независимо от API."
        )
    else:
        st.info("Основной поиск готов. API-ключ модели не задан, поэтому будет использован прозрачный локальный рейтинг.")

    last_run = parse_datetime(get_setting(user_id, "live_jobs_last_auto", ""))
    if last_run:
        moscow_time = last_run.astimezone(timezone(timedelta(hours=3))).strftime("%d.%m.%Y · %H:%M МСК")
        last_count = int(get_setting(user_id, "live_jobs_last_count", "0") or 0)
        st.caption(f"Последняя завершённая проверка: {moscow_time} · сохранено карточек: {last_count}")

    refresh = st.button(
        "Обновить подборку сейчас" if nav == "Today" else "Найти свежие вакансии",
        type="primary", key=f"live_refresh_{nav}",
    )
    st.caption("После запуска ничего дополнительно нажимать не нужно. Обычно проверка занимает 15–60 секунд; ниже появятся этапы и итог.")
    today_key = f"{MATCHING_VERSION}:{utcnow().date().isoformat()}"
    try:
        schedule_hours = max(3, min(24, int(get_setting(user_id, "live_jobs_schedule_hours", "6") or 6)))
    except ValueError:
        schedule_hours = 6
    auto_due = nav == "Today" and (
        not last_run or utcnow() - last_run >= timedelta(hours=schedule_hours)
    )

    result = None
    if refresh or auto_due:
        with st.status("Поиск запущен · можно наблюдать за этапами", expanded=True) as search_status:
            seen_progress = set()

            def report_progress(stage: str, detail: str) -> None:
                marker = (stage, detail)
                if marker not in seen_progress:
                    seen_progress.add(marker)
                    search_status.write(detail)

            result = run_search(
                query, execute, user_id, selected_ids, min_score=min_score, max_age_days=max_age,
                remote_only=remote_only, job_types=set(types), limit_per_candidate=30,
                force_refresh=refresh, use_open_model=use_ai,
                progress=report_progress,
            )
            search_status.update(
                label=f"Готово · проверено {result['raw_count']} уникальных вакансий",
                state="complete", expanded=True,
            )
        set_setting(user_id, "live_jobs_last_auto", iso())
        set_setting(user_id, "live_jobs_last_count", str(len(result["saved"])))
        set_setting(user_id, "live_jobs_last_ai_status", json.dumps(result["ai_statuses"], ensure_ascii=False))
        new_ai_pause = ai_pause_from_statuses(result["ai_statuses"])
        if new_ai_pause:
            set_setting(user_id, AI_PAUSE_SETTING, json.dumps(new_ai_pause, ensure_ascii=False))
        elif any(status.get("code") == "completed" for status in result["ai_statuses"]):
            set_setting(user_id, AI_PAUSE_SETTING, "")
        if nav == "Today":
            set_setting(user_id, "live_jobs_daily_date", today_key)
        _render_search_summary(st, result)
        try:
            try:
                from app import public_release as _public_release
            except ImportError:
                import public_release as _public_release
            notification_statuses = _public_release.deliver_digest_notifications(
                query, execute, user_id, result.get("saved") or [],
                app_url=os.getenv("APP_URL", ""),
            )
            for notification in notification_statuses:
                label = "Email" if notification["channel"] == "email" else "Telegram"
                if notification["status"] in {"sent", "already_sent"}:
                    st.success(f"{label}: {notification['detail']}")
                else:
                    st.warning(f"{label}: {notification['detail']}")
        except Exception:
            st.caption("Сводка сохранена. Канал уведомлений временно недоступен.")
        for status in result["ai_statuses"]:
            _render_ai_status(st, status)
        with st.expander("Источники: что обновилось"):
            for item in result["diagnostics"]:
                icon = "✅" if item["status"] == "updated" else "🕒" if item["status"] == "cached" else "⚠️"
                state = "обновлено" if item["status"] == "updated" else "использован свежий кэш" if item["status"] == "cached" else "источник временно недоступен"
                st.write(f"{icon} **{item['source']}** · вакансий: {item['count']} · {state}")
        if result.get("excluded"):
            with st.expander("Почему строгий фильтр скрыл вакансии"):
                for item in result["excluded"][:24]:
                    st.write(f"**{item['candidate']} · {item['company']} · {item['position']}** — {item['reason']}")

    rows = latest_jobs(query, user_id, selected_ids)
    if selected == "all":
        _render_daily_digest(st, rows)
    _render_manual_import(st, query, execute, user_id, candidate_ids, labels)
    st.subheader("Результаты для ручной проверки")
    st.caption("Сначала выберите уровень совпадения. Карточка объясняет, почему вакансия показана и что нужно проверить до отклика.")
    _render_results(st, rows, execute, user_id)
    _render_directory(st)
    st.caption("Автоисточники: Talanto, 6 зарубежных Telegram-каналов, Хабр Карьера, Arbeitnow, Remote OK, We Work Remotely, Remotive и Jobicy. Перед откликом обязательно проверьте оригинальную публикацию и условия оформления.")
