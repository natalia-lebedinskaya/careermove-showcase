from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
import math
import os
import re
import requests
import secrets
import time
import uuid
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Literal
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request as FastAPIRequest, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app import live_jobs, public_release, push_notifications
from api.database import Database


UTC = timezone.utc
MOSCOW_TIMEZONE = timezone(timedelta(hours=3))
db = Database()
_schema_ready = False
API_SECRET = os.getenv("CAREERMOVE_API_SECRET", secrets.token_urlsafe(48)).encode("utf-8")
SOURCE_CATALOG = tuple(
    (
        name,
        "live feed",
        "international",
        str(spec.get("url") or ""),
        str(spec.get("attribution") or ""),
    )
    for name, spec in live_jobs.SOURCE_SPECS.items()
)
SEARCH_LAUNCH_VERSION = "foreground-first-stable-search-2026-08-14"
AUTH_BOOTSTRAP_VERSION = "schema-guard-seed-2026-08-14"
INTERACTIVE_SOURCE_BATCH_SIZE = 9
AI_PROVIDER_DEFINITIONS = (
    {"id": "vercel_gateway", "label": "Vercel AI Gateway", "key_env": "AI_GATEWAY_API_KEY", "model_env": "AI_GATEWAY_MODEL", "default_model": "openai/gpt-oss-20b"},
    {"id": "openai", "label": "OpenAI", "key_env": "OPENAI_API_KEY", "model_env": "OPENAI_MODEL", "default_model": "gpt-4.1-mini"},
    {"id": "gemini", "label": "Gemini", "key_env": "GEMINI_API_KEY", "model_env": "GEMINI_MODEL", "default_model": "gemini-2.5-flash"},
    {"id": "anthropic", "label": "Claude", "key_env": "ANTHROPIC_API_KEY", "model_env": "ANTHROPIC_MODEL", "default_model": "claude-3-5-haiku-latest"},
    {"id": "groq", "label": "Groq", "key_env": "GROQ_API_KEY", "model_env": "GROQ_MODEL", "default_model": "openai/gpt-oss-20b"},
    {"id": "openrouter", "label": "OpenRouter", "key_env": "OPENROUTER_API_KEY", "model_env": "OPENROUTER_MODEL", "default_model": "openrouter/free"},
)

# These are real public directories that do not expose a stable, documented
# machine-readable vacancy feed.  They are intentionally presented as manual
# verification links and kept out of automatic collection: pretending that a
# browser scrape is a live API would create stale or duplicated jobs.
REFERENCE_SOURCE_CATALOG = (
    ("ITviec", "manual verified link", "Vietnam · IT", "https://itviec.com/it-jobs", "Vietnamese IT board; verify work permit, office city and English requirements on the original posting."),
    ("VietnamWorks", "manual verified link", "Vietnam", "https://www.vietnamworks.com/", "Large Vietnam job board; keep only a current original vacancy link."),
    ("TopCV Vietnam", "manual verified link", "Vietnam", "https://www.topcv.vn/viec-lam", "Vietnam job board; check employer and contract directly."),
    ("CareerViet", "manual verified link", "Vietnam", "https://careerviet.vn/viec-lam/", "Vietnam employment listings; check visa/work permit language."),
    ("JobsGO", "manual verified link", "Vietnam", "https://jobsgo.vn/viec-lam.html", "Vietnam jobs including entry-level and support roles."),
    ("Glints Vietnam", "manual verified link", "Vietnam", "https://glints.com/vn/en/opportunities/jobs/explore", "Regional startup and entry-level roles."),
    ("JobStreet Vietnam", "manual verified link", "Vietnam", "https://www.jobstreet.vn/en/job-search/", "Regional job board; use the original employer posting."),
    ("Indeed Vietnam", "manual verified link", "Vietnam", "https://vn.indeed.com/", "Vietnam search; aggregator results require original-source verification."),
    ("LinkedIn Jobs · Vietnam", "manual verified link", "Vietnam", "https://www.linkedin.com/jobs/search/?location=Vietnam", "Public search link only; CareerMove does not access private LinkedIn data."),
    ("CareerLink Vietnam", "manual verified link", "Vietnam", "https://www.careerlink.vn/viec-lam.html", "Vietnam listings; verify the original employer, contract and permit support."),
    ("Vieclam24h", "manual verified link", "Vietnam", "https://vieclam24h.vn/", "Public Vietnam listings; confirm the original employer and date."),
    ("Timviecnhanh", "manual verified link", "Vietnam", "https://timviecnhanh.com/", "Public Vietnam listings; confirm legal work eligibility with the employer."),
    ("VietnamWorks IT", "manual verified link", "Vietnam · IT", "https://www.vietnamworks.com/it-jobs", "IT and support roles in Vietnam; verify language and work permit support."),
    ("Vietnam Teaching Jobs", "manual verified link", "Vietnam · training", "https://vietnamteachingjobs.com/", "Use only if the original post confirms training and legal work support."),
    ("Relocate.me", "manual verified link", "Relocation", "https://relocate.me/", "Relocation-friendly technology vacancies; confirm Vietnam eligibility."),
    ("Himalayas", "manual verified link", "Remote international", "https://himalayas.app/jobs", "Remote-first companies; confirm hiring country and contractor status."),
    ("Wellfound", "manual verified link", "Remote international", "https://wellfound.com/jobs", "Startup vacancies; original company link required."),
    ("HeadHunter · international", "manual verified link", "International", "https://hh.ru/search/vacancy?text=remote", "Manual public search only; no applicant OAuth or auto-apply integration."),
    ("Habr Career · international", "manual verified link", "Russian-speaking abroad", "https://career.habr.com/vacancies", "Keep only explicit worldwide/Vietnam/Kazakhstan remote roles, never Russia-only employment."),
    ("Kolesa Group Careers", "manual verified link", "Kazakhstan / remote", "https://kolesa.group/careers", "Russian-speaking international company; verify worldwide remote and legal employer in each role."),
    ("Astana Hub Jobs", "manual verified link", "Kazakhstan", "https://astanahub.com/ru/jobs", "Kazakhstan technology vacancies; verify remote work from Vietnam."),
    ("Remote.co", "manual verified link", "Remote international", "https://remote.co/remote-jobs/", "Public remote roles; confirm Vietnam hiring eligibility."),
    ("Working Nomads", "manual verified link", "Remote international", "https://www.workingnomads.com/jobs", "Public remote and contract roles; verify work country."),
    ("Dynamite Jobs", "manual verified link", "Remote international", "https://dynamitejobs.com/", "Remote-first listings; verify employer and country restrictions."),
    ("Jobspresso", "manual verified link", "Remote international", "https://jobspresso.co/remote-work/", "Public remote jobs; original company link required."),
    ("No Fluff Jobs", "manual verified link", "Europe", "https://nofluffjobs.com/", "European tech roles, often with salary ranges."),
    ("TestDevJobs", "manual verified link", "International", "https://testdevjobs.com/", "Public software testing vacancies."),
    ("Contra", "manual verified link", "Freelance", "https://contra.com/jobs", "Public project work; contact details must be verified manually."),
    ("PeoplePerHour", "manual verified link", "Freelance", "https://www.peopleperhour.com/freelance-jobs", "Public freelance marketplace link."),
    ("Guru", "manual verified link", "Freelance", "https://www.guru.com/d/jobs/", "Public freelance marketplace link."),
    ("Freelancer", "manual verified link", "Freelance", "https://www.freelancer.com/jobs/", "Public project marketplace link."),
    ("Upwork public catalogue", "manual verified link", "Freelance", "https://www.upwork.com/nx/search/jobs/", "Public catalogue only; no private account scraping."),
    ("Archinect Jobs", "manual verified link", "Architecture & landscape", "https://archinect.com/jobs", "Architecture, visualization and landscape-adjacent projects."),
    ("Dezeen Jobs", "manual verified link", "Architecture & design", "https://www.dezeenjobs.com/", "Design and visualization listings."),
    ("Behance JobList", "manual verified link", "Creative", "https://www.behance.net/joblist", "Public creative and visualization work."),
    ("Dribbble Jobs", "manual verified link", "Creative", "https://dribbble.com/jobs", "Public creative roles and projects."),
    ("Europe Language Jobs", "manual verified link", "Europe", "https://www.europelanguagejobs.com/", "European roles where language requirements are explicit."),
    ("Landing.Jobs", "manual verified link", "Europe", "https://landing.jobs/", "European technology vacancies."),
    ("Telegram · Vietnam IT Jobs", "manual verified link", "Vietnam / community", "https://t.me/s/vietnamitjobs", "Public channel link only; verify original employer, contacts and current date."),
    ("Facebook · Expats in Da Nang", "manual verified link", "Da Nang / community", "https://www.facebook.com/groups/expatsindanang/", "Community link only; never collected automatically and every lead needs verification."),
)


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if hasattr(value, "item"):
        return json_safe(value.item())
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def ensure_runtime_schema() -> None:
    """Prepare storage once unless production uses a pre-migrated database."""
    global _schema_ready
    if _schema_ready:
        return
    if os.getenv("CAREERMOVE_SCHEMA_ON_START", "1").strip().lower() in {"0", "false", "no"}:
        _schema_ready = True
        return
    db.ensure_schema()
    _schema_ready = True


def _ai_setting_key(provider_id: str, suffix: str) -> str:
    return f"ai_{provider_id}_{suffix}"


def _read_int_setting(query, user_id: int, key: str, default: int, *, low: int, high: int) -> int:
    try:
        value = int(public_release.read_setting(query, user_id, key, str(default)) or default)
    except ValueError:
        value = default
    return max(low, min(value, high))


def user_ai_runtime_env(query, user_id: int) -> dict[str, str]:
    if public_release.read_setting(query, user_id, "ai_online_enabled", "1") == "0":
        return {}
    mode = public_release.read_setting(query, user_id, "ai_provider_mode", "ensemble") or "ensemble"
    max_providers = _read_int_setting(query, user_id, "ai_max_providers", 3, low=1, high=5)
    env: dict[str, str] = {
        "OPEN_MODEL_PROVIDER": mode,
        "AI_MAX_PROVIDERS": str(max_providers),
    }
    for provider in AI_PROVIDER_DEFINITIONS:
        provider_id = str(provider["id"])
        key = public_release.read_setting(query, user_id, _ai_setting_key(provider_id, "api_key"), "").strip()
        model = public_release.read_setting(query, user_id, _ai_setting_key(provider_id, "model"), "").strip()
        if key:
            env[str(provider["key_env"])] = key
        if provider_id == "gemini" and key:
            env["GOOGLE_API_KEY"] = key
        if model:
            env[str(provider["model_env"])] = model
    return env


@contextmanager
def temporary_environ(values: dict[str, str]):
    if not values:
        yield
        return
    previous = {key: os.environ.get(key) for key in values}
    try:
        for key, value in values.items():
            os.environ[key] = value
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def ai_settings_payload(user_id: int) -> dict[str, Any]:
    query = db.query
    mode = public_release.read_setting(query, user_id, "ai_provider_mode", "ensemble") or "ensemble"
    max_providers = _read_int_setting(query, user_id, "ai_max_providers", 3, low=1, high=5)
    enabled = public_release.read_setting(query, user_id, "ai_online_enabled", "1") != "0"
    providers = []
    for provider in AI_PROVIDER_DEFINITIONS:
        provider_id = str(provider["id"])
        saved_key = public_release.read_setting(query, user_id, _ai_setting_key(provider_id, "api_key"), "").strip()
        saved_model = public_release.read_setting(query, user_id, _ai_setting_key(provider_id, "model"), "").strip()
        env_configured = bool(os.getenv(str(provider["key_env"]), "").strip())
        if provider_id == "vercel_gateway":
            env_configured = env_configured or bool(os.getenv("VERCEL_OIDC_TOKEN", "").strip())
        if provider_id == "gemini":
            env_configured = env_configured or bool(os.getenv("GOOGLE_API_KEY", "").strip())
        providers.append({
            "id": provider_id,
            "label": provider["label"],
            "configured": bool(saved_key or env_configured),
            "account_configured": bool(saved_key),
            "environment_configured": env_configured,
            "model": saved_model or os.getenv(str(provider["model_env"]), str(provider["default_model"])),
            "default_model": provider["default_model"],
        })
    return {
        "enabled": enabled,
        "mode": mode,
        "max_providers": max_providers,
        "providers": providers,
    }


def make_challenge(user_id: int) -> str:
    payload = json.dumps(
        {"uid": int(user_id), "exp": int(time.time()) + 300, "nonce": secrets.token_hex(8)},
        separators=(",", ":"),
    ).encode("utf-8")
    signature = hmac.new(API_SECRET, payload, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(payload + signature).decode("ascii").rstrip("=")


def read_challenge(token: str) -> int:
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
        payload, signature = raw[:-32], raw[-32:]
        expected = hmac.new(API_SECRET, payload, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError("signature")
        data = json.loads(payload)
        if int(data["exp"]) < int(time.time()):
            raise ValueError("expired")
        return int(data["uid"])
    except Exception as error:
        raise HTTPException(status_code=401, detail="Код входа устарел. Войдите ещё раз.") from error


def bearer_token(authorization: str = Header(default="")) -> str:
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Требуется вход.")
    return token.strip()


def current_user(token: str = Depends(bearer_token)) -> int:
    ensure_runtime_schema()
    # Validate and occasionally touch the session against one database
    # snapshot. This avoids a read/write race between two Blob downloads on
    # serverless hosting and is also cheaper when Postgres is configured.
    with db.transaction() as (query, execute):
        user_id = public_release.restore_session(query, execute, token)
    if not user_id:
        raise HTTPException(status_code=401, detail="Сессия завершена. Войдите снова.")
    return int(user_id)


class RegisterRequest(BaseModel):
    email: str
    password: str = Field(min_length=8)
    privacy_accepted: bool


class LoginRequest(BaseModel):
    email: str
    password: str


class PasswordResetRequest(BaseModel):
    email: str


class PasswordResetConfirmRequest(BaseModel):
    token: str = Field(min_length=32, max_length=256)
    password: str = Field(min_length=8, max_length=256)


class AdminPasswordResetRequest(BaseModel):
    email: str
    password: str = Field(min_length=8, max_length=256)


class TotpRequest(BaseModel):
    challenge: str
    code: str


class AppearanceRequest(BaseModel):
    theme: Literal["system-light", "system-dark", "cyber-aurora"]
    font_scale: int = Field(ge=85, le=125, multiple_of=5)
    density: Literal["auto", "compact", "comfortable"]


class JobStatusRequest(BaseModel):
    # Status is deliberately separated from the personal "favourite" mark:
    # a vacancy can be both in work and starred.
    status: Literal["approved", "later", "skip", "ready", "sent", "in_progress", "done"] | None = None
    favorite: bool | None = None


class SearchRequest(BaseModel):
    force: bool = True
    use_ai: bool = True


class AiSettingsRequest(BaseModel):
    enabled: bool = True
    mode: Literal["auto", "ensemble", "vercel_gateway", "openai", "gemini", "anthropic", "groq", "openrouter"] = "ensemble"
    max_providers: int = Field(default=3, ge=1, le=5)
    keys: dict[str, str] = Field(default_factory=dict)
    models: dict[str, str] = Field(default_factory=dict)
    clear: list[str] = Field(default_factory=list)


class AiChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=3000)


class CandidateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    target_title: str = Field(default="", max_length=180)
    english_level: str = Field(default="", max_length=40)
    desired_countries: str = Field(default="", max_length=500)
    salary_min: int = Field(default=0, ge=0, le=1_000_000)
    notes: str = Field(default="", max_length=2000)
    hard_exclude: str = Field(default="", max_length=1000)
    hard_require: str = Field(default="", max_length=1000)
    preferred_regions: str = Field(default="", max_length=500)
    preferred_cities: str = Field(default="", max_length=500)
    preferred_companies: str = Field(default="", max_length=500)
    priority_titles: str = Field(default="", max_length=500)
    contact_email: str = Field(default="", max_length=320)
    cover_tone: Literal["formal", "friendly", ""] = ""
    cover_length: Literal["compact", "detailed", ""] = ""
    manual_review: int = Field(default=1, ge=0, le=1)
    skills: list[str] = Field(default_factory=list, max_length=60)


class ResumeRequest(BaseModel):
    title: str = Field(default="", max_length=180)
    language: Literal["RU", "EN", "SR", "OTHER"] = "EN"
    content: str = Field(min_length=20, max_length=30_000)


class ResumeTransformRequest(BaseModel):
    candidate_id: int | None = None
    content: str = Field(min_length=20, max_length=30_000)
    language: Literal["RU", "EN", "SR", "OTHER"] = "EN"


class ApplicationPrepareRequest(BaseModel):
    vacancy_ids: list[int] = Field(min_length=1, max_length=30)


class ApplicationPreferencesRequest(BaseModel):
    tone: Literal["formal", "friendly"] = "formal"
    length: Literal["compact", "detailed"] = "compact"
    include_certificates: bool = True
    include_achievements: bool = True
    from_email: str = Field(default="", max_length=320)


class ApplicationComposeRequest(BaseModel):
    vacancy_id: int
    tone: Literal["formal", "friendly"] | None = None
    length: Literal["compact", "detailed"] | None = None
    include_salary: bool = False


class GigApplicationComposeRequest(BaseModel):
    tone: Literal["formal", "friendly"] | None = None
    length: Literal["compact", "detailed"] | None = None


class ProCodeRequest(BaseModel):
    code: str = Field(min_length=4, max_length=128)


class CertificateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=180)
    issuer: str = Field(default="", max_length=180)
    credential_url: str = Field(default="", max_length=2048)
    issued_at: str = Field(default="", max_length=40)
    notes: str = Field(default="", max_length=1000)
    include_in_resume: int = Field(default=1, ge=0, le=1)


class SearchScheduleRequest(BaseModel):
    enabled: bool = False
    frequency: Literal["once", "twice"] = "once"


class TelegramBotConnectRequest(BaseModel):
    bot_token: str = Field(min_length=20, max_length=256)


class ManualVacancyRequest(BaseModel):
    candidate_id: int
    company: str = Field(min_length=1, max_length=180)
    position: str = Field(min_length=1, max_length=180)
    link: str = Field(default="", max_length=2048)
    location: str = Field(default="", max_length=500)
    source: str = Field(default="Ручная проверка", max_length=120)
    posted_at: str = Field(default="", max_length=40)
    salary_text: str = Field(default="", max_length=180)
    description: str = Field(default="", max_length=15000)


class ManualGigRequest(BaseModel):
    candidate_id: int
    title: str = Field(min_length=1, max_length=180)
    client: str = Field(default="", max_length=180)
    link: str = Field(default="", max_length=2048)
    category: str = Field(default="Проектная работа", max_length=120)
    work_format: str = Field(default="", max_length=120)
    location: str = Field(default="Дананг / удалённо из Вьетнама", max_length=500)
    pay_text: str = Field(default="", max_length=180)
    posted_at: str = Field(default="", max_length=40)
    description: str = Field(default="", max_length=15000)


class VacancyCleanupRequest(BaseModel):
    mode: Literal["inactive", "ignored", "all", "reset"] = "inactive"


class SerbiaPresetRequest(BaseModel):
    kind: Literal["qa_candidate", "support_candidate"]


class VietnamPresetRequest(BaseModel):
    kind: Literal["qa_candidate", "support_candidate"]


class SourceRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: str = Field(default="manual public link", max_length=120)
    region: str = Field(default="", max_length=180)
    url: str = Field(default="", max_length=2048)
    notes: str = Field(default="", max_length=1000)
    enabled: bool = True


class SourceUpdateRequest(BaseModel):
    enabled: bool | None = None
    notes: str | None = Field(default=None, max_length=1000)


TRACKER_RESULTS = (
    "Собеседование 1/1", "Собеседование 1/2", "Собеседование 2/2",
    "Собеседование 1/3", "Собеседование 2/3", "Собеседование 3/3",
    "Техническое собеседование 1/1", "Тестовое задание", "Игнор",
    "Самоотказ", "HR-собеседование",
)


class TrackerUpdateRequest(BaseModel):
    response_at: str | None = Field(default=None, max_length=20)
    result: str | None = Field(default=None, max_length=80)
    comments: str | None = Field(default=None, max_length=4000)
    salary_range: str | None = Field(default=None, max_length=300)


class GoogleSheetsSettingsRequest(BaseModel):
    spreadsheet_url: str = Field(default="", max_length=2048)
    webhook_url: str = Field(default="", max_length=2048)
    webhook_secret: str = Field(default="", max_length=512)


class PushKeys(BaseModel):
    p256dh: str = Field(min_length=20, max_length=512)
    auth: str = Field(min_length=8, max_length=256)


class PushSubscriptionRequest(BaseModel):
    endpoint: str = Field(min_length=16, max_length=2048)
    keys: PushKeys


@asynccontextmanager
async def lifespan(_: FastAPI):
    if os.getenv("CAREERMOVE_SCHEMA_ON_START", "1").strip().lower() not in {"0", "false", "no"}:
        db.ensure_schema()
    yield


app = FastAPI(
    title="CareerMove API",
    version="13.3.9",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)
origins = [
    item.strip()
    for item in os.getenv(
        "CORS_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173",
    ).split(",")
    if item.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.middleware("http")
async def keep_private_api_responses_fresh(request, call_next):
    """Never let a browser/PWA reuse another user's cached account state.

    Search results, profile settings and appearance are user-specific.  A
    cacheable `/api/me` response was the practical cause of an old theme and
    old location rules appearing again after a successful save in Safari.
    """
    if request.url.path.startswith("/api/") and request.url.path not in {"/api/docs", "/api/openapi.json"}:
        try:
            ensure_runtime_schema()
        except Exception as error:
            print(f"CareerMove schema bootstrap failed: {type(error).__name__}")
            return JSONResponse(
                {"detail": "Сервер запускает хранилище. Повторите через несколько секунд."},
                status_code=503,
                headers={"Cache-Control": "private, no-store, max-age=0", "Pragma": "no-cache"},
            )
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "private, no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"
    return response


@app.get("/health", response_model=None)
def health() -> Any:
    # Expose the matching revision too: the browser can tell that it is talking
    # to the updated search service rather than an older deployment.
    try:
        db.query("SELECT 1 AS ready")
    except Exception as error:
        print(f"CareerMove storage health check failed: {type(error).__name__}")
        return JSONResponse(
            {
                "status": "degraded",
                "service": "careermove-api",
                "version": "13.3.9",
                "storage": "unavailable",
            },
            status_code=503,
        )
    return {
        "status": "ok",
        "service": "careermove-api",
        "version": "13.3.9",
        "storage": "postgres" if db.is_postgres else "sqlite",
        "sources": str(len(live_jobs.SOURCE_SPECS)),
        "interactive_sources": str(len(live_jobs.FAST_SOURCE_NAMES)),
        "matching_version": live_jobs.MATCHING_VERSION,
        "search_launch_version": SEARCH_LAUNCH_VERSION,
        "auth_bootstrap_version": AUTH_BOOTSTRAP_VERSION,
    }


@app.get("/api/ai/status")
def ai_status(user_id: int = Depends(current_user)) -> dict[str, Any]:
    with temporary_environ(user_ai_runtime_env(db.query, user_id)):
        configs = live_jobs.open_model_configs()
    last_raw = public_release.read_setting(db.query, user_id, "live_jobs_last_ai_status", "")
    last_statuses: list[dict[str, Any]] = []
    if last_raw:
        try:
            decoded = json.loads(last_raw)
            if isinstance(decoded, list):
                last_statuses = [item for item in decoded if isinstance(item, dict)]
        except (TypeError, ValueError, json.JSONDecodeError):
            last_statuses = []
    status_code = "ready" if configs else "browser_search"
    title = "AI подключен" if configs else "Онлайн-поиск активен"
    detail = (
        "Vercel AI Gateway и локальный рейтинг активны; чат использует онлайн-модель, а поиск сохраняет только проверенные ссылки."
        if configs else
        "Генеративные модели не подключены; чат использует онлайн-поиск по публичным источникам CareerMove."
    )
    level = "ok"
    configured_provider_names = {str(item.get("provider") or "") for item in configs}
    for item in last_statuses:
        if not configs:
            break
        code = str(item.get("code") or "")
        # Do not let an old local-search status or a removed provider override
        # the currently configured Gateway in the real-time service badge.
        if str(item.get("provider") or "") not in configured_provider_names:
            continue
        if code in {"quota_exceeded", "rate_limited", "provider_unavailable", "request_rejected", "unavailable", "all_providers_unavailable"}:
            status_code = code
            title = str(item.get("title") or "AI provider issue")
            detail = str(item.get("detail") or "Последняя AI-проверка завершилась с ошибкой.")
            level = "error" if code in {"quota_exceeded", "all_providers_unavailable"} else "warning"
            break
        if code in {"completed", "partial", "disabled"}:
            status_code = code
            title = str(item.get("title") or title)
            detail = str(item.get("detail") or detail)
            level = "ok" if code == "completed" else "warning" if code == "partial" else "muted"
            break
    primary = configs[0] if configs else {}
    return {
        "api": "ok",
        "checked_at": now_iso(),
        "ai": status_code,
        "title": title,
        "detail": detail,
        "level": level,
        "provider": primary.get("provider", ""),
        "model": primary.get("model", ""),
        "providers": [{"provider": item["provider"], "model": item["model"]} for item in configs],
        "last_statuses": last_statuses[:5],
    }


@app.get("/api/ai/settings")
def get_ai_settings(user_id: int = Depends(current_user)) -> dict[str, Any]:
    return ai_settings_payload(user_id)


@app.put("/api/ai/settings")
def save_ai_settings(body: AiSettingsRequest, user_id: int = Depends(current_user)) -> dict[str, Any]:
    provider_ids = {str(provider["id"]) for provider in AI_PROVIDER_DEFINITIONS}
    public_release.put_setting(db.query, db.execute, user_id, "ai_online_enabled", "1" if body.enabled else "0")
    public_release.put_setting(db.query, db.execute, user_id, "ai_provider_mode", body.mode)
    public_release.put_setting(db.query, db.execute, user_id, "ai_max_providers", str(body.max_providers))
    for provider_id in provider_ids:
        if provider_id in body.clear:
            db.execute("DELETE FROM settings WHERE user_id=? AND key=?", (user_id, _ai_setting_key(provider_id, "api_key")))
            continue
        key = body.keys.get(provider_id)
        if isinstance(key, str) and key.strip():
            public_release.put_setting(db.query, db.execute, user_id, _ai_setting_key(provider_id, "api_key"), key.strip())
        model = body.models.get(provider_id)
        if isinstance(model, str) and model.strip():
            public_release.put_setting(db.query, db.execute, user_id, _ai_setting_key(provider_id, "model"), model.strip())
    return ai_settings_payload(user_id)


def local_ai_chat_answer(message: str) -> str:
    text = str(message or "").lower()
    lead = "Онлайн-поиск активен. Генеративные модели не подключены, поэтому отвечаю через правила CareerMove и публичные источники."
    if any(marker in text for marker in ("стаж", "intern", "trainee", "без опыта")):
        points = [
            "Открывайте раздел «Стажировка»: туда попадают junior/trainee-варианты, где допустим старт без опыта.",
            "Перед откликом проверьте три условия: обучение/наставник, техника или компенсация техники, понятный договор.",
            "Для Support-кандидата такие карточки можно рассматривать как основной вход в IT; для QA-кандидата — как запасной QA/support-старт.",
        ]
    elif any(marker in text for marker in ("совмещ", "part-time", "пол дня", "полдня", "moonlight")):
        points = [
            "Открывайте раздел «Совмещение»: туда попадают вакансии с явным part-time/flexible/contract/asynchronous сигналом.",
            "Если в вакансии нет прямого разрешения на неполный день, сервис не считает её подходящей для совмещения.",
            "Перед откликом уточняйте часы в день, таймзону, нагрузку и запрет на параллельные проекты.",
        ]
    elif any(marker in text for marker in ("резюме", "cv", "resume", "тк", "оформ")):
        points = [
            "В резюме и профиле закреплено ожидание: международная удалёнка из Вьетнама, без оформления по ТК РФ и без привязки к российскому юрлицу.",
            "Для международного remote проверяйте прозрачный legal/contractor contract, налоговый статус, выплаты, график и технику.",
            "Сопроводительное письмо оставляем коротким: опыт, релевантные навыки, готовность обсудить формат и следующий шаг.",
        ]
    else:
        points = [
            "Проверьте профиль: роль, минимальную оплату, страны, hard exclude и hard require.",
            "Запустите обновление: сервис сначала сохранит больше карточек локальным рейтингом, затем проверит их актуальность.",
            "Начинайте с золотых вакансий, но не игнорируйте 60-79%: там часто бывают хорошие remote/junior варианты.",
            "Перед откликом откройте оригинал вакансии и проверьте дату, контакты, договор, технику, график и язык команды.",
        ]
    return lead + "\n\n" + "\n".join(f"{index}. {point}" for index, point in enumerate(points, start=1))


def browser_search_chat_answer(message: str, user_id: int) -> str:
    text = str(message or "").lower()
    wants_resume = any(marker in text for marker in ("резюме", "cv", "resume", "сопровод", "письм")) and not any(
        marker in text for marker in ("вакан", "работ", "поиск", "стаж", "intern", "remote", "удал")
    )
    if wants_resume:
        return local_ai_chat_answer(message)
    wants_internship = any(marker in text for marker in ("стаж", "intern", "trainee", "без опыта", "junior"))
    wants_part_time = any(marker in text for marker in ("совмещ", "part-time", "part time", "пол дня", "полдня", "moonlight"))
    wanted_terms: list[str] = []
    term_groups = [
        (("qa", "тест", "tester", "testing", "quality"), ("qa", "test", "тест", "quality")),
        (("support", "поддерж", "help desk", "service desk", "саппорт"), ("support", "поддерж", "help desk", "service desk", "саппорт")),
        (("api", "postman"), ("api", "postman", "swagger", "openapi")),
        (("java",), ("java",)),
        (("swift", "ios"), ("swift", "ios")),
        (("remote", "удал", "worldwide"), ("remote", "удал", "worldwide", "anywhere")),
    ]
    for markers, terms in term_groups:
        if any(marker in text for marker in markers):
            wanted_terms.extend(terms)

    with db.transaction() as (query, execute):
        candidates_frame = query("SELECT id,name FROM candidates WHERE user_id=? ORDER BY id", (user_id,))
        if candidates_frame.empty:
            return "Сначала добавьте профиль кандидата, затем я смогу искать свежие вакансии по публичным источникам."
        candidates = [(int(row["id"]), str(row.get("name") or "Кандидат")) for _, row in candidates_frame.iterrows()]
        jobs, diagnostics = live_jobs.collect_live_jobs(
            query,
            execute,
            force=True,
            source_names=live_jobs.FAST_SOURCE_NAMES,
            max_wait_seconds=12,
        )
        results: list[dict[str, Any]] = []
        job_types = {"full-time", "contract", "part-time", "freelance", "internship"}
        if wants_part_time:
            job_types = {"part-time", "freelance", "contract"}
        for candidate_id, candidate_name in candidates:
            profile = live_jobs.candidate_profile(query, user_id, candidate_id)
            ranked = live_jobs.filter_and_score(
                jobs,
                profile,
                min_score=live_jobs.BROAD_REVIEW_SCORE,
                max_age_days=30,
                remote_only=False,
                job_types=job_types,
            )
            ranked = live_jobs.extend_with_review_reserve(
                jobs,
                profile,
                ranked,
                target=18,
                max_age_days=30,
                remote_only=False,
                job_types=job_types,
            )
            for job in ranked:
                blob = " ".join(str(job.get(key) or "") for key in ("title", "description", "tags", "job_type", "location", "source")).lower()
                if wants_internship and not re.search(r"стаж[её]р|стажиров|internship|intern\b|trainee|junior|джун|без опыта", blob, flags=re.I):
                    continue
                if wants_part_time and not live_jobs.moonlight_fit(job)[0]:
                    continue
                if wanted_terms and not any(term in blob for term in wanted_terms):
                    continue
                item = dict(job)
                item["candidate"] = candidate_name
                results.append(item)
    unique: dict[str, dict[str, Any]] = {}
    for item in sorted(
        results,
        key=lambda row: (
            int(row.get("score") or 0),
            live_jobs.parse_datetime(row.get("posted_at")) or live_jobs.parse_datetime(row.get("verified_at")) or datetime(1970, 1, 1, tzinfo=UTC),
        ),
        reverse=True,
    ):
        key = live_jobs.canonical_url(item.get("url")) or f"{item.get('source')}|{item.get('title')}|{item.get('company')}"
        if key and key not in unique:
            unique[key] = item
    top = list(unique.values())[:8]
    checked = sum(int(item.get("count") or 0) for item in diagnostics)
    updated_sources = sum(1 for item in diagnostics if str(item.get("status") or "") in {"updated", "cached", "stale"})
    header = (
        f"Онлайн-поиск по публичным источникам CareerMove: проверено/прочитано {checked} объявлений "
        f"из {updated_sources} источников. Показываю только свежие карточки до 30 дней."
    )
    if not top:
        return (
            header
            + "\n\nПо этому запросу сейчас не нашлось точных свежих совпадений. Попробуйте сузить вопрос: "
            "«QA remote», «support remote», «стажировка без опыта», «совмещение part-time»."
        )
    lines = [header, ""]
    for index, item in enumerate(top, start=1):
        posted = live_jobs.parse_datetime(item.get("posted_at")) or live_jobs.parse_datetime(item.get("verified_at"))
        age = ""
        if posted:
            days = max(0, (datetime.now(UTC) - posted).days)
            age = "сегодня" if days == 0 else f"{days} дн. назад"
        salary = live_jobs.clean_text(item.get("salary"), 160) or "оплата не указана"
        location = live_jobs.clean_text(item.get("location"), 180) or "локацию проверить"
        url = live_jobs.canonical_url(item.get("url"))
        lines.append(
            f"{index}. {live_jobs.clean_text(item.get('title'), 180)} — "
            f"{live_jobs.clean_text(item.get('company'), 140)} "
            f"({item.get('candidate')}, {int(item.get('score') or 0)}%). "
            f"{location}; {salary}; {age}.\n   {url}"
        )
    lines.extend([
        "",
        "Что сделать перед откликом: открыть оригинал, проверить дату, договор/оформление, географию remote, оплату, технику и язык команды.",
    ])
    return "\n".join(lines)


def ai_chat_provider_answer(config: dict[str, str], message: str) -> str:
    provider = config["provider"]
    model = config["model"]
    key = os.getenv(config["key_name"], "").strip()
    if config["id"] == "vercel_gateway":
        key = key or os.getenv("VERCEL_OIDC_TOKEN", "").strip()
    if config["id"] == "gemini":
        key = key or os.getenv("GOOGLE_API_KEY", "").strip()
    if not key:
        raise RuntimeError("AI key is missing")
    system = (
        "You are CareerMove AI, a concise Russian-language job search assistant for Demo QA and Demo Support. "
        "Help with remote international search, QA/support resumes, internships, part-time work, cover letters and vacancy checks. "
        "Never claim that an application was sent. Be specific, practical and brief."
    )
    headers = {"Content-Type": "application/json", "User-Agent": "CareerMove/1.0"}
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
                {"role": "user", "content": message},
            ],
            "temperature": 0.2,
        }
    elif config["protocol"] == "gemini":
        headers["x-goog-api-key"] = key
        payload = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": message}]}],
            "generationConfig": {"temperature": 0.2},
        }
    else:
        headers["x-api-key"] = key
        headers["anthropic-version"] = "2023-06-01"
        payload = {
            "model": model,
            "max_tokens": 1200,
            "temperature": 0.2,
            "system": system,
            "messages": [{"role": "user", "content": message}],
        }
    response = requests.post(endpoint, headers=headers, json=payload, timeout=35)
    if response.status_code >= 400:
        status = live_jobs._open_model_http_status(response, provider, model)
        raise RuntimeError(str(status.get("detail") or "AI provider rejected request"))
    data = response.json()
    if config["protocol"] == "openai":
        return str(data["choices"][0]["message"]["content"]).strip()
    if config["protocol"] == "gemini":
        return "".join(
            str(part.get("text") or "")
            for part in data["candidates"][0]["content"]["parts"]
            if isinstance(part, dict)
        ).strip()
    return "".join(
        str(block.get("text") or "")
        for block in data["content"]
        if isinstance(block, dict) and block.get("type") == "text"
    ).strip()


@app.post("/api/ai/chat")
def ai_chat(body: AiChatRequest, user_id: int = Depends(current_user)) -> dict[str, Any]:
    message = body.message.strip()
    with temporary_environ(user_ai_runtime_env(db.query, user_id)):
        configs = live_jobs.open_model_configs()
        statuses: list[dict[str, Any]] = []
        for config in configs:
            provider = config["provider"]
            model = config["model"]
            try:
                answer = ai_chat_provider_answer(config, message)
                status_item = live_jobs._ai_status(
                    "completed",
                    f"{provider} ответил в AI-чате",
                    f"{provider} · {model} доступен для карьерных подсказок.",
                    provider=provider,
                    model=model,
                    level="success",
                )
                public_release.put_setting(
                    db.query, db.execute, user_id, "live_jobs_last_ai_status",
                    json.dumps([status_item], ensure_ascii=False),
                )
                return {
                    "answer": answer or local_ai_chat_answer(message),
                    "mode": "online",
                    "provider": provider,
                    "model": model,
                    "status": status_item,
                }
            except (requests.RequestException, KeyError, TypeError, ValueError, RuntimeError):
                statuses.append(live_jobs._ai_status(
                    "unavailable",
                    f"{provider} не ответил",
                    "CareerMove переключился на следующую модель; локальная подсказка остаётся доступной.",
                    provider=provider,
                    model=model,
                    level="warning",
                    retryable=True,
                ))
    if statuses:
        public_release.put_setting(
            db.query, db.execute, user_id, "live_jobs_last_ai_status",
            json.dumps(statuses[:5], ensure_ascii=False),
        )
    search_status = live_jobs._ai_status(
        "completed",
        "Онлайн-поиск активен",
        "Генеративные модели не подключены или недоступны; чат ищет по публичным источникам CareerMove.",
        provider="CareerMove Search",
        model="public-sources",
        level="success",
    )
    try:
        answer = browser_search_chat_answer(message, user_id)
        public_release.put_setting(
            db.query, db.execute, user_id, "live_jobs_last_ai_status",
            json.dumps([search_status], ensure_ascii=False),
        )
        return {
            "answer": answer,
            "mode": "online",
            "provider": "CareerMove Search",
            "model": "public-sources",
            "status": search_status,
        }
    except Exception:
        pass
    return {
        "answer": local_ai_chat_answer(message),
        "mode": "local",
        "provider": "",
        "model": "",
        "status": statuses[0] if statuses else live_jobs._ai_status(
            "browser_search_unavailable",
            "Онлайн-поиск временно недоступен",
            "CareerMove не смог быстро прочитать публичные источники; базовая подсказка остаётся доступной.",
            level="info",
        ),
    }


@app.post("/api/auth/register", status_code=status.HTTP_201_CREATED)
def register(body: RegisterRequest) -> dict[str, Any]:
    if not body.privacy_accepted:
        raise HTTPException(status_code=400, detail="Подтвердите Privacy Policy и Terms.")
    with db.transaction() as (query, execute):
        ok, message, user_id = public_release.create_account(
            query, execute, body.email, body.password, SOURCE_CATALOG,
        )
        if not ok or not user_id:
            raise HTTPException(status_code=400, detail=message)
        token = public_release.issue_session(execute, int(user_id))
        user = user_payload(int(user_id), query=query)
    return {"token": token, "user": user, "message": message}


@app.post("/api/auth/login")
def login(body: LoginRequest) -> dict[str, Any]:
    with db.transaction() as (query, execute):
        result = public_release.authenticate(query, execute, body.email, body.password)
        if not result.get("ok"):
            raise HTTPException(status_code=401, detail=str(result.get("message") or "Ошибка входа."))
        user_id = int(result["user_id"])
        if result.get("requires_totp"):
            return {"requires_totp": True, "challenge": make_challenge(user_id)}
        token = public_release.issue_session(execute, user_id)
        user = user_payload(user_id, query=query)
    return {"token": token, "user": user}


@app.post("/api/auth/password/forgot", status_code=status.HTTP_202_ACCEPTED)
def forgot_password(body: PasswordResetRequest) -> dict[str, Any]:
    reset = public_release.issue_password_reset(db.query, db.execute, body.email)
    payload = {
        "message": (
            "Если аккаунт с таким email существует, мы отправили одноразовую ссылку. "
            "Проверьте также папку «Спам»."
        )
    }
    if reset:
        to_email, token = reset
        # Never generate localhost links for a production reset email when the
        # optional APP_URL variable is missing.
        # The public client is served from Vercel.  Keeping this default here
        # prevents password-reset emails from pointing to the retired Pages
        # deployment when an environment variable has not been configured yet.
        app_url = os.getenv("APP_URL", "http://localhost:5173").strip().rstrip("/")
        reset_url = f"{app_url}/?reset={token}"
        delivery_ok, delivery_detail = public_release.send_password_reset_email(to_email, reset_url)
        owner_emails = {
            item.strip().lower()
            for item in os.getenv("ADMIN_EMAILS", public_release.OWNER_EMAIL).split(",")
            if item.strip()
        }
        owner_emails.add(public_release.OWNER_EMAIL)
        owner_emails.add("owner@example.com")
        # Personal-production fallback: if SMTP/Resend is not configured, the
        # owner still needs a working recovery path.  Only owner emails receive
        # the one-time link in the response; other addresses keep the generic
        # non-enumerating message.
        if str(to_email).strip().lower() in owner_emails:
            payload["reset_url"] = reset_url
            payload["delivery"] = "sent" if delivery_ok else "manual_link"
            payload["delivery_detail"] = delivery_detail
            payload["message"] = (
                "Письмо для восстановления отправлено. Если оно задержится, ссылка ниже тоже работает 30 минут."
                if delivery_ok
                else f"Почта пока не отправилась: {delivery_detail}. Ссылка ниже работает 30 минут."
            )
    return payload


@app.post("/api/auth/password/reset")
def confirm_password_reset(body: PasswordResetConfirmRequest) -> dict[str, str]:
    ok, message = public_release.reset_password(db.query, db.execute, body.token, body.password)
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    return {"message": message}


@app.post("/api/auth/admin-reset")
def admin_password_reset(
    body: AdminPasswordResetRequest,
    x_admin_reset_token: str = Header(default=""),
) -> dict[str, str]:
    expected = os.getenv("CAREERMOVE_ADMIN_RESET_TOKEN", "").strip()
    if not expected or not hmac.compare_digest(x_admin_reset_token.strip(), expected):
        raise HTTPException(status_code=404, detail="Not found")

    clean_email = body.email.strip().lower()
    if not public_release.valid_email(clean_email):
        raise HTTPException(status_code=400, detail="Проверьте формат email.")

    now = datetime.now(UTC).replace(microsecond=0).isoformat()
    with db.transaction() as (query, execute):
        user_frame = query("SELECT id FROM users WHERE lower(email)=lower(?)", (clean_email,))
        if user_frame.empty:
            execute(
                "INSERT INTO users(email,password_hash,password_version,created_at) VALUES(?,?,?,?)",
                (clean_email, public_release.password_hash(body.password), "pbkdf2_sha256", now),
            )
            created = query("SELECT id FROM users WHERE lower(email)=lower(?)", (clean_email,))
            if created.empty:
                raise HTTPException(status_code=500, detail="Аккаунт не был создан.")
            user_id = int(created.iloc[0]["id"])
            try:
                public_release.seed_public_account(query, execute, user_id, clean_email, SOURCE_CATALOG)
            except Exception:
                pass
            action = "created"
        else:
            user_id = int(user_frame.iloc[0]["id"])
            execute(
                "UPDATE users SET password_hash=?,password_version=? WHERE id=?",
                (public_release.password_hash(body.password), "pbkdf2_sha256", user_id),
            )
            action = "updated"

        execute("UPDATE auth_sessions SET revoked=1 WHERE user_id=?", (user_id,))
        execute(
            "INSERT INTO security_events(user_id,event_type,detail,created_at) VALUES(?,?,?,?)",
            (user_id, "admin_password_reset", "Password reset through protected owner endpoint", now),
        )

    return {"message": "Пароль обновлён.", "status": action}


@app.post("/api/auth/totp")
def confirm_totp(body: TotpRequest) -> dict[str, Any]:
    user_id = read_challenge(body.challenge)
    frame = db.query("SELECT totp_secret,totp_enabled FROM users WHERE id=?", (user_id,))
    if frame.empty or not bool(frame.iloc[0].get("totp_enabled") or 0):
        raise HTTPException(status_code=401, detail="Двухфакторная защита не настроена.")
    if not public_release.verify_totp(str(frame.iloc[0]["totp_secret"] or ""), body.code):
        raise HTTPException(status_code=401, detail="Неверный или устаревший код.")
    token = public_release.issue_session(db.execute, user_id)
    return {"token": token, "user": user_payload(user_id)}


@app.post("/api/auth/logout")
def logout(token: str = Depends(bearer_token)) -> dict[str, bool]:
    public_release.revoke_session(db.execute, token)
    return {"ok": True}


def appearance_payload(user_id: int, *, query=None) -> dict[str, Any]:
    read = query or db.query
    theme = public_release.read_setting(read, user_id, "ui_theme", "system-light")
    density = public_release.read_setting(read, user_id, "layout_density", "auto")
    try:
        scale = int(public_release.read_setting(read, user_id, "font_scale", "100"))
    except ValueError:
        scale = 100
    return {
        "theme": theme if theme in public_release.THEMES else "system-light",
        "font_scale": max(85, min(scale, 125)),
        "density": density if density in public_release.LAYOUT_MODES else "auto",
    }


def user_payload(user_id: int, *, query=None) -> dict[str, Any]:
    read = query or db.query
    frame = read("SELECT id,email FROM users WHERE id=?", (user_id,))
    if frame.empty:
        raise HTTPException(status_code=404, detail="Пользователь не найден.")
    row = frame.iloc[0]
    return {
        "id": int(row["id"]),
        "email": str(row["email"]),
        "appearance": appearance_payload(user_id, query=read),
    }


@app.get("/api/me")
def me(user_id: int = Depends(current_user)) -> dict[str, Any]:
    return user_payload(user_id)


@app.patch("/api/settings/appearance")
def update_appearance(
    body: AppearanceRequest,
    user_id: int = Depends(current_user),
) -> dict[str, Any]:
    # Persist all three appearance controls atomically.  Otherwise a free
    # serverless request can be interrupted between the three Blob snapshots,
    # which looks like a saved theme has reverted after a refresh.
    with db.transaction() as (query, execute):
        public_release.put_setting(query, execute, user_id, "ui_theme", body.theme)
        public_release.put_setting(query, execute, user_id, "font_scale", body.font_scale)
        public_release.put_setting(query, execute, user_id, "layout_density", body.density)
        return appearance_payload(user_id, query=query)


@app.get("/api/push/config")
def push_config(user_id: int = Depends(current_user)) -> dict[str, Any]:
    public_key = os.getenv("VAPID_PUBLIC_KEY", "").strip()
    return {"enabled": bool(public_key), "public_key": public_key}


@app.post("/api/push/subscribe")
def subscribe_push(
    body: PushSubscriptionRequest,
    user_id: int = Depends(current_user),
) -> dict[str, bool]:
    endpoint = body.endpoint.strip()
    if not endpoint.startswith("https://"):
        raise HTTPException(status_code=400, detail="Push endpoint должен использовать HTTPS.")
    created = now_iso()
    db.execute(
        """
        INSERT INTO push_subscriptions(user_id,endpoint,p256dh,auth,enabled,created_at,last_used_at)
        VALUES(?,?,?,?,?,?,?)
        ON CONFLICT(endpoint) DO UPDATE SET
          user_id=excluded.user_id,p256dh=excluded.p256dh,auth=excluded.auth,
          enabled=1,last_used_at=excluded.last_used_at
        """,
        (user_id, endpoint, body.keys.p256dh, body.keys.auth, 1, created, created),
    )
    return {"ok": True}


@app.delete("/api/push/subscribe")
def unsubscribe_push(
    body: PushSubscriptionRequest,
    user_id: int = Depends(current_user),
) -> dict[str, bool]:
    db.execute(
        "UPDATE push_subscriptions SET enabled=0,last_used_at=? WHERE user_id=? AND endpoint=?",
        (now_iso(), user_id, body.endpoint.strip()),
    )
    return {"ok": True}


def fallback_jobs(user_id: int, limit: int = 80) -> list[dict[str, Any]]:
    frame = db.query(
        """
        SELECT v.id,v.candidate_id,c.name candidate,v.company,v.position,v.source,v.link,
          v.remote_location,v.salary_text,v.score,v.status,v.company_rating,v.strengths,
          v.weaknesses,v.positioning,v.recommendation,v.risk,v.posted_at,
          v.employer_email,v.employer_contact,v.final_salary_advice,v.cover_letter,v.ai_analysis,
          v.source_snapshot,COALESCE(v.favorite,0) favorite,
          c.target_title,c.hard_exclude,c.english_level,c.notes,c.salary_min
        FROM vacancies v JOIN candidates c ON c.id=v.candidate_id
        WHERE v.user_id=?
        ORDER BY v.score DESC,v.id DESC LIMIT ?
        """,
        (user_id, limit),
    )
    if frame.empty:
        return []
    rows = frame.to_dict("records")
    for row in rows:
        try:
            snapshot = json.loads(str(row.get("source_snapshot") or "{}"))
        except (TypeError, json.JSONDecodeError):
            snapshot = {}
        row["links"] = snapshot.get("links") or (
            [{"url": row.get("link"), "source": row.get("source"), "posted_at": row.get("posted_at")}]
            if row.get("link") else []
        )
        row["verified_at"] = snapshot.get("verified_at")
        location = str(row.get("remote_location") or "")
        raw_job = {
            "title": row.get("position"), "company": row.get("company"),
            "description": snapshot.get("description") or "", "tags": snapshot.get("tags") or "",
            "location": location, "source": row.get("source"), "url": row.get("link"),
            "remote": bool(re.search(r"\bremote\b|удал[её]н|из\s+дома|anywhere|worldwide", location, re.IGNORECASE)),
        }
        profile = {
            "target_title": row.get("target_title") or "", "hard_exclude": row.get("hard_exclude") or "",
            "english_level": row.get("english_level") or "", "notes": row.get("notes") or "",
            "salary_min": row.get("salary_min") or 0, "allow_vietnam_hybrid": True, "base_country": "Vietnam",
        }
        # Fallback must apply exactly the same hard filter as the live index.
        # Otherwise a transient index error could expose director/senior roles to
        # a junior candidate, which is worse than returning fewer cards.
        if live_jobs.hard_block(raw_job, profile):
            continue
        presentation = live_jobs.vacancy_presentation(raw_job)
        row["contacts"] = presentation["contacts"]
        row["equipment"] = presentation["equipment"]
        row["benefits"] = presentation["benefits"]
        row["schedule"] = presentation["schedule"]
        row["sector"] = presentation["sector"]
        row["company_rating_verified"] = False
        row["company_rating_note"] = "Рейтинг компании не проверен — проверьте отзывы, юрлицо и условия самостоятельно."
        if not int(row.get("company_rating") or 0):
            # Database default zero means that a public rating was not found.
            row["company_rating"] = None
        row.pop("source_snapshot", None)
        for field in ("target_title", "hard_exclude", "english_level", "notes", "salary_min"):
            row.pop(field, None)
    return rows


def ensure_catalog_sources(user_id: int, *, query=None, execute=None) -> None:
    """Backfill new public feeds without overwriting a user's own source list."""
    read = query or db.query
    write = execute or db.execute
    existing = read("SELECT id,service,url FROM job_sources WHERE user_id=?", (user_id,))
    keys = {
        str(row.get("service") or "").strip().lower()
        for row in ([] if existing.empty else existing.to_dict("records"))
    }
    # This was one aggregate feed in older accounts.  It is replaced by six
    # separately visible Telegram feeds; keep the row as history, but do not
    # count a non-fetchable aggregate as an active source.
    if "telegram abroad" in keys:
        write(
            "UPDATE job_sources SET enabled=0,notes=? WHERE user_id=? AND lower(service)=?",
            ("Заменён шестью отдельными публичными Telegram-каналами. История не удалена.", user_id, "telegram abroad"),
        )
    for service, source_type, region, url, notes in (*SOURCE_CATALOG, *REFERENCE_SOURCE_CATALOG):
        if service.strip().lower() not in keys:
            write(
                "INSERT INTO job_sources(user_id,service,source_type,region,url,enabled,notes) VALUES(?,?,?,?,?,?,?)",
                # Only sources with a documented public feed are enabled for
                # autonomous collection. Directory links remain visible and
                # useful, but cannot quietly turn into unreliable scraping.
                (user_id, service, source_type, region, url, 1 if source_type == "live feed" else 0, notes),
            )


def source_rows_with_health(user_id: int, *, query=None) -> list[dict[str, Any]]:
    read = query or db.query
    rows = read(
        "SELECT id,service AS name,source_type AS kind,region,url,enabled,notes FROM job_sources WHERE user_id=? ORDER BY enabled DESC,service",
        (user_id,),
    )
    values = [] if rows.empty else rows.to_dict("records")
    cache = read("SELECT source,fetched_at,payload,error FROM live_source_cache")
    health: dict[str, dict[str, Any]] = {}
    for item in ([] if cache.empty else cache.to_dict("records")):
        payload = []
        try:
            decoded = json.loads(str(item.get("payload") or "[]"))
            payload = decoded if isinstance(decoded, list) else []
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
        health[str(item.get("source") or "").strip().lower()] = {
            "last_checked_at": item.get("fetched_at") or "",
            "jobs_found": len(payload),
            "detail": str(item.get("error") or "").strip(),
        }
    for item in values:
        item["enabled"] = int(item.get("enabled") or 0)
        state = health.get(str(item.get("name") or "").strip().lower())
        if state:
            item.update(state)
            item["status"] = "error" if state["detail"] else "checked"
        else:
            item["last_checked_at"] = ""
            item["jobs_found"] = 0
            item["detail"] = "Ожидает первой проверки" if item["enabled"] else "Источник на паузе"
            item["status"] = "pending" if item["enabled"] else "paused"
    return json_safe(values)


def migrate_legacy_serbia_strategy(user_id: int, query, execute) -> bool:
    """Move only the obsolete Serbia preset to the current Vietnam strategy.

    Earlier public builds saved Belgrade/Novi Sad as a hard requirement and
    literally put Vietnam into the stop-list.  That combination makes the
    current collector correctly reject every Vietnam card.  The migration is
    deliberately narrow: it only runs when *both* legacy markers are present,
    and changes search preferences only — never resumes, certificates, mail,
    applications or other personal data.
    """
    frame = query(
        """SELECT id,name,desired_countries,hard_exclude,hard_require,notes,
                  preferred_regions,preferred_cities
           FROM candidates WHERE user_id=?""",
        (user_id,),
    )
    changed = False
    for _, row in frame.iterrows():
        desired = str(row.get("desired_countries") or "")
        excluded = str(row.get("hard_exclude") or "")
        legacy_serbia = any(marker in desired.lower() for marker in ("белград", "belgrade", "нови-сад", "novi sad"))
        vietnam_blocked = "вьетнам" in excluded.lower() or "vietnam" in excluded.lower()
        if not (legacy_serbia and vietnam_blocked):
            continue
        name = str(row.get("name") or "").lower()
        junior = "алекс" in name or "alex" in name
        clean_excluded = re.sub(r"(?:^|[;,\n]\s*)(?:вьетнам|vietnam)(?=\s*(?:[;,\n]|$))", "", excluded, flags=re.IGNORECASE)
        clean_excluded = re.sub(r"[;,\n]{2,}", "; ", clean_excluded).strip(" ;,\n")
        if not clean_excluded:
            clean_excluded = "Russia-only; офис или гибрид в любом городе РФ; оформление по ТК РФ; российское юрлицо; Sber; Senior; Lead; Head; Director; обязательный B2 English"
        notes = (
            "Переезжаю в Дананг 1 сентября, документы для легального оформления во Вьетнаме в процессе. "
            "Английский A1; рассматриваю remote, hybrid или office во Вьетнаме. "
            "Для русскоязычной вакансии уточнить возможность onboarding/daily на русском."
        )
        if junior:
            notes = (
                "Переезжаю в Дананг 1 сентября, документы для легального оформления во Вьетнаме в процессе. "
                "Ищу первую IT-роль: junior support/help desk/manual QA/trainee; нужен onboarding. "
                "Английский A1; рассматриваю remote, hybrid или office во Вьетнаме. "
                "Для русскоязычной вакансии уточнить возможность daily на русском."
            )
        execute(
            """UPDATE candidates SET desired_countries=?,hard_exclude=?,hard_require=?,notes=?,
                 preferred_regions=?,preferred_cities=? WHERE id=? AND user_id=?""",
            (
                "Вьетнам: Дананг; удалённо из Вьетнама; hybrid/office во Вьетнаме; международные вакансии, доступные из Вьетнама",
                clean_excluded,
                "Вьетнам или remote worldwide, доступно резиденту Вьетнама; без оформления по ТК РФ и без российского юрлица; прозрачный международный legal/contractor contract; проверить work permit, налоги, график, технику и компенсацию",
                notes,
                "Вьетнам; Юго-Восточная Азия; Казахстан; remote worldwide",
                "Дананг; Da Nang; Ханой; Хошимин",
                int(row["id"]), user_id,
            ),
        )
        changed = True
    if changed:
        public_release.put_setting(query, execute, user_id, "search_base_country", "Vietnam")
        public_release.put_setting(query, execute, user_id, "search_vietnam_hybrid", "1")
        public_release.put_setting(query, execute, user_id, "search_serbia_hybrid", "0")
        public_release.put_setting(query, execute, user_id, "search_stop_countries", "Россия; РФ")
    return changed


def seed_local_demo_profiles(user_id: int, query, execute) -> bool:
    """Create safe local-only demo profiles so the dev search can run at once."""
    if os.getenv("CAREERMOVE_LOCAL_DEMO", "").strip().lower() not in {"1", "true", "yes"}:
        return False
    existing = query("SELECT id FROM candidates WHERE user_id=? LIMIT 1", (user_id,))
    if not existing.empty:
        return False
    now = now_iso()
    profiles = [
        {
            "name": "Demo QA Candidate",
            "target_title": "Manual QA / API QA / Product Support",
            "salary_min": 1000,
            "notes": (
                "QA/support специалист с банковским доменным опытом, Postman/API, SQL, регрессией, документацией "
                "и клиентской поддержкой. Работа из Дананга, Вьетнам; русскоязычный onboarding/коммуникация предпочтительны."
            ),
            "hard_exclude": "Russia-only; офис или гибрид в любом городе РФ; оформление по ТК РФ; российское юрлицо; Sber; Сбер; Senior-only; Lead; Head; Director; обязательный B2 English; C1 English",
            "hard_require": "русскоязычная коммуникация или onboarding; Вьетнам или remote worldwide, доступно из Вьетнама; без ТК РФ и российского юрлица; прозрачный международный legal/contractor contract; указаны формат, график, оплата и контакт",
            "skills": ["Manual QA", "API testing", "Postman", "SQL", "Regression testing", "Mobile testing", "Bug reports", "User support"],
            "resume": (
                "QA and user-support specialist with banking/fintech experience, API testing, Postman, SQL, "
                "regression testing, mobile/web checks, bug reports, requirements analysis and release validation."
            ),
        },
        {
            "name": "Demo Support Candidate",
            "target_title": "Junior IT Support / Help Desk / QA Trainee",
            "salary_min": 700,
            "notes": (
                "Entry-level IT support/help desk/QA trainee кандидат: troubleshooting, пользовательская поддержка, "
                "документация, базовые QA-проверки. Нужен onboarding; работа из Дананга, Вьетнам."
            ),
            "hard_exclude": "Russia-only; офис или гибрид в любом городе РФ; оформление по ТК РФ; российское юрлицо; Sber; Сбер; Senior; Lead; Head; Director; Manager; обязательный B2 English; C1 English",
            "hard_require": "junior/trainee/support; русскоязычная коммуникация или onboarding; Вьетнам или remote worldwide, доступно из Вьетнама; без ТК РФ и российского юрлица; прозрачный международный legal/contractor contract; указаны формат, график, оплата и контакт",
            "skills": ["IT support", "Help desk", "Technical Support", "Manual QA basics", "Bug reports", "Documentation", "Basic SQL"],
            "resume": (
                "Entry-level IT support and QA trainee profile: help desk, user support, troubleshooting, bug reports, "
                "basic SQL, manual checks, documentation and careful communication."
            ),
        },
    ]
    for profile in profiles:
        execute(
            """
            INSERT INTO candidates(
              user_id,name,emoji,target_title,english_level,desired_countries,salary_min,
              notes,hard_exclude,hard_require,preferred_regions,preferred_cities,manual_review,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                user_id, profile["name"], "👤", profile["target_title"], "A1",
                "Вьетнам: Дананг; удалённо из Вьетнама; remote worldwide; hybrid/office во Вьетнаме",
                profile["salary_min"], profile["notes"], profile["hard_exclude"], profile["hard_require"],
                "Вьетнам; Юго-Восточная Азия; Казахстан; remote worldwide",
                "Дананг; Da Nang; Ханой; Хошимин",
                1, now,
            ),
        )
        created = query(
            "SELECT id FROM candidates WHERE user_id=? AND name=? ORDER BY id DESC LIMIT 1",
            (user_id, profile["name"]),
        )
        if created.empty:
            continue
        candidate_id = int(created.iloc[0]["id"])
        for skill in profile["skills"]:
            execute(
                "INSERT INTO skills(user_id,candidate_id,skill) VALUES(?,?,?)",
                (user_id, candidate_id, skill),
            )
        execute(
            "INSERT INTO resumes(user_id,candidate_id,language,title,content,updated_at) VALUES(?,?,?,?,?,?)",
            (user_id, candidate_id, "EN", f"{profile['name']} CV EN", profile["resume"], now),
        )
    for key, value in {
        "search_base_country": "Vietnam",
        "search_remote": "1",
        "search_vietnam_hybrid": "1",
        "search_serbia_hybrid": "0",
        "search_salary_min": "1100",
        "search_max_age_days": "30",
        "onboarding_completed": "1",
    }.items():
        public_release.put_setting(query, execute, user_id, key, value)
    return True


def dashboard_payload(user_id: int, query, execute) -> dict[str, Any]:
    """Read a complete cabinet from one SQLite snapshot.

    On Vercel the private SQLite file lives in Blob storage.  The generic
    helpers deliberately refresh that snapshot per call, which is safe for a
    mutation but made the previous dashboard perform dozens of GET/PUT calls.
    Keeping this read plus the source-catalog backfill in one transaction
    avoids the serverless 60-second timeout and makes a just-finished search
    visible immediately.
    """
    migrated_strategy = migrate_legacy_serbia_strategy(user_id, query, execute)
    ensure_catalog_sources(user_id, query=query, execute=execute)
    seeded_demo = seed_local_demo_profiles(user_id, query, execute)
    refreshed_resume_pack = ensure_qa_resume_pack(query, execute, user_id)
    candidates_frame = query(
        """
        SELECT c.id,c.name,c.target_title,c.english_level,c.desired_countries,c.salary_min,
          c.notes,c.hard_exclude,c.hard_require,c.preferred_regions,c.preferred_cities,
          c.preferred_companies,c.priority_titles,c.contact_email,c.cover_tone,c.cover_length,
          COALESCE(c.manual_review,1) manual_review,
          COUNT(DISTINCT r.id) resume_count
        FROM candidates c LEFT JOIN resumes r ON r.candidate_id=c.id AND r.user_id=c.user_id
        WHERE c.user_id=?
        GROUP BY c.id,c.name,c.target_title,c.english_level,c.desired_countries,c.salary_min,
          c.notes,c.hard_exclude,c.hard_require,c.preferred_regions,c.preferred_cities,
          c.preferred_companies,c.priority_titles,c.contact_email,c.cover_tone,c.cover_length,c.manual_review
        ORDER BY c.id
        """,
        (user_id,),
    )
    candidates = [] if candidates_frame.empty else candidates_frame.to_dict("records")
    for candidate in candidates:
        resume_frame = query(
            "SELECT id,title,language,content,photo_data,updated_at FROM resumes WHERE user_id=? AND candidate_id=? ORDER BY id DESC",
            (user_id, int(candidate["id"])),
        )
        resumes = [] if resume_frame.empty else resume_frame.to_dict("records")
        candidate["resumes"] = resumes
        candidate["photo_data"] = next((str(item.get("photo_data") or "") for item in resumes if item.get("photo_data")), "")
        skills_frame = query("SELECT skill FROM skills WHERE user_id=? AND candidate_id=? ORDER BY skill", (user_id, int(candidate["id"])))
        candidate["skills"] = [] if skills_frame.empty else [str(value) for value in skills_frame["skill"].tolist()]
        certificate_frame = query(
            "SELECT id,title,issuer,credential_url,issued_at,notes,include_in_resume FROM certificates WHERE user_id=? AND candidate_id=? ORDER BY id DESC",
            (user_id, int(candidate["id"])),
        )
        candidate["certificates"] = [] if certificate_frame.empty else certificate_frame.to_dict("records")
    candidate_ids = [int(item["id"]) for item in candidates]
    try:
        jobs = live_jobs.latest_jobs(query, user_id, candidate_ids, limit=140)
    except Exception:
        # A temporary index problem must not make the whole cabinet fail.  The
        # next search will rebuild it; old cards remain in the archive.
        jobs = []
    # Keep the review queue usable: at most 50 distinct opportunities, while
    # retaining both candidate assessments when one vacancy fits both people.
    selected_job_keys: set[str] = set()
    bounded_jobs: list[dict[str, Any]] = []
    for item in jobs:
        key = str(item.get("link") or f"{item.get('company')}|{item.get('position')}|{item.get('source')}").lower()
        if key not in selected_job_keys and len(selected_job_keys) >= 50:
            continue
        selected_job_keys.add(key)
        bounded_jobs.append(item)
    jobs = bounded_jobs
    application_frame = query(
        """
        SELECT id,company,position,status,created_at,link,cover_letter,recipient_email,subject,resume_id
        FROM applications WHERE user_id=? ORDER BY id DESC LIMIT 20
        """,
        (user_id,),
    )
    applications = [] if application_frame.empty else application_frame.to_dict("records")
    gigs_frame = query(
        """
        SELECT g.id,g.candidate_id,c.name candidate,g.title,g.client,g.source,g.link,g.location,
          g.category,g.work_format,g.pay_text,g.score,g.status,g.favorite,g.posted_at,
          g.active_checked_at,g.is_active,g.description,g.contacts_json,g.safety_note,g.requirements_note,g.source_snapshot,
          c.english_level
        FROM side_gigs g JOIN candidates c ON c.id=g.candidate_id
        WHERE g.user_id=? AND COALESCE(g.is_active,1)=1
        ORDER BY g.favorite DESC,g.score DESC,g.id DESC LIMIT 100
        """,
        (user_id,),
    )
    gigs = [] if gigs_frame.empty else gigs_frame.to_dict("records")
    filtered_gigs = []
    for gig in gigs:
        posted = live_jobs.parse_datetime(gig.get("posted_at"))
        checked = live_jobs.parse_datetime(gig.get("active_checked_at"))
        generated = str(gig.get("source") or "") != "Добавлено вручную"
        if (
            (posted and datetime.now(timezone.utc) - posted > timedelta(days=30))
            or (generated and not posted and checked and datetime.now(timezone.utc) - checked > timedelta(days=30))
        ):
            execute("UPDATE side_gigs SET is_active=0 WHERE id=? AND user_id=?", (int(gig["id"]), user_id))
            continue
        gig_text = " ".join(str(gig.get(key) or "") for key in ("title", "description", "category", "work_format", "location"))
        if gig_english_blocked(gig_text, str(gig.get("english_level") or "")):
            continue
        if str(gig.get("category") or "").lower() == "стажировка" and not internship_without_experience(gig):
            execute("UPDATE side_gigs SET is_active=0 WHERE id=? AND user_id=?", (int(gig["id"]), user_id))
            continue
        try:
            gig["contacts"] = json.loads(str(gig.pop("contacts_json") or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            gig["contacts"] = {"emails": [], "phones": [], "telegram": []}
        try:
            snapshot = json.loads(str(gig.pop("source_snapshot") or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            snapshot = {}
        gig["links"] = snapshot.get("links") or ([{"url": gig.get("link"), "source": gig.get("source")} ] if gig.get("link") else [])
        gig.pop("english_level", None)
        filtered_gigs.append(gig)
    gigs = filtered_gigs
    internships = [
        item for item in gigs
        if str(item.get("category") or "").lower() == "стажировка"
    ]
    gigs = [
        item for item in gigs
        if str(item.get("category") or "").lower() != "стажировка"
    ]
    sources_list = source_rows_with_health(user_id, query=query)
    latest_run = query(
        """
        SELECT run_id,status,stage,detail,result_json,error,created_at,updated_at
        FROM search_runs_v2 WHERE user_id=? ORDER BY created_at DESC LIMIT 1
        """,
        (user_id,),
    )
    latest_search = None if latest_run.empty else latest_run.iloc[0].to_dict()
    if latest_search and latest_search.get("result_json"):
        try:
            latest_search["result"] = json.loads(str(latest_search["result_json"]))
        except json.JSONDecodeError:
            latest_search["result"] = None
    if latest_search:
        latest_search.pop("result_json", None)
    sent_count = sum(1 for item in applications if str(item.get("status")) == "sent")
    unique_job_keys = {
        str(item.get("link") or f"{item.get('company')}|{item.get('position')}|{item.get('source')}").lower()
        for item in jobs
    }
    response = {
        "metrics": {
            "found": len(unique_job_keys),
            "golden": sum(1 for item in jobs if int(item.get("score") or 0) >= live_jobs.GOLDEN_SCORE),
            "gigs": len([item for item in gigs if str(item.get("status") or "") != "skip"]),
            "internships": len([item for item in internships if str(item.get("status") or "") != "skip"]),
            "combine": len({
                str(item.get("link") or f"{item.get('company')}|{item.get('position')}|{item.get('source')}").lower()
                for item in jobs
                if int(item.get("moonlight_compatible") or 0)
            }),
            "sent": sent_count,
            "candidates": len(candidates),
            # Show the whole available catalogue, while each card clearly says
            # whether it is an automated public feed or a manual verification link.
            "sources": len(sources_list),
            "live_sources": sum(
                1 for item in sources_list
                if int(item.get("enabled") or 0) and str(item.get("kind") or "").lower() == "live feed"
            ),
        },
        "candidates": candidates,
        "jobs": jobs,
        "applications": applications,
        "gigs": gigs,
        "internships": internships,
        "education_recommendations": education_recommendations(),
        "higher_education_options": higher_education_options(),
        "education_application_guide": education_application_guide(),
        "applicant_resources": applicant_resources(),
        "relocation_resources": relocation_resources(),
        "sources_list": sources_list,
        "strategy_migrated": migrated_strategy,
        "local_demo_seeded": seeded_demo,
        "resume_pack_updated": bool(refreshed_resume_pack),
        "search": latest_search,
        "network": live_jobs.search_network_label().replace("Render", "CareerMove API"),
    }
    return json_safe(response)


def higher_education_options() -> list[dict[str, Any]]:
    """Practical admission shortlist for the two current CareerMove profiles.

    Scores are deliberately conservative planning estimates, not admission
    promises.  They assume a completed secondary-school certificate and no
    undisclosed academic or visa restriction; every card links to the official
    programme or admissions page so volatile fees and deadlines can be checked.
    """
    return [
        {
            "rank": 1,
            "kind": "university",
            "institution": "University of the People",
            "program": "Associate of Science in Computer Science",
            "location": "США · полностью онлайн из Вьетнама",
            "mode": "онлайн",
            "language": "английский; старт после подтверждения языка или подготовительного English course",
            "credential": "аккредитованная американская степень Associate",
            "recognition": "Университет аккредитован WSCUC. Это полноценная степень США, но признание конкретным работодателем или вузом для перевода всё равно проверяется отдельно.",
            "cost": "$60 application fee + $180 за каждый завершённый курс; около 20 курсов",
            "funding": "Можно запросить scholarship на assessment fees; помощь не гарантирована и запрашивается отдельно для каждого",
            "admission": "16+, школьный аттестат, английский и прохождение Foundations; традиционного вступительного экзамена нет.",
            "math": "Есть базовая математика, но маршрут короче бакалавриата; до поступления можно запросить точный план курсов.",
            "deadline": "Набор по термам в течение года; запрашивать ближайший доступный term сейчас.",
            "ease_score": 92,
            "budget_score": 90,
            "qa_candidate": {"score": 82, "label": "высокий шанс после English bridge", "note": "Опыт QA усиливает мотивацию; главный барьер сейчас — английский A1 и документ об образовании."},
            "support_candidate": {"score": 78, "label": "высокий шанс после English bridge", "note": "Опыт в IT не обязателен; нужно показать готовность к регулярной самостоятельной учёбе."},
            "preparation": ["Поднять чтение и письмо до A2", "Сделать заверенный перевод аттестата", "Подать две отдельные заявки и сразу запросить scholarship", "Проверить недельную нагрузку до оплаты первого курса"],
            "caveat": "Не полностью бесплатно: при отсутствии scholarship остаются сборы за курсы. Associate ниже бакалавриата, но его можно продолжить до BS.",
            "url": "https://www.uopeople.edu/programs/online-associates/computer-science/",
            "apply_url": "https://apply.uopeople.edu/",
            "scholarship_url": "https://www.uopeople.edu/tuition-free/our-scholarships/",
            "community_url": "https://www.reddit.com/r/UoPeople/",
            "contact": "Admissions через apply.uopeople.edu; вопрос об аккредитации сверять в каталоге WSCUC.",
        },
        {
            "rank": 2,
            "kind": "university",
            "institution": "University of the People",
            "program": "Bachelor of Science in Computer Science",
            "location": "США · полностью онлайн из Вьетнама",
            "mode": "онлайн",
            "language": "английский; самостоятельное асинхронное обучение",
            "credential": "аккредитованный американский Bachelor of Science",
            "recognition": "WSCUC-accredited degree; наиболее бюджетный полноценный бакалавриат в списке.",
            "cost": "$60 application fee + $180 за курс; около 40 курсов без scholarship",
            "funding": "Scholarship может закрывать assessment fees частично или полностью; заявку рассматривают индивидуально",
            "admission": "16+, аттестат, английский и Foundations; без классического конкурсного экзамена.",
            "math": "В программе есть College Algebra, Calculus и Discrete Mathematics. Исключить дискретную математику нельзя.",
            "deadline": "Следующий набор после сентябрьского term 2026 нужно уточнить в applicant portal.",
            "ease_score": 90,
            "budget_score": 88,
            "qa_candidate": {"score": 80, "label": "реально при A2-B1", "note": "Профессиональный QA-контекст поможет, но математику придётся проходить по программе."},
            "support_candidate": {"score": 76, "label": "реально при A2-B1", "note": "Вход возможен с нуля; успех зависит от английского и дисциплины, а не от IT-стажа."},
            "preparation": ["Дойти до A2 и начать English for IT", "Повторить алгебру школьного уровня", "Подготовить перевод аттестата", "Запросить scholarship до начала платных assessment"],
            "caveat": "Дискретная математика обязательна; учёба требует устойчивого английского и самостоятельного темпа.",
            "url": "https://www.uopeople.edu/programs/online-bachelors/computer-science/",
            "apply_url": "https://apply.uopeople.edu/",
            "scholarship_url": "https://www.uopeople.edu/tuition-free/our-scholarships/",
            "community_url": "https://www.reddit.com/r/UoPeople/",
            "contact": "Admissions через applicant portal; accreditation: wscuc.org/institutions/university-of-the-people/.",
        },
        {
            "rank": 3,
            "kind": "university",
            "institution": "The Open University",
            "program": "BSc (Honours) Computing & IT (Q62)",
            "location": "Великобритания · онлайн из Вьетнама",
            "mode": "онлайн / заочно",
            "language": "английский; формального сертификата на вход обычно не требуют, но нужен рабочий уровень",
            "credential": "британский BSc (Hons); программа связана с BCS/Euro-Inf recognition",
            "recognition": "Диплом крупного британского государственного дистанционного университета; конкретную professional accreditation выбрать по маршруту модулей.",
            "cost": "Ориентир около £24,528 за полный degree при текущей ставке; оплачивается по модулям",
            "funding": "Полное финансирование для живущих за пределами UK маловероятно; спрашивать international bursary и рассрочку",
            "admission": "Нет формальных entry requirements; можно начать с Access module и учиться 6 лет part-time.",
            "math": "Можно выбрать более мягкий модуль Discovering Mathematics (MU123), но полностью без математики IT-degree не будет.",
            "deadline": "Проверить ближайший октябрьский/февральский старт на странице программы.",
            "ease_score": 88,
            "budget_score": 12,
            "qa_candidate": {"score": 78, "label": "поступить легко, оплатить сложно", "note": "Формальный вход реалистичен после B1; QA-опыт полезен для выбора computing pathway."},
            "support_candidate": {"score": 75, "label": "поступить легко, оплатить сложно", "note": "Можно входить без IT-опыта через Access module, но английский A1 пока недостаточен."},
            "preparation": ["Поднять английский до B1", "Запросить international fee support письменно", "Выбрать part-time 60 credits/year", "Сравнить MU123 с обязательными модулями до регистрации"],
            "caveat": "Очень сильный по доступности поступления, но не по бюджету; не оплачивать до письменного расчёта полной стоимости для Vietnam resident.",
            "url": "https://www.open.ac.uk/courses/computing-it/degrees/bsc-computing-it-q62/",
            "apply_url": "https://www.open.ac.uk/courses/apply",
            "community_url": "https://www.reddit.com/r/OpenUniversity/",
            "contact": "International student enquiry через официальный Open University Help Centre.",
        },
        {
            "rank": 4,
            "kind": "college",
            "institution": "Softech Aptech Da Nang",
            "program": "Advanced Diploma in Software Engineering (ADSE)",
            "location": "Дананг, Вьетнам",
            "mode": "очно / прикладная программа",
            "language": "уточнить язык потока; материалы и IT-термины частично на английском",
            "credential": "международный профессиональный Advanced Diploma Aptech, не университетский bachelor",
            "recognition": "Прикладной диплом и возможный top-up полезны для входа в разработку, но это не замена аккредитованному бакалавриату без отдельного top-up degree.",
            "cost": "Стоимость и рассрочка запрашиваются у кампуса; бывают скидки наборам и группам",
            "funding": "Полная бюджетная учёба не заявлена; реальнее скидка и помесячная оплата",
            "admission": "Низкий порог; аттестат и консультация/входная диагностика, точные правила для граждан РФ запросить заранее.",
            "math": "Фокус прикладной; запросить syllabus и подтвердить отсутствие отдельного курса дискретной математики.",
            "deadline": "Наборы несколько раз в год; запросить ближайший intake и место для двух иностранных студентов.",
            "ease_score": 85,
            "budget_score": 35,
            "qa_candidate": {"score": 82, "label": "высокий шанс", "note": "QA-база и технический опыт подходят; важно уточнить язык и итоговый top-up маршрут."},
            "support_candidate": {"score": 80, "label": "высокий шанс", "note": "Один из наиболее мягких стартов с нуля, если доступен понятный языковой поток."},
            "preparation": ["Написать в кампус на английском и через переводчик на вьетнамском", "Запросить полный syllabus и расписание", "Получить письменный расчёт на двоих", "Проверить университет-партнёр для bachelor top-up"],
            "caveat": "Не называть этот диплом высшим образованием без завершённого top-up bachelor; признание зависит от страны и работодателя.",
            "url": "https://www.aptech-danang.edu.vn/tuyensinh",
            "apply_url": "https://www.aptech-danang.edu.vn/lien-he",
            "contact": "tuyensinh@softech.vn · +84 236 3 779 779",
        },
        {
            "rank": 5,
            "kind": "college",
            "institution": "BTEC FPT Da Nang",
            "program": "BTEC Higher National Diploma in Computing",
            "location": "Дананг, Вьетнам",
            "mode": "очно · около 2 лет",
            "language": "уточнить поддержку иностранцев; профессиональная терминология на английском",
            "credential": "Pearson BTEC HND Level 5, эквивалент первых двух лет degree для progression",
            "recognition": "Pearson HND признаётся многими университетами для top-up, но сам по себе не является bachelor. Университет назначения должен письменно подтвердить зачёт.",
            "cost": "Запрашивается у BTEC FPT; обычно дешевле международного бакалавриата",
            "funding": "Есть конкурсные scholarships 2026; 100% не гарантированы и зависят от оценок/достижений",
            "admission": "Обычно по аттестату и документам; правила для иностранного аттестата и визы нужно подтвердить у кампуса.",
            "math": "В опубликованном Computing curriculum есть Discrete Maths (15 credits).",
            "deadline": "Уточнить текущий набор 2026 и scholarship interview до подачи.",
            "ease_score": 82,
            "budget_score": 45,
            "qa_candidate": {"score": 74, "label": "вероятно после проверки документов", "note": "QA-опыт будет плюсом; scholarship зависит прежде всего от аттестата и интервью."},
            "support_candidate": {"score": 72, "label": "вероятно после проверки документов", "note": "Программа прикладная и подходит новичку, но дискретная математика останется."},
            "preparation": ["Собрать оценки 11-12 класса", "Подготовить перевод аттестата", "Повторить базовую алгебру", "До оплаты выбрать университет top-up и получить от него письменное подтверждение"],
            "caveat": "Есть дискретная математика; итоговый bachelor потребует отдельного top-up, времени и денег.",
            "url": "https://btec.fpt.edu.vn/",
            "apply_url": "https://btec.fpt.edu.vn/dang-ky-tu-van/",
            "scholarship_url": "https://btec.fpt.edu.vn/chuong-trinh-hoc-bong-cung-btec-fpt-buoc-ra-the-gioi-nam-2026/",
            "contact": "Приёмная BTEC FPT через официальный registration form; выбрать кампус Đà Nẵng.",
        },
        {
            "rank": 6,
            "kind": "university",
            "institution": "Greenwich Vietnam",
            "program": "Bachelor pathways in Information Technology",
            "location": "Дананг, Вьетнам",
            "mode": "очно",
            "language": "английский; до основной программы доступна 6-уровневая English preparation",
            "credential": "британская степень University of Greenwich",
            "recognition": "Международный британский диплом, обучение полностью во Вьетнаме.",
            "cost": "Точную международную tuition quote запрашивать для двух заявителей; без scholarship дорого",
            "funding": "Scholarships 30/50/70/100%; крупные скидки требуют сильного аттестата, достижений или высокого IELTS",
            "admission": "Аттестат, проверка документов и English placement; IELTS 6 позволяет перейти сразу к major.",
            "math": "Computer Science/IT содержит математику; запросить более прикладной major и module list.",
            "deadline": "Подача через CRM; уточнить ближайший intake и scholarship interview в Da Nang.",
            "ease_score": 78,
            "budget_score": 25,
            "qa_candidate": {"score": 68, "label": "реально через English prep", "note": "Поступление реалистичнее полной стипендии; оценки аттестата критичны для скидки."},
            "support_candidate": {"score": 64, "label": "реально через English prep", "note": "Можно начать с языкового блока, но полное финансирование без сильных оценок маловероятно."},
            "preparation": ["Запросить оценку российского аттестата", "Пройти бесплатный English placement", "Собрать портфолио/достижения для scholarship interview", "Подать две заявки в один intake"],
            "caveat": "100% scholarship конкурсная; сначала получить официальную смету после всех скидок, затем принимать решение.",
            "url": "https://greenwich.edu.vn/",
            "apply_url": "https://crm.greenwich.edu.vn/",
            "scholarship_url": "https://tuyensinh.greenwich.edu.vn/phong-van-hoc-bong",
            "contact": "Da Nang admissions через crm.greenwich.edu.vn.",
        },
        {
            "rank": 7,
            "kind": "university",
            "institution": "Swinburne Vietnam",
            "program": "Bachelor of Computer Science · Software Development / Cybersecurity",
            "location": "Дананг, Вьетнам",
            "mode": "очно",
            "language": "английский; для уровня ниже IELTS 6 есть Global Citizen English pathway",
            "credential": "австралийский bachelor Swinburne University of Technology",
            "recognition": "Тот же австралийский бренд degree; в Дананге доступны прикладные CS majors.",
            "cost": "Ориентир $22,000 за 9 semesters до scholarship и English pathway",
            "funding": "Scholarships/discounts запрашиваются при поступлении; полное финансирование не является базовым сценарием",
            "admission": "Аттестат, ориентир GPA 7/10; Mathematics в 11 классе; English grade/interview или IELTS 6 для прямого входа.",
            "math": "Математика входит в критерии и CS curriculum; полностью избежать её нельзя.",
            "deadline": "Intakes обычно January, May, September; подтвердить место и scholarship для ближайшего набора.",
            "ease_score": 72,
            "budget_score": 20,
            "qa_candidate": {"score": 58, "label": "возможно при GPA 7+", "note": "Опыт QA полезен для мотивационного интервью; A1 означает обязательный English pathway."},
            "support_candidate": {"score": 55, "label": "возможно при GPA 7+", "note": "Проверят школьную математику и английский; IT-опыт не обязателен."},
            "preparation": ["Посчитать GPA по аттестату", "Взять English placement/interview", "Повторить школьную математику", "Запросить скидку и полный бюджет с English pathway"],
            "caveat": "Сильный диплом, но дорогой путь; без большой scholarship не соответствует вашему текущему бюджету.",
            "url": "https://swinburne-vn.edu.vn/en/course/software-development/",
            "apply_url": "https://tuyensinh.swin.edu.vn/dangkyxettuyen",
            "scholarship_url": "https://swinburne-vn.edu.vn/en/list-admission/admission-announcement/",
            "contact": "Da Nang admission через официальный application form Swinburne Vietnam.",
        },
        {
            "rank": 8,
            "kind": "university",
            "institution": "VNUK · University of Danang",
            "program": "Bachelor in Computer Science / Software Engineering",
            "location": "Дананг, Вьетнам",
            "mode": "очно",
            "language": "английский; условия языковой подготовки для A1 уточнить до заявки",
            "credential": "бакалавр University of Danang; отдельные dual-degree маршруты проверяются по программе",
            "recognition": "Государственный университет Вьетнама; это не автоматически британский диплом, если не выбран и не оплачен конкретный dual-degree route.",
            "cost": "Ниже многих иностранных филиалов; международную tuition quote запросить у VNUK",
            "funding": "Merit scholarships до 100%, но детальные условия обычно требуют сильных оценок и английского",
            "admission": "Для иностранных выпускников заявлен transcript review; нужны аттестат и перевод. На 2026 основной срок 15 июня уже прошёл.",
            "math": "Выбирать Software Engineering и заранее запросить module map; базовая математика всё равно будет.",
            "deadline": "Срочно спросить о late admission 2026; иначе готовить цикл 2027.",
            "ease_score": 65,
            "budget_score": 55,
            "qa_candidate": {"score": 55, "label": "возможно при late admission", "note": "Оценка зависит от аттестата; опыт QA поможет в письме, но не заменит язык."},
            "support_candidate": {"score": 50, "label": "возможно при late admission", "note": "Нужна проверка аттестата и понятный языковой bridge для иностранца."},
            "preparation": ["Сегодня написать о late admission/2027", "Сделать перевод и легализацию аттестата", "Дойти минимум до A2", "Подать запрос на scholarship одновременно с admission"],
            "caveat": "Текущий основной дедлайн прошёл; полная scholarship и English bridge не гарантированы.",
            "url": "https://vnuk.udn.vn/en/2026-admissions-update-vnuk-officially-expands-direct-admission-opportunities/",
            "apply_url": "https://dangkyxettuyen.vnuk.udn.vn/",
            "scholarship_url": "https://vnuk.udn.vn/en/scholarships/merit-scholarships/",
            "community_url": "https://zalo.me/g/qiasdz432",
            "contact": "contact@vnuk.edu.vn · +84 905 55 66 54",
        },
        {
            "rank": 9,
            "kind": "university",
            "institution": "Duy Tan University · Troy University partnership",
            "program": "Bachelor of Science in Computer Science",
            "location": "Дананг, Вьетнам",
            "mode": "очно",
            "language": "100% английский",
            "credential": "американский degree Troy University по партнёрской программе",
            "recognition": "Американская партнёрская степень; до оплаты запросить подтверждение, что конкретный intake и transcript выдаёт Troy University.",
            "cost": "Запросить актуальную tuition quote; без scholarship дорого",
            "funding": "Стипендии 30-100%; 100% обычно требует выдающихся оценок/олимпиадных результатов",
            "admission": "Аттестат, английский и проверка иностранных документов; точный English pathway запросить у приёмной комиссии.",
            "math": "CS degree включает математику; отсутствие discrete math не обещано.",
            "deadline": "Уточнить международный admission 2026/2027 в Duy Tan.",
            "ease_score": 62,
            "budget_score": 30,
            "qa_candidate": {"score": 50, "label": "средний шанс на admission", "note": "Для учёбы нужен быстрый рост английского; шанс на 100% намного ниже шанса поступления."},
            "support_candidate": {"score": 45, "label": "погранично до English prep", "note": "Потребуется сильное мотивационное письмо и подтверждённый языковой маршрут."},
            "preparation": ["Запросить English bridge", "Собрать академические достижения", "Получить письменное подтверждение Troy degree", "Сравнить итоговую стоимость после scholarship"],
            "caveat": "Поступление и полная стипендия — разные конкурсы; A1 недостаточен для старта основной программы.",
            "url": "https://duytan.edu.vn/tuyen-sinh/Page/EducationDetail.aspx?id=234",
            "apply_url": "https://duytan.edu.vn/tuyen-sinh/Page/Home.aspx",
            "contact": "tuyensinh@duytan.edu.vn",
        },
        {
            "rank": 10,
            "kind": "university",
            "institution": "University of London",
            "program": "Online BSc Computer Science",
            "location": "Великобритания · онлайн из Вьетнама",
            "mode": "онлайн / 3-6 лет",
            "language": "английский",
            "credential": "британский BSc University of London",
            "recognition": "Международно известная университетская степень, полностью онлайн.",
            "cost": "Band A ориентир около £14,666 за программу, без пересдач и дополнительных расходов",
            "funding": "Полная стипендия маловероятна; проверить локальные scholarships и оплату по модулям",
            "admission": "Direct entry по документам или performance-based route; для PBA нужны 17+, English и около года релевантного IT-опыта.",
            "math": "В PBA нужно пройти Programming I и Computational or Discrete Mathematics.",
            "deadline": "Заявки на October 2026 указаны до 9 сентября 2026.",
            "ease_score": 60,
            "budget_score": 15,
            "qa_candidate": {"score": 52, "label": "возможен performance route", "note": "QA-опыт может подтвердить релевантный IT-год; English нужно быстро поднять."},
            "support_candidate": {"score": 32, "label": "пока низкий", "note": "Без года IT-опыта PBA сложнее; сначала UoPeople/Open University или колледжный маршрут."},
            "preparation": ["QA-кандидата собрать подтверждение QA-стажа", "Сдать/подтвердить English requirement", "Повторить алгебру и computational math", "Подать до текущего дедлайна только после fee assessment"],
            "caveat": "Не бюджетный вариант и содержит математику; Support-кандидат может не пройти PBA по опыту.",
            "url": "https://www.london.ac.uk/study/courses/undergraduate/bsc-computer-science",
            "apply_url": "https://www.london.ac.uk/applications/how-apply",
            "contact": "Course enquiries через официальный University of London page.",
        },
        {
            "rank": 11,
            "kind": "university",
            "institution": "Vietnamese-German University (VGU)",
            "program": "BSc Computer Science and Engineering",
            "location": "Bình Dương / рядом с Ho Chi Minh City, Вьетнам",
            "mode": "очно · потребуется переезд из Дананга",
            "language": "английский; IELTS 5+ или VGU English test для входа",
            "credential": "степень Frankfurt University of Applied Sciences, Германия",
            "recognition": "Сильный германский degree route во Вьетнаме; один из лучших вариантов по признанию в списке.",
            "cost": "Запросить international tuition; без scholarship не соответствует нулевому бюджету",
            "funding": "Ежегодные scholarships 25/50/100%; 100% на четыре года крайне конкурсная и обычно требует IELTS 7 и очень сильного TestAS/достижений",
            "admission": "Аттестат, TestAS/альтернативный qualifying route и English; есть foundation year.",
            "math": "Инженерная программа содержит серьёзную математику, включая дискретные темы.",
            "deadline": "Проверить актуальный 2026 intake на apply.vgu.edu.vn.",
            "ease_score": 45,
            "budget_score": 45,
            "qa_candidate": {"score": 30, "label": "stretch-вариант", "note": "За два месяца реально подготовить документы, но IELTS/TestAS и математика требуют больше времени."},
            "support_candidate": {"score": 25, "label": "stretch-вариант", "note": "С нуля и A1 вход сейчас маловероятен; рассматривать цикл 2027 после подготовки."},
            "preparation": ["Записаться на английский с целью IELTS 5-6", "Начать TestAS sample tests", "Повторить математику", "Спросить о foundation и scholarship для двух foreign applicants"],
            "caveat": "Не облегчённая программа и не путь без математики; стоит отправить запрос ради сильного диплома, но не считать основным шансом 2026.",
            "url": "https://tuyensinh.vgu.edu.vn/en/chuong-trinh-dai-hoc/khoa-hoc-may-tinh",
            "apply_url": "https://apply.vgu.edu.vn/",
            "scholarship_url": "https://tuyensinh.vgu.edu.vn/en/hocbong",
            "contact": "study@vgu.edu.vn · scholarships@vgu.edu.vn",
        },
        {
            "rank": 12,
            "kind": "university",
            "institution": "VinUniversity",
            "program": "Bachelor of Science in Computer Science",
            "location": "Ханой, Вьетнам",
            "mode": "очно · потребуется переезд",
            "language": "английский",
            "credential": "вьетнамский international-oriented bachelor",
            "recognition": "Сильная современная программа и международные академические партнёрства, но молодой университет требует отдельной проверки признания в стране будущей работы.",
            "cost": "Высокая tuition без financial support",
            "funding": "Merit scholarships и donor grants возможны, включая крупную поддержку; конкурс очень высокий",
            "admission": "Holistic AACC review: академические результаты, качества, вклад и интервью; нужен рабочий English.",
            "math": "CS включает математику; облегчённого math-free route нет.",
            "deadline": "Rolling round 2026 закончился 15 августа 2026; срочно спросить late consideration или готовить 2027.",
            "ease_score": 30,
            "budget_score": 35,
            "qa_candidate": {"score": 18, "label": "низкий в текущем цикле", "note": "Дедлайн прошёл, A1 и неизвестные оценки снижают шанс; сильная история QA может помочь только в следующем цикле."},
            "support_candidate": {"score": 15, "label": "низкий в текущем цикле", "note": "Нужны английский, академический профиль и достижения для конкурентного scholarship."},
            "preparation": ["Написать admissions о late consideration", "Поднять English до B1-B2", "Собрать достижения и рекомендации", "Готовить цельный application story на 2027"],
            "caveat": "Очень конкурсный вариант, текущий дедлайн уже прошёл; оставить как дополнительную заявку, не основной план.",
            "url": "https://admissions.vinuni.edu.vn/vinuniversity-scholarship-program-academic-year-2026-2027/",
            "apply_url": "https://apply.vinuni.edu.vn/",
            "contact": "admissions@vinuni.edu.vn · WhatsApp +84 98 100 8189",
        },
        {
            "rank": 13,
            "kind": "university",
            "institution": "Fulbright University Vietnam",
            "program": "Bachelor in Computer Science",
            "location": "Ho Chi Minh City, Вьетнам",
            "mode": "очно · потребуется переезд",
            "language": "английский",
            "credential": "американская liberal-arts модель, степень Fulbright University Vietnam",
            "recognition": "Сильная международная среда; признание degree и аккредитационный статус проверять для конкретной страны продолжения.",
            "cost": "Высокая tuition без scholarship",
            "funding": "International applicants не получают need-based financial aid; остаются только конкурсные scholarships",
            "admission": "Holistic application и английский; STEM scholarship требует сильных оценок/достижений и обычно IELTS 6.",
            "math": "Discrete Mathematics прямо входит в Computer Science curriculum.",
            "deadline": "Основной summer cycle 2026 завершён; уточнить следующий intake.",
            "ease_score": 25,
            "budget_score": 8,
            "qa_candidate": {"score": 15, "label": "очень низкий при нулевом бюджете", "note": "Поступить потенциально возможно позже, но международная need-based помощь недоступна."},
            "support_candidate": {"score": 12, "label": "очень низкий при нулевом бюджете", "note": "Нужны English, достижения и отдельная merit scholarship; программа содержит дискретную математику."},
            "preparation": ["Не тратить application fee без подтверждения scholarship eligibility", "Поднять English до IELTS 6", "Собрать STEM achievements", "Рассматривать только как дальний конкурсный вариант"],
            "caveat": "Плохо соответствует вашему бюджету и желанию избежать дискретной математики.",
            "url": "https://fulbright.edu.vn/major/computer-science/",
            "apply_url": "https://fulbright.edu.vn/apply-to-us/",
            "scholarship_url": "https://fulbright.edu.vn/tuition-and-aid-scholarships/",
            "contact": "admissions@fulbright.edu.vn",
        },
    ]


def education_application_guide() -> list[dict[str, Any]]:
    return [
        {"step": 1, "title": "Собрать один цифровой пакет", "detail": "Паспорта, аттестаты с приложениями, сканы оценок, CV, краткая история смены профессии и подтверждения опыта. Пока не отправляйте оригиналы."},
        {"step": 2, "title": "Сделать перевод документов", "detail": "Сначала запросите у каждого вуза, нужен ли нотариальный перевод, апостиль или консульская легализация именно для российского документа."},
        {"step": 3, "title": "Отправить pre-admission enquiry", "detail": "До оплаты спросите о признании аттестата, English pathway с A1, полной стоимости, визе, ближайшем intake и двух независимых scholarship applications."},
        {"step": 4, "title": "Подать две отдельные заявки", "detail": "QA-кандидат и Support-кандидат подаются как отдельные кандидаты. В каждой заявке просите full scholarship/fee waiver и разрешение донести English result позже."},
        {"step": 5, "title": "Сверить ответы в таблице", "detail": "Фиксируйте deadline, application fee, кто выдаёт диплом, аккредитацию, обязательную математику, итоговую цену после скидки и письменное решение по scholarship."},
        {"step": 6, "title": "Платить только после проверки", "detail": "Сверьте домен, оферту, refund policy и accreditation. Не переводите деньги посреднику или участнику чата."},
    ]


def applicant_resources() -> list[dict[str, Any]]:
    return [
        {"title": "StudyQA", "kind": "Telegram-канал", "detail": "кейсы поступления и scholarships за рубежом", "url": "https://t.me/studyqa"},
        {"title": "StudyQA Community", "kind": "Telegram-чат", "detail": "вопросы абитуриентов; ответы перепроверять на сайтах вузов", "url": "https://t.me/studyqacomchat", "safety": "не передавайте паспорт и деньги участникам"},
        {"title": "VNUK Admissions 2026", "kind": "официальная Zalo-группа", "detail": "поддержка поступающих VNUK в Дананге", "url": "https://zalo.me/g/qiasdz432"},
        {"title": "International Students", "kind": "Reddit", "detail": "опыт иностранных студентов по документам, визам и адаптации", "url": "https://www.reddit.com/r/InternationalStudents/"},
        {"title": "UoPeople students", "kind": "Reddit", "detail": "опыт по scholarship, нагрузке и переводам credits", "url": "https://www.reddit.com/r/UoPeople/"},
        {"title": "Open University students", "kind": "Reddit", "detail": "нагрузка и выбор модулей Q62", "url": "https://www.reddit.com/r/OpenUniversity/"},
    ]


def relocation_resources() -> list[dict[str, Any]]:
    safety = "встречайтесь в публичном месте; билеты и жильё оплачивайте только официальным сервисам"
    return [
        {"title": "Русский Дананг", "kind": "Telegram-чат", "detail": "быт, помощь и знакомства в Дананге; подходит для поиска попутчиков", "url": "https://t.me/rus_danang", "safety": safety},
        {"title": "Da Nang RU", "kind": "Telegram-чат", "detail": "русскоязычное городское сообщество", "url": "https://t.me/danang_ru", "safety": safety},
        {"title": "Da Nang Meetups", "kind": "Telegram-чат", "detail": "встречи и знакомства уже после приезда", "url": "https://t.me/danang_meetups", "safety": safety},
        {"title": "Vietnam RU chats", "kind": "Telegram-каталог", "detail": "навигация по локальным чатам Вьетнама", "url": "https://t.me/Vietnam_ru", "safety": "проверяйте, что выбранный чат живой и модерируется"},
    ]


def education_recommendations() -> list[dict[str, Any]]:
    return [
        {
            "track": "Старт без бюджета",
            "title": "English for IT + ежедневная практика чтения вакансий",
            "format": "онлайн, бесплатно/недорого",
            "fit": "Первый обязательный слой: без A2-B1 слишком много хороших junior/support/QA ролей будет отсеиваться.",
            "eligibility": "Подходит обоим сразу; цель на 8-12 недель — понимать требования вакансий, тикеты и базовую документацию.",
            "priority": "1 · легко и нужно",
            "url": "https://learnenglish.britishcouncil.org/",
        },
        {
            "track": "Старт без бюджета",
            "title": "freeCodeCamp · Responsive Web / JavaScript / QA basics",
            "format": "онлайн, бесплатно",
            "fit": "Быстрый способ собрать базу программирования и первые проекты без поступления и оплаты.",
            "eligibility": "Подходит с нуля; после первых модулей можно выбирать Java/Swift или QA automation осознаннее.",
            "priority": "2 · база портфолио",
            "url": "https://www.freecodecamp.org/learn/",
        },
        {
            "track": "Старт без бюджета",
            "title": "CS50x · Introduction to Computer Science",
            "format": "онлайн, бесплатно, сертификат платный по желанию",
            "fit": "Сильная базовая программа для понимания разработки, но сложнее, чем прикладные курсы.",
            "eligibility": "Лучше начинать после 2-4 недель английского и базовой практики, проходить в спокойном темпе.",
            "priority": "3 · сложнее, но ценится",
            "url": "https://cs50.harvard.edu/x/",
        },
        {
            "track": "Основное образование во Вьетнаме",
            "title": "RMIT Vietnam · Software Engineering / IT",
            "format": "кампус во Вьетнаме, англоязычное обучение",
            "fit": "Сильный вариант для признанного диплома в IT, но вход зависит от английского и академических документов.",
            "eligibility": "Подаваться после апостиля/перевода документов и проверки English pathway/entry requirements.",
            "priority": "7 · сильный платный путь",
            "url": "https://www.rmit.edu.vn/",
        },
        {
            "track": "Основное образование во Вьетнаме",
            "title": "FPT University · Software Engineering / Information Technology",
            "format": "Вьетнам, очно/кампус",
            "fit": "Практичный IT-маршрут внутри Вьетнама; стоит проверить международный admission и язык программы.",
            "eligibility": "Подходит как кандидатный вариант после перевода документов во Вьетнаме.",
            "priority": "6 · основное образование",
            "url": "https://daihoc.fpt.edu.vn/en/",
        },
        {
            "track": "Основное образование во Вьетнаме",
            "title": "Swinburne Vietnam · Information Technology",
            "format": "Вьетнам, международная программа",
            "fit": "Хороший компромисс между международным брендом и обучением на месте.",
            "eligibility": "Проверить требования к английскому и признание российских документов после апостиля/перевода.",
            "priority": "6 · международный диплом",
            "url": "https://swinburne-vn.edu.vn/en/",
        },
        {
            "track": "Онлайн-основа профессии",
            "title": "Google IT Support Professional Certificate",
            "format": "онлайн",
            "fit": "Лучший старт для Support-кандидата: help desk, troubleshooting, network basics, tickets.",
            "eligibility": "Обычно подходит новичкам; английский можно подтягивать параллельно, но задания будут на английском.",
            "priority": "4 · лучший старт support",
            "url": "https://www.coursera.org/professional-certificates/google-it-support",
        },
        {
            "track": "Разработка",
            "title": "JetBrains Academy / Hyperskill · Java",
            "format": "онлайн, проектное обучение",
            "fit": "Хорошая база под Java backend и автоматизацию QA без необходимости сразу поступать в университет.",
            "eligibility": "Подходит с нуля при регулярном графике и английском на уровне чтения документации.",
            "priority": "5 · Java-практика",
            "url": "https://hyperskill.org/",
        },
        {
            "track": "Разработка Apple",
            "title": "Apple · Develop in Swift / App Development with Swift",
            "format": "онлайн-материалы и Swift pathway",
            "fit": "База под iOS/macOS приложения; лучше начинать после основ программирования.",
            "eligibility": "Подходит для самостоятельного обучения, нужен Mac и английская документация.",
            "priority": "6 · после основ",
            "url": "https://developer.apple.com/learn/curriculum/",
        },
        {
            "track": "Сертификаты",
            "title": "ISTQB Certified Tester Foundation Level",
            "format": "международный экзамен",
            "fit": "Самый узнаваемый сертификат для QA; сильнее всего подходит QA-кандидата.",
            "eligibility": "Можно готовиться онлайн; экзамен планировать после освоения терминологии на английском.",
            "priority": "5 · QA-сертификат",
            "url": "https://www.istqb.org/certifications/certified-tester-foundation-level",
        },
        {
            "track": "Сертификаты",
            "title": "Oracle Certified Professional · Java SE Developer",
            "format": "международный экзамен",
            "fit": "Серьёзный сертификат для Java-разработчика, но не первый шаг с нуля.",
            "eligibility": "Ставить после 6-12 месяцев Java-практики и pet projects.",
            "priority": "8 · после 6-12 месяцев Java",
            "url": "https://education.oracle.com/java-se-17-developer/pexam_1Z0-829",
        },
        {
            "track": "Сертификаты",
            "title": "Microsoft Learn · Azure Fundamentals / Developer path",
            "format": "онлайн + экзамены",
            "fit": "Полезно для Windows/cloud/software development, особенно если уходить в backend или поддержку.",
            "eligibility": "Начинать с бесплатных learning paths; экзамен сдавать после практики.",
            "priority": "5 · cloud/support база",
            "url": "https://learn.microsoft.com/en-us/credentials/",
        },
    ]


def gig_english_blocked(text: str, english_level: str) -> bool:
    level = str(english_level or "").strip().upper()
    if level not in {"A1", "A2", "B1"}:
        return False
    return bool(live_jobs.HIGH_ENGLISH_RE.search(text or ""))


SIDE_GIG_SIGNAL_RE = re.compile(
    r"freelance|part[ -]?time|project[- ]based|side project|side job|moonlight|"
    r"неполн(?:ая|ой) занятость|частичн(?:ая|ой) занятость|подработ|фриланс|проектная работа|"
    r"flexible (?:hours|schedule)|гибкий график|asynchronous|async work|асинхрон|"
    r"no exclusivity|non[- ]exclusive|можно совмещать|совмещение разрешено|"
    r"(?:up to|до)\s*\d{1,2}\s*(?:hours?|час)",
    re.IGNORECASE,
)


def sync_side_gigs_from_jobs(
    query, execute, user_id: int, jobs: list[dict[str, Any]], *, max_age_days: int = 30,
) -> dict[str, int]:
    """Mirror only fresh, explicitly combinable search results into Side gigs."""
    current = datetime.now(timezone.utc)
    cutoff = current - timedelta(days=max_age_days)
    stale = query(
        """
        SELECT g.id,g.posted_at,g.active_checked_at,g.source,
          COALESCE(v.status,'') vacancy_status
        FROM side_gigs g
        LEFT JOIN vacancies v
          ON v.user_id=g.user_id AND v.candidate_id=g.candidate_id AND v.link=g.link
        WHERE g.user_id=? AND COALESCE(g.is_active,1)=1 AND g.category<>'Стажировка'
        """,
        (user_id,),
    )
    archived = 0
    if not stale.empty:
        for row in stale.to_dict("records"):
            posted = live_jobs.parse_datetime(row.get("posted_at"))
            checked = live_jobs.parse_datetime(row.get("active_checked_at"))
            generated = str(row.get("source") or "") != "Добавлено вручную"
            expired = bool((posted and posted < cutoff) or (generated and not posted and checked and checked < cutoff))
            if str(row.get("vacancy_status") or "") == "archived" or expired:
                execute("UPDATE side_gigs SET is_active=0,active_checked_at=? WHERE id=? AND user_id=?", (now_iso(), int(row["id"]), user_id))
                archived += 1

    saved = 0
    refreshed = 0
    for job in jobs:
        try:
            candidate_id = int(job.get("candidate_id") or 0)
        except (TypeError, ValueError):
            candidate_id = 0
        if not candidate_id:
            continue
        text = " ".join(str(job.get(key) or "") for key in ("title", "description", "tags", "job_type", "work_type", "location"))
        if not SIDE_GIG_SIGNAL_RE.search(text):
            continue
        posted = live_jobs.parse_datetime(job.get("posted_at"))
        if not posted or posted < cutoff:
            continue
        link = live_jobs.canonical_url(job.get("url") or job.get("link"))
        if not link or live_jobs.concrete_vacancy_reason(job):
            continue
        candidate = query("SELECT english_level FROM candidates WHERE id=? AND user_id=?", (candidate_id, user_id))
        english_level = "" if candidate.empty else str(candidate.iloc[0].get("english_level") or "")
        if gig_english_blocked(text, english_level):
            continue
        presentation = live_jobs.vacancy_presentation(job)
        existing = query(
            "SELECT id FROM side_gigs WHERE user_id=? AND candidate_id=? AND link=? AND category<>'Стажировка' ORDER BY id DESC LIMIT 1",
            (user_id, candidate_id, link),
        )
        score = min(94, max(58, int(job.get("score") or 0)) + (6 if job.get("salary") else 0))
        source = str(job.get("source") or "Открытый источник")
        snapshot = {
            "links": [{"url": link, "source": source, "posted_at": str(job.get("posted_at") or "")}],
            "verified_at": now_iso(),
        }
        values = (
            str(job.get("title") or "Проект"), str(job.get("company") or "Заказчик не указан"),
            source, link, str(job.get("location") or ""), "Подработка",
            str(job.get("job_type") or job.get("work_type") or "part-time"), str(job.get("salary") or "Не указана"),
            str(job.get("description") or ""), json.dumps(presentation["contacts"], ensure_ascii=False), score,
            str(job.get("posted_at") or ""), now_iso(),
            "Проверьте заказчика, договор, оплату и право работать из Вьетнама до начала.",
            "В карточке найден явный part-time/freelance/project-сигнал; подтвердите часы, сроки и отсутствие эксклюзивности.",
            json.dumps(snapshot, ensure_ascii=False),
        )
        if existing.empty:
            execute(
                """INSERT INTO side_gigs(user_id,candidate_id,title,client,source,link,location,category,work_format,pay_text,
                   description,contacts_json,score,status,posted_at,active_checked_at,is_active,safety_note,requirements_note,source_snapshot)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'found',?,?,1,?,?,?)""",
                (user_id, candidate_id, *values),
            )
            saved += 1
        else:
            execute(
                """UPDATE side_gigs SET title=?,client=?,source=?,link=?,location=?,category=?,work_format=?,pay_text=?,
                   description=?,contacts_json=?,score=?,posted_at=?,active_checked_at=?,is_active=1,safety_note=?,requirements_note=?,source_snapshot=?
                   WHERE id=? AND user_id=?""",
                (*values, int(existing.iloc[0]["id"]), user_id),
            )
            refreshed += 1
    return {"saved": saved, "refreshed": refreshed, "archived": archived}


@app.get("/api/dashboard")
def dashboard(user_id: int = Depends(current_user)) -> dict[str, Any]:
    # One Blob download and one final snapshot write, not a network round trip
    # for every candidate, certificate and source.
    with db.transaction() as (query, execute):
        return dashboard_payload(user_id, query, execute)


@app.get("/api/sources")
def list_sources(user_id: int = Depends(current_user)) -> list[dict[str, Any]]:
    ensure_catalog_sources(user_id)
    return source_rows_with_health(user_id)


@app.post("/api/sources", status_code=status.HTTP_201_CREATED)
def create_source(body: SourceRequest, user_id: int = Depends(current_user)) -> dict[str, Any]:
    name = body.name.strip()
    existing = db.query(
        "SELECT id FROM job_sources WHERE user_id=? AND lower(service)=lower(?) LIMIT 1",
        (user_id, name),
    )
    if not existing.empty:
        raise HTTPException(status_code=409, detail="Источник с таким названием уже добавлен.")
    db.execute(
        "INSERT INTO job_sources(user_id,service,source_type,region,url,enabled,notes) VALUES(?,?,?,?,?,?,?)",
        (user_id, name, body.kind.strip(), body.region.strip(), body.url.strip(), int(body.enabled), body.notes.strip()),
    )
    created = db.query(
        "SELECT id,service AS name,source_type AS kind,region,url,enabled,notes FROM job_sources WHERE user_id=? ORDER BY id DESC LIMIT 1",
        (user_id,),
    )
    return json_safe(created.iloc[0].to_dict())


@app.patch("/api/sources/{source_id}")
def update_source(source_id: int, body: SourceUpdateRequest, user_id: int = Depends(current_user)) -> dict[str, Any]:
    owned = db.query("SELECT id,enabled,notes FROM job_sources WHERE id=? AND user_id=?", (source_id, user_id))
    if owned.empty:
        raise HTTPException(status_code=404, detail="Источник не найден.")
    current = owned.iloc[0].to_dict()
    enabled = int(body.enabled) if body.enabled is not None else int(current.get("enabled") or 0)
    notes = body.notes.strip() if body.notes is not None else str(current.get("notes") or "")
    db.execute("UPDATE job_sources SET enabled=?,notes=? WHERE id=? AND user_id=?", (enabled, notes, source_id, user_id))
    row = db.query(
        "SELECT id,service AS name,source_type AS kind,region,url,enabled,notes FROM job_sources WHERE id=? AND user_id=?",
        (source_id, user_id),
    )
    return json_safe(row.iloc[0].to_dict())


@app.post("/api/candidates", status_code=status.HTTP_201_CREATED)
def create_candidate(body: CandidateRequest, user_id: int = Depends(current_user)) -> dict[str, Any]:
    values = (
        user_id, body.name.strip(), body.target_title.strip(), body.english_level.strip(),
        body.desired_countries.strip(), int(body.salary_min), body.notes.strip(),
        "", body.hard_exclude.strip(), body.hard_require.strip(), body.preferred_regions.strip(),
        body.preferred_cities.strip(), body.preferred_companies.strip(), body.priority_titles.strip(),
        body.contact_email.strip().lower(), body.cover_tone, body.cover_length, int(body.manual_review),
    )
    with db.transaction() as (query, execute):
        execute(
            """INSERT INTO candidates(
              user_id,name,target_title,english_level,desired_countries,salary_min,
              notes,private_hints,hard_exclude,hard_require,preferred_regions,preferred_cities,
              preferred_companies,priority_titles,contact_email,cover_tone,cover_length,manual_review
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            values,
        )
        candidate_id_frame = query("SELECT id FROM candidates WHERE user_id=? ORDER BY id DESC LIMIT 1", (user_id,))
        if not candidate_id_frame.empty:
            candidate_id = int(candidate_id_frame.iloc[0]["id"])
            for skill in list(dict.fromkeys(item.strip()[:100] for item in body.skills if item.strip())):
                execute("INSERT INTO skills(user_id,candidate_id,skill) VALUES(?,?,?)", (user_id, candidate_id, skill))
        created = query(
            """SELECT id,name,target_title,english_level,desired_countries,salary_min,
              notes,hard_exclude,hard_require,preferred_regions,preferred_cities,preferred_companies,
              priority_titles,contact_email,cover_tone,cover_length,manual_review,0 resume_count
              FROM candidates WHERE user_id=? ORDER BY id DESC LIMIT 1""",
            (user_id,),
        )
    if created.empty:
        raise HTTPException(status_code=500, detail="Профиль не сохранился.")
    return json_safe(created.iloc[0].to_dict())


@app.patch("/api/candidates/{candidate_id}")
def update_candidate(
    candidate_id: int,
    body: CandidateRequest,
    user_id: int = Depends(current_user),
) -> dict[str, Any]:
    with db.transaction() as (query, execute):
        owned = query("SELECT id FROM candidates WHERE id=? AND user_id=?", (candidate_id, user_id))
        if owned.empty:
            raise HTTPException(status_code=404, detail="Профиль не найден.")
        execute(
            """UPDATE candidates SET name=?,target_title=?,english_level=?,desired_countries=?,
              salary_min=?,notes=?,hard_exclude=?,hard_require=?,preferred_regions=?,preferred_cities=?,
              preferred_companies=?,priority_titles=?,contact_email=?,cover_tone=?,cover_length=?,manual_review=?
              WHERE id=? AND user_id=?""",
            (
                body.name.strip(), body.target_title.strip(), body.english_level.strip(),
                body.desired_countries.strip(), int(body.salary_min), body.notes.strip(),
                body.hard_exclude.strip(), body.hard_require.strip(), body.preferred_regions.strip(),
                body.preferred_cities.strip(), body.preferred_companies.strip(), body.priority_titles.strip(),
                body.contact_email.strip().lower(), body.cover_tone, body.cover_length, int(body.manual_review),
                candidate_id, user_id,
            ),
        )
        execute("DELETE FROM skills WHERE user_id=? AND candidate_id=?", (user_id, candidate_id))
        for skill in list(dict.fromkeys(item.strip()[:100] for item in body.skills if item.strip())):
            execute("INSERT INTO skills(user_id,candidate_id,skill) VALUES(?,?,?)", (user_id, candidate_id, skill))
        updated = query(
            """SELECT id,name,target_title,english_level,desired_countries,salary_min,
              notes,hard_exclude,hard_require,preferred_regions,preferred_cities,preferred_companies,
              priority_titles,contact_email,cover_tone,cover_length,manual_review,0 resume_count
              FROM candidates WHERE id=? AND user_id=?""",
            (candidate_id, user_id),
        )
    return json_safe(updated.iloc[0].to_dict())


def require_candidate(candidate_id: int, user_id: int) -> None:
    owned = db.query("SELECT id FROM candidates WHERE id=? AND user_id=?", (candidate_id, user_id))
    if owned.empty:
        raise HTTPException(status_code=404, detail="Профиль не найден.")


@app.post("/api/candidates/{candidate_id}/resumes")
def save_resume(candidate_id: int, body: ResumeRequest, user_id: int = Depends(current_user)) -> dict[str, Any]:
    """Store user-provided resume text privately for a single candidate."""
    require_candidate(candidate_id, user_id)
    existing = db.query(
        "SELECT id FROM resumes WHERE user_id=? AND candidate_id=? AND language=? ORDER BY id DESC LIMIT 1",
        (user_id, candidate_id, body.language),
    )
    if existing.empty:
        db.execute(
            "INSERT INTO resumes(user_id,candidate_id,language,title,content,updated_at) VALUES(?,?,?,?,?,?)",
            (user_id, candidate_id, body.language, body.title.strip(), body.content.strip(), now_iso()),
        )
    else:
        db.execute(
            "UPDATE resumes SET title=?,content=?,updated_at=? WHERE id=? AND user_id=?",
            (body.title.strip(), body.content.strip(), now_iso(), int(existing.iloc[0]["id"]), user_id),
        )
    return {"ok": True}


QA_RESUME_PACK_VERSION = "qa-remote-market-v17-contacts-cover-2026-08-17"


def _candidate_kind(name: str) -> str:
    lowered = str(name or "").lower()
    if any(marker in lowered for marker in ("demo qa", "qa candidate", "qa-кандидат", "manual qa")):
        return "qa_candidate"
    if any(marker in lowered for marker in ("demo support", "support candidate", "support-кандидат", "help desk")):
        return "support_candidate"
    return ""


def _resume_pack(kind: str) -> dict[str, Any]:
    if kind == "qa_candidate":
        return {
            "profile": {
                "target_title": "Manual QA Engineer / API QA / Product Support QA",
                "english_level": "A1-A2, improving",
                "desired_countries": "Remote worldwide; remote from Vietnam; Vietnam hybrid/office; Russian-speaking international teams",
                "salary_min": 1000,
                "hard_exclude": "Russia-only; office/hybrid in any Russian city; employment under TK RF; Russian legal entity; Sber/Сбер; Senior-only; Lead; Head; Director; C1/C2/native English required; gambling/betting",
                "hard_require": "Manual QA/API QA/Product Support QA; international remote available from Vietnam or Vietnam-based role; no TK RF/Russian legal entity; transparent international legal or contractor contract; original vacancy link or public recruiter contact",
                "preferred_regions": "Remote worldwide; Europe/EMEA; Southeast Asia; Kazakhstan; Armenia; Georgia; Cyprus; Vietnam",
                "preferred_cities": "Da Nang; Hanoi; Ho Chi Minh City; remote",
                "preferred_companies": "remote-first product companies; fintech; SaaS; support-heavy products; Russian-speaking international teams",
                "priority_titles": "Manual QA Engineer; API QA Engineer; QA Specialist; Software Tester; Manual Tester; Product QA; Product Support Specialist; Technical Support Specialist",
                "notes": (
                    "Recruiter positioning: Manual QA/API QA specialist with banking and fintech domain experience, "
                    "Postman/API checks, SQL/PostgreSQL validation, regression, release checks, bug reports and user-support background. "
                    "Best match: junior+/middle- manual QA, API QA, product support QA, fintech/SaaS QA. Open to international remote work from Vietnam."
                ),
            },
            "skills": [
                "Manual QA - Advanced",
                "API testing - Intermediate",
                "Postman - Intermediate",
                "SQL - Intermediate",
                "PostgreSQL - Basic+",
                "Regression testing - Intermediate",
                "Smoke testing - Intermediate",
                "Bug reports - Advanced",
                "Test documentation - Intermediate",
                "Mobile/Web testing - Basic+",
                "Swagger/OpenAPI - Basic+",
                "Chrome DevTools - Basic",
                "Fintech/Banking domain - Intermediate",
                "User support - Advanced",
                "Attention to detail - Advanced",
                "Calm written communication - Advanced",
            ],
            "resumes": {
                "EN": (
                    "Demo QA Candidate | Manual QA Engineer / API QA / Product Support QA\n"
                    "Location: Da Nang, Vietnam / Remote worldwide | Citizenship: Russia | Work format: remote, official contract, Vietnam hybrid/office\n\n"
                    "CONTACTS\n"
                    "Email: qa.candidate@example.com | GitHub: https://github.com/example | Portfolio: https://example.com/qa-portfolio\n\n"
                    "SUMMARY\n"
                    "Manual QA and API QA specialist with 3+ years across banking operations, user support and fintech product quality. "
                    "Hands-on experience with API checks in Postman, SQL/PostgreSQL data validation, regression/smoke testing, bug reporting, checklists and release validation. "
                    "Strong fit for remote international QA roles where recruiters search for Manual QA, API Testing, Postman, SQL, fintech, product support and clear documentation.\n\n"
                    "TARGET ROLES\n"
                    "Manual QA Engineer | API QA Engineer | QA Specialist | Software Tester | Product QA | Product Support Specialist | Technical Support Specialist\n\n"
                    "WORK FORMAT\n"
                    "Remote international work or Vietnam-based hybrid/office. Contract must be available to a Vietnam resident; no Russian Labor Code/TK RF employment or Russian legal entity. Transparent legal/contractor agreement only.\n\n"
                    "EXPERIENCE\n"
                    "Example Fintech — QA-focused specialist, Jun 2025 - Present\n"
                    "- Test internal banking services and client-data migration scenarios; compare expected vs actual results and validate data with PostgreSQL/SQL checks.\n"
                    "- Run regression, smoke and release-readiness checks; document defects, edge cases and business-flow risks for product and technical teams.\n\n"
                    "Example Payments — QA Engineer, Jul 2024 - Jun 2025\n"
                    "- Tested terminal software and financial operation scenarios, including API behavior in Postman and backend data consistency through SQL.\n"
                    "- Prepared bug reports, checklists, regression notes and reproducible steps for fixes.\n\n"
                    "Example Payments — User Support Specialist, Mar 2023 - Jul 2024\n"
                    "- Worked with customer requests on an inbound line, clarified financial scenarios, documented cases and coordinated with adjacent departments.\n"
                    "- Built a practical support-to-QA advantage: user empathy, precise reproduction steps and clear escalation.\n\n"
                    "AI image-generation web/mobile product — Product QA, May 2026 - Present\n"
                    "- Test user flows, mobile UI, generation scenarios, edge cases, smoke/regression scope and fix validation.\n\n"
                    "HARD SKILLS\n"
                    "Manual QA — Advanced | Bug reports — Advanced | Test documentation/checklists — Intermediate | Regression/smoke testing — Intermediate | "
                    "API testing — Intermediate | Postman — Intermediate | SQL — Intermediate | PostgreSQL/DBeaver — Basic+ | Swagger/OpenAPI — Basic+ | "
                    "Chrome DevTools — Basic | Mobile/Web testing — Basic+ | Git/GitHub — Basic | Fintech/Banking domain — Intermediate\n\n"
                    "SOFT SKILLS\n"
                    "Attention to detail — Advanced | Calm written communication — Advanced | User empathy — Advanced | Structured thinking — Intermediate+ | "
                    "Ownership of repetitive checks — Advanced | Fast learning — Intermediate+ | Cross-team coordination — Intermediate\n\n"
                    "LANGUAGES\n"
                    "Russian — Native | English — A1-A2, actively improving; comfortable with written templates, documentation, async communication and Russian-speaking onboarding."
                ),
                "RU": (
                    "Демо-кандидат QA | Manual QA Engineer / API QA / Product Support QA\n"
                    "Локация: Дананг, Вьетнам / Remote worldwide | Гражданство: РФ | Формат: удалённо, официальное оформление, hybrid/office во Вьетнаме\n\n"
                    "КОНТАКТЫ\n"
                    "Email: qa.candidate@example.com | GitHub: https://github.com/example | Портфолио: https://example.com/qa-portfolio\n\n"
                    "ПРОФИЛЬ\n"
                    "Manual QA / API QA специалист с 3+ годами в банковских операциях, поддержке пользователей и финтех-качестве продукта. "
                    "Практика: API-проверки в Postman, SQL/PostgreSQL-валидация данных, regression/smoke, баг-репорты, чек-листы и release validation. "
                    "Рекрутерские ключи: Manual QA, API Testing, Postman, SQL, fintech, product support, test documentation, remote QA.\n\n"
                    "ЦЕЛЕВЫЕ РОЛИ\n"
                    "Manual QA Engineer | API QA Engineer | QA Specialist | Software Tester | Product QA | Product Support Specialist | Technical Support Specialist\n\n"
                    "ОЖИДАНИЯ ПО ФОРМАТУ\n"
                    "Remote international или hybrid/office во Вьетнаме. Без оформления по ТК РФ и без российского юрлица; только прозрачный международный legal/contractor contract, доступный резиденту Вьетнама.\n\n"
                    "ОПЫТ\n"
                    "Example Fintech — QA-focused specialist, июнь 2025 - настоящее время\n"
                    "- Тестирую внутренние банковские сервисы и сценарии миграции клиентских данных; сравниваю expected/actual, проверяю данные через PostgreSQL/SQL.\n"
                    "- Провожу regression, smoke и release-readiness checks; фиксирую дефекты, edge cases и риски бизнес-сценариев.\n\n"
                    "Example Payments — QA Engineer, июль 2024 - июнь 2025\n"
                    "- Тестировала терминальное ПО и финансовые операции, API-поведение в Postman и консистентность данных через SQL.\n"
                    "- Готовила баг-репорты, чек-листы, regression notes и воспроизводимые шаги для исправлений.\n\n"
                    "Example Payments — User Support Specialist, март 2023 - июль 2024\n"
                    "- Работала с клиентскими обращениями на входящей линии, уточняла финансовые сценарии, документировала кейсы и эскалировала в смежные команды.\n"
                    "- Сильная связка support-to-QA: понимаю пользователя, точно воспроизвожу проблему, ясно описываю дефекты.\n\n"
                    "AI image-generation web/mobile product — Product QA, май 2026 - настоящее время\n"
                    "- Проверяю пользовательские сценарии, mobile UI, generation scenarios, edge cases, smoke/regression и fix validation.\n\n"
                    "HARD SKILLS\n"
                    "Manual QA — Advanced | Bug reports — Advanced | Test documentation/checklists — Intermediate | Regression/smoke — Intermediate | "
                    "API testing — Intermediate | Postman — Intermediate | SQL — Intermediate | PostgreSQL/DBeaver — Basic+ | Swagger/OpenAPI — Basic+ | "
                    "Chrome DevTools — Basic | Mobile/Web testing — Basic+ | Git/GitHub — Basic | Fintech/Banking domain — Intermediate\n\n"
                    "SOFT SKILLS\n"
                    "Внимательность к деталям — Advanced | Спокойная письменная коммуникация — Advanced | Пользовательская эмпатия — Advanced | "
                    "Структурное мышление — Intermediate+ | Ответственность за повторяемые проверки — Advanced | Быстрое обучение — Intermediate+ | Координация с командами — Intermediate\n\n"
                    "ЯЗЫКИ\n"
                    "Русский — Native | Английский — A1-A2, активно улучшается; комфортны письменные шаблоны, документация, async-коммуникация и русскоязычный onboarding."
                ),
            },
        }
    return {
        "profile": {
            "target_title": "Junior IT Support / Help Desk / QA Trainee",
            "english_level": "A1, improving",
            "desired_countries": "Remote worldwide; remote from Vietnam; Vietnam hybrid/office; Russian-speaking international teams",
            "salary_min": 700,
            "hard_exclude": "Russia-only; office/hybrid in any Russian city; employment under TK RF; Russian legal entity; Sber/Сбер; Senior; Lead; Head; Manager; Director; Architect; Principal; C1/C2/native English required; gambling/betting",
            "hard_require": "Junior IT Support, Help Desk, Product Support, QA Trainee or Manual QA basics; onboarding/mentorship preferred; international remote available from Vietnam or Vietnam-based role; no TK RF/Russian legal entity; transparent international legal or contractor contract",
            "preferred_regions": "Remote worldwide; Europe/EMEA; Southeast Asia; Kazakhstan; Armenia; Georgia; Cyprus; Vietnam",
            "preferred_cities": "Da Nang; Hanoi; Ho Chi Minh City; remote",
            "preferred_companies": "remote-first support teams; VPN/SaaS/product support; Russian-speaking international teams; teams with onboarding",
            "priority_titles": "Junior IT Support; Help Desk; Service Desk; Technical Support Specialist; Product Support; Support Engineer Trainee; Junior QA; Manual QA Trainee; QA Intern",
            "notes": (
                "Recruiter positioning: entry-level IT support/help desk/QA trainee candidate with structured troubleshooting, "
                "user support, documentation, basic manual QA, bug reports and readiness for onboarding. Best match: junior support, product support, VPN/support operations or QA trainee roles."
            ),
        },
        "skills": [
            "IT Support - Basic+",
            "Help Desk - Basic+",
            "Technical Support - Basic",
            "User support - Intermediate",
            "Troubleshooting - Basic+",
            "Ticket systems - Basic",
            "Manual QA basics - Basic",
            "Bug reports - Basic+",
            "Test documentation - Basic",
            "Postman - Basic",
            "SQL - Basic",
            "Chrome DevTools - Basic",
            "VPN clients - Basic+",
            "Windows/macOS user support - Basic+",
            "Patience and calm communication - Advanced",
            "Willingness to learn - Advanced",
        ],
        "resumes": {
            "EN": (
                "Demo Support Candidate | Junior IT Support / Help Desk / QA Trainee\n"
                "Location: Da Nang, Vietnam / Remote worldwide | Citizenship: Russia | Work format: remote, contract, Vietnam hybrid/office\n\n"
                "CONTACTS\n"
                "Email: support.candidate@example.com | GitHub: https://github.com/example\n\n"
                "SUMMARY\n"
                "Entry-level IT Support, Help Desk and QA Trainee candidate focused on user support, structured troubleshooting, ticket updates, documentation and basic manual QA checks. "
                "Strong fit for remote junior support roles, product support, VPN/customer support, service desk, QA trainee and internship positions with onboarding.\n\n"
                "TARGET ROLES\n"
                "Junior IT Support | Help Desk | Service Desk | Technical Support Specialist | Product Support | Support Engineer Trainee | Junior QA | Manual QA Trainee | QA Intern\n\n"
                "WORK FORMAT\n"
                "Remote international work or Vietnam-based hybrid/office. Contract must be available to a Vietnam resident; no Russian Labor Code/TK RF employment or Russian legal entity. Transparent legal/contractor agreement only.\n\n"
                "PRACTICAL STRENGTHS\n"
                "- Troubleshooting: collect symptoms, reproduce steps, separate user issue from product issue and escalate clearly.\n"
                "- User support: calm communication, patience, work with scripts, knowledge bases and standard procedures.\n"
                "- QA basics: manual checks, simple regression, screenshots, expected/actual result, reproducible bug reports and checklist updates.\n"
                "- Learning track: help desk fundamentals, VPN/user access support, SQL basics, Postman basics, then Java/Swift and app-development fundamentals.\n\n"
                "HARD SKILLS\n"
                "IT Support — Basic+ | Help Desk — Basic+ | Technical Support — Basic | Troubleshooting — Basic+ | Ticket systems — Basic | "
                "Manual QA basics — Basic | Bug reports — Basic+ | Test documentation — Basic | Postman — Basic | SQL — Basic | "
                "Chrome DevTools — Basic | VPN clients — Basic+ | Windows/macOS user support — Basic+ | Google Workspace / MS Office — Basic+\n\n"
                "SOFT SKILLS\n"
                "Patience and calm communication — Advanced | Attention to instructions — Advanced | Willingness to learn — Advanced | "
                "Reliable routine work — Advanced | Clear escalation — Basic+ | Team communication — Basic+\n\n"
                "LANGUAGES\n"
                "Russian — Native | English — A1, actively improving; best start with written/asynchronous communication or Russian-speaking onboarding."
            ),
            "RU": (
                "Демо-кандидат Support | Junior IT Support / Help Desk / QA Trainee\n"
                "Локация: Дананг, Вьетнам / Remote worldwide | Гражданство: РФ | Формат: удалённо, контракт, hybrid/office во Вьетнаме\n\n"
                "КОНТАКТЫ\n"
                "Email: support.candidate@example.com | GitHub: https://github.com/example\n\n"
                "ПРОФИЛЬ\n"
                "Entry-level кандидат в IT Support, Help Desk и QA Trainee с фокусом на поддержку пользователей, структурный troubleshooting, обновление тикетов, документацию и базовые manual QA-проверки. "
                "Подходит для junior support, product support, VPN/customer support, service desk, QA trainee и стажировок с onboarding.\n\n"
                "ЦЕЛЕВЫЕ РОЛИ\n"
                "Junior IT Support | Help Desk | Service Desk | Technical Support Specialist | Product Support | Support Engineer Trainee | Junior QA | Manual QA Trainee | QA Intern\n\n"
                "ОЖИДАНИЯ ПО ФОРМАТУ\n"
                "Remote international или hybrid/office во Вьетнаме. Без оформления по ТК РФ и без российского юрлица; только прозрачный международный legal/contractor contract, доступный резиденту Вьетнама.\n\n"
                "СИЛЬНЫЕ СТОРОНЫ\n"
                "- Troubleshooting: собрать симптомы, воспроизвести шаги, отделить пользовательскую проблему от продуктовой и понятно эскалировать.\n"
                "- User support: спокойная коммуникация, терпение, работа по скриптам, базам знаний и регламентам.\n"
                "- QA basics: ручные проверки, простая регрессия, скриншоты, expected/actual result, воспроизводимые bug reports и обновление checklist.\n"
                "- Трек развития: help desk fundamentals, VPN/user access support, SQL basics, Postman basics, затем Java/Swift и app-development fundamentals.\n\n"
                "HARD SKILLS\n"
                "IT Support — Basic+ | Help Desk — Basic+ | Technical Support — Basic | Troubleshooting — Basic+ | Ticket systems — Basic | "
                "Manual QA basics — Basic | Bug reports — Basic+ | Test documentation — Basic | Postman — Basic | SQL — Basic | "
                "Chrome DevTools — Basic | VPN clients — Basic+ | Windows/macOS user support — Basic+ | Google Workspace / MS Office — Basic+\n\n"
                "SOFT SKILLS\n"
                "Терпение и спокойная коммуникация — Advanced | Внимательность к инструкциям — Advanced | Готовность учиться — Advanced | "
                "Надёжная рутинная работа — Advanced | Понятная эскалация — Basic+ | Командная коммуникация — Basic+\n\n"
                "ЯЗЫКИ\n"
                "Русский — Native | Английский — A1, активно улучшается; лучший старт — письменная/asynchronous коммуникация или русскоязычный onboarding."
            ),
        },
    }


def ensure_qa_resume_pack(query, execute, user_id: int, *, force: bool = False) -> int:
    if not force and public_release.read_setting(query, user_id, "qa_resume_pack_version", "") == QA_RESUME_PACK_VERSION:
        return 0
    candidates = query("SELECT id,name FROM candidates WHERE user_id=? ORDER BY id", (user_id,))
    updated = 0
    now = now_iso()
    for row in ([] if candidates.empty else candidates.to_dict("records")):
        kind = _candidate_kind(str(row.get("name") or ""))
        if not kind:
            continue
        candidate_id = int(row["id"])
        pack = _resume_pack(kind)
        profile = pack["profile"]
        execute(
            """UPDATE candidates SET target_title=?,english_level=?,desired_countries=?,salary_min=?,notes=?,hard_exclude=?,hard_require=?,
              preferred_regions=?,preferred_cities=?,preferred_companies=?,priority_titles=? WHERE id=? AND user_id=?""",
            (
                profile["target_title"], profile["english_level"], profile["desired_countries"], profile["salary_min"],
                profile["notes"], profile["hard_exclude"], profile["hard_require"], profile["preferred_regions"],
                profile["preferred_cities"], profile["preferred_companies"], profile["priority_titles"], candidate_id, user_id,
            ),
        )
        execute("DELETE FROM skills WHERE user_id=? AND candidate_id=?", (user_id, candidate_id))
        for skill in pack["skills"]:
            execute("INSERT INTO skills(user_id,candidate_id,skill) VALUES(?,?,?)", (user_id, candidate_id, skill))
        public_links = {
            "qa_candidate": {
                "GitHub": "https://github.com/example",
                "Portfolio": "https://example.com/qa-portfolio",
            },
            "support_candidate": {"GitHub": "https://github.com/example"},
        }[kind]
        for platform, url in public_links.items():
            existing_link = query(
                "SELECT id FROM social_links WHERE user_id=? AND candidate_id=? AND lower(platform)=lower(?) ORDER BY id LIMIT 1",
                (user_id, candidate_id, platform),
            )
            if existing_link.empty:
                execute(
                    "INSERT INTO social_links(user_id,candidate_id,platform,url,show_global,show_foreign,show_ru,notes) VALUES(?,?,?,?,1,1,1,?)",
                    (user_id, candidate_id, platform, url, "public application contact"),
                )
            else:
                execute(
                    "UPDATE social_links SET url=?,show_global=1,show_foreign=1,show_ru=1 WHERE id=? AND user_id=?",
                    (url, int(existing_link.iloc[0]["id"]), user_id),
                )
        for language, content in pack["resumes"].items():
            title = content.splitlines()[0]
            existing = query(
                "SELECT id FROM resumes WHERE user_id=? AND candidate_id=? AND language=? ORDER BY id DESC LIMIT 1",
                (user_id, candidate_id, language),
            )
            if existing.empty:
                execute(
                    "INSERT INTO resumes(user_id,candidate_id,language,title,content,updated_at) VALUES(?,?,?,?,?,?)",
                    (user_id, candidate_id, language, title, content, now),
                )
            else:
                execute(
                    "UPDATE resumes SET title=?,content=?,updated_at=? WHERE id=? AND user_id=?",
                    (title, content, now, int(existing.iloc[0]["id"]), user_id),
                )
            updated += 1
    if updated:
        public_release.put_setting(query, execute, user_id, "qa_resume_pack_version", QA_RESUME_PACK_VERSION)
        public_release.put_setting(query, execute, user_id, "search_max_age_days", "30")
    return updated


@app.post("/api/candidates/resumes/vietnam-it")
def refresh_vietnam_it_resumes(user_id: int = Depends(current_user)) -> dict[str, Any]:
    with db.transaction() as (query, execute):
        if query("SELECT id FROM candidates WHERE user_id=? LIMIT 1", (user_id,)).empty:
            raise HTTPException(status_code=400, detail="Сначала создайте профили кандидатов.")
        updated = ensure_qa_resume_pack(query, execute, user_id, force=True)
    return {"updated": updated}


@app.post("/api/candidates/{candidate_id}/photo")
async def upload_resume_photo(
    candidate_id: int,
    photo: UploadFile = File(...),
    language: Literal["RU", "EN", "SR", "OTHER"] = Form("RU"),
    user_id: int = Depends(current_user),
) -> dict[str, Any]:
    """Save a small private photo, never a public asset or a third-party upload."""
    require_candidate(candidate_id, user_id)
    media_type = (photo.content_type or "").lower()
    if media_type not in {"image/jpeg", "image/png", "image/webp"}:
        raise HTTPException(status_code=400, detail="Подойдёт JPG, PNG или WebP.")
    raw = await photo.read()
    if not raw or len(raw) > 2 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Фото должно быть не больше 2 МБ.")
    encoded = f"data:{media_type};base64,{base64.b64encode(raw).decode('ascii')}"
    resume = db.query(
        "SELECT id FROM resumes WHERE user_id=? AND candidate_id=? AND language=? ORDER BY id DESC LIMIT 1",
        (user_id, candidate_id, language),
    )
    if resume.empty:
        db.execute(
            "INSERT INTO resumes(user_id,candidate_id,language,title,content,photo_data,updated_at) VALUES(?,?,?,?,?,?,?)",
            (user_id, candidate_id, language, "Основное резюме", "Фото добавлено. Добавьте текст резюме для подбора.", encoded, now_iso()),
        )
    else:
        db.execute("UPDATE resumes SET photo_data=?,updated_at=? WHERE id=? AND user_id=?", (encoded, now_iso(), int(resume.iloc[0]["id"]), user_id))
    return {"ok": True, "photo_data": encoded}


@app.post("/api/resumes/transform")
def transform_resume(body: ResumeTransformRequest, user_id: int = Depends(current_user)) -> dict[str, str]:
    """A transparent local normalization step; the resume is not sent to an AI provider."""
    cleaned = "\n".join(line.strip() for line in body.content.splitlines() if line.strip())
    if body.candidate_id is None:
        return {
            "content": cleaned,
            "message": "Черновик структурирован локально. Перед отправкой проверьте факты и тон.",
        }
    candidate = db.query(
        "SELECT name,target_title,english_level,desired_countries,salary_min,notes FROM candidates WHERE id=? AND user_id=?",
        (body.candidate_id, user_id),
    )
    if candidate.empty:
        raise HTTPException(status_code=404, detail="Профиль не найден.")
    profile = candidate.iloc[0].to_dict()
    skills = db.query("SELECT skill FROM skills WHERE user_id=? AND candidate_id=? ORDER BY skill", (user_id, body.candidate_id))
    skill_line = ", ".join(str(item) for item in ([] if skills.empty else skills["skill"].tolist())[:12])
    heading = "Адаптированная версия резюме" if body.language == "RU" else "Tailored resume draft"
    target = str(profile.get("target_title") or "специалист")
    location = str(profile.get("desired_countries") or "")
    context = [
        heading,
        f"Цель: {target}",
        f"География: {location}" if location else "",
        f"Ключевые навыки: {skill_line}" if skill_line else "",
        "",
        cleaned,
    ]
    return {
        "content": "\n".join(line for line in context if line is not None).strip(),
        "message": "Резюме адаптировано под настройки кандидата локально: факты не добавлялись и не отправлялись внешним сервисам.",
    }


@app.post("/api/candidates/{candidate_id}/serbia-preset")
def apply_serbia_preset(
    candidate_id: int,
    body: SerbiaPresetRequest,
    user_id: int = Depends(current_user),
) -> dict[str, Any]:
    require_candidate(candidate_id, user_id)
    qa_candidate = body.kind == "qa_candidate"
    preset = {
        "target_title": "Manual QA Engineer" if qa_candidate else "Junior IT Support / Manual QA",
        "english_level": "A1",
        "desired_countries": "Сербия: Белград / Нови-Сад; удалённо из Сербии; международные вакансии, доступные при проживании в Сербии",
        "salary_min": 1300 if qa_candidate else 1100,
        "hard_exclude": "Вьетнам; Sber; офис Москва; релокация Санкт-Петербург" if qa_candidate else "Senior; Lead; Principal; Manager; Director; Architect; студент; Вьетнам; Sber; офис Москва",
        "hard_require": "Белград/Нови-Сад или remote international; уточнить релокацию, компенсацию, технику и язык команды",
        "preferred_regions": "Сербия; remote international; Европа",
        "preferred_cities": "Белград; Нови-Сад",
        "preferred_companies": "",
        "priority_titles": "Manual QA Engineer; QA Engineer; Software Tester" if qa_candidate else "Junior IT Support; Help Desk; Junior QA; Trainee QA",
        "notes": (
            "Переезд в Белград в процессе, старт через 4–6 недель. Английский A1; желательно уточнить возможность daily на русском."
            if qa_candidate else
            "Документы для Белграда в процессе, старт через 4–6 недель. Первая IT-роль: support/help desk/junior QA; remote предпочтительно, офис/гибрид Белград–Нови-Сад допустим. Английский A1; желательно уточнить возможность daily на русском."
        ),
        "skills": (
            ["Manual QA", "API testing", "Postman", "SQL", "Regression testing", "Test documentation", "Fintech"]
            if qa_candidate else ["IT Support", "Help Desk", "Manual QA", "Ticket systems", "User support", "Basic troubleshooting"]
        ),
    }
    with db.transaction() as (query, execute):
        execute(
            """UPDATE candidates SET target_title=?,english_level=?,desired_countries=?,salary_min=?,notes=?,hard_exclude=?,hard_require=?,
              preferred_regions=?,preferred_cities=?,preferred_companies=?,priority_titles=? WHERE id=? AND user_id=?""",
            (preset["target_title"], preset["english_level"], preset["desired_countries"], preset["salary_min"], preset["notes"], preset["hard_exclude"], preset["hard_require"], preset["preferred_regions"], preset["preferred_cities"], preset["preferred_companies"], preset["priority_titles"], candidate_id, user_id),
        )
        existing_skills = query("SELECT skill FROM skills WHERE user_id=? AND candidate_id=?", (user_id, candidate_id))
        known_skills = {str(value).strip().lower() for value in ([] if existing_skills.empty else existing_skills["skill"].tolist())}
        for skill in preset["skills"]:
            if skill.lower() not in known_skills:
                execute("INSERT INTO skills(user_id,candidate_id,skill) VALUES(?,?,?)", (user_id, candidate_id, skill))
        # Keep submitted/approved history, but remove the noisy current feed.
        execute(
            "UPDATE vacancies SET status='archived' WHERE user_id=? AND candidate_id=? AND status NOT IN ('sent','approved','applied','archived')",
            (user_id, candidate_id),
        )
        execute(
            "UPDATE live_job_index SET active=0 WHERE user_id=? AND candidate_id=? AND vacancy_id IN (SELECT id FROM vacancies WHERE user_id=? AND candidate_id=? AND status='archived')",
            (user_id, candidate_id, user_id, candidate_id),
        )
        row = query("""SELECT id,name,target_title,english_level,desired_countries,salary_min,notes,hard_exclude,hard_require,
                   preferred_regions,preferred_cities,preferred_companies,priority_titles,contact_email,cover_tone,cover_length,manual_review
                   FROM candidates WHERE id=? AND user_id=?""", (candidate_id, user_id))
    return json_safe(row.iloc[0].to_dict())


@app.post("/api/candidates/{candidate_id}/vietnam-preset")
def apply_vietnam_preset(
    candidate_id: int,
    body: VietnamPresetRequest,
    user_id: int = Depends(current_user),
) -> dict[str, Any]:
    """Apply the current Vietnam strategy without inventing experience facts."""
    require_candidate(candidate_id, user_id)
    qa_candidate = body.kind == "qa_candidate"
    preset = {
        "target_title": "Manual QA / API QA / Product Support" if qa_candidate else "Junior IT Support / Help Desk / QA Trainee",
        "english_level": "A1",
        "desired_countries": "Вьетнам: Дананг; удалённо из Вьетнама; remote worldwide; hybrid/office во Вьетнаме; русскоязычные международные команды",
        "salary_min": 1000 if qa_candidate else 700,
        "hard_exclude": (
            "Russia-only; офис или гибрид в любом городе РФ; оформление по ТК РФ; российское юрлицо; Sber; Senior-only; Lead; Head; Director; обязательный B2 English; C1 English; букмекерская компания; gambling"
            if qa_candidate else
            "Russia-only; офис или гибрид в любом городе РФ; оформление по ТК РФ; российское юрлицо; Sber; Senior; Lead; Head; Manager; Director; Architect; Principal; обязательный B2 English; C1 English; букмекерская компания; gambling"
        ),
        "hard_require": "русскоязычная коммуникация или русскоязычный onboarding; Вьетнам или international remote, доступно из Вьетнама; без ТК РФ и российского юрлица; прозрачный international legal/contractor contract; указаны формат, график, оплата и контакт; технику и компенсацию уточнить",
        "preferred_regions": "Вьетнам; Юго-Восточная Азия; Казахстан; Армения; Грузия; Кипр; remote worldwide",
        "preferred_cities": "Дананг; Da Nang; Ханой; Хошимин",
        "preferred_companies": "русскоязычные международные команды; remote-first компании; продуктовые команды с поддержкой пользователей",
        "priority_titles": (
            "Manual QA Engineer; API QA; QA Engineer; QA Specialist; Software Tester; Manual Tester; Product Support Specialist; Technical Support Specialist; User Support Specialist"
            if qa_candidate else
            "Junior IT Support; Help Desk; Service Desk; Technical Support Specialist; Product Support; Support Engineer trainee; Junior QA; Manual QA; Trainee QA; QA Intern"
        ),
        "notes": (
            "Позиционирование для рекрутера: QA/support специалист с банковским доменным опытом, Postman/API, SQL, регрессией, документацией и годом клиентской поддержки. Переезд/работа из Дананга, Вьетнам. Английский базовый и улучшается; стартовый фокус — русскоязычные международные команды, remote/worldwide или Vietnam hybrid/office."
            if qa_candidate else
            "Позиционирование для рекрутера: entry-level IT support/help desk/QA trainee кандидат с фокусом на troubleshooting, пользовательскую поддержку, документацию, базовые QA-проверки и готовность к наставничеству. Переезд/работа из Дананга, Вьетнам. Английский базовый и улучшается; стартовый фокус — русскоязычные международные команды, remote/worldwide или Vietnam hybrid/office."
        ),
        "skills": (
            ["Manual QA", "API testing", "Postman", "SQL", "Regression testing", "Test documentation", "Fintech", "Mobile testing", "User support", "Bug reports"]
            if qa_candidate else
            ["IT Support", "Help Desk", "Technical Support", "Manual QA basics", "Ticket systems", "User support", "Basic troubleshooting", "Bug reports", "Documentation"]
        ),
    }
    with db.transaction() as (query, execute):
        execute(
            """UPDATE candidates SET target_title=?,english_level=?,desired_countries=?,salary_min=?,notes=?,hard_exclude=?,hard_require=?,
              preferred_regions=?,preferred_cities=?,preferred_companies=?,priority_titles=? WHERE id=? AND user_id=?""",
            (preset["target_title"], preset["english_level"], preset["desired_countries"], preset["salary_min"], preset["notes"], preset["hard_exclude"], preset["hard_require"], preset["preferred_regions"], preset["preferred_cities"], preset["preferred_companies"], preset["priority_titles"], candidate_id, user_id),
        )
        existing = query("SELECT skill FROM skills WHERE user_id=? AND candidate_id=?", (user_id, candidate_id))
        known = {str(value).strip().lower() for value in ([] if existing.empty else existing["skill"].tolist())}
        for skill in preset["skills"]:
            if skill.lower() not in known:
                execute("INSERT INTO skills(user_id,candidate_id,skill) VALUES(?,?,?)", (user_id, candidate_id, skill))
        execute(
            "UPDATE vacancies SET status='archived' WHERE user_id=? AND candidate_id=? AND status NOT IN ('sent','approved','applied','archived')",
            (user_id, candidate_id),
        )
        execute(
            "UPDATE live_job_index SET active=0 WHERE user_id=? AND candidate_id=? AND vacancy_id IN (SELECT id FROM vacancies WHERE user_id=? AND candidate_id=? AND status='archived')",
            (user_id, candidate_id, user_id, candidate_id),
        )
        public_release.put_setting(query, execute, user_id, "search_base_country", "Vietnam")
        public_release.put_setting(query, execute, user_id, "search_vietnam_hybrid", "1")
        # Do not leave a legacy Serbian toggle behind: it is confusing in the
        # UI even though the current base country is already Vietnam.
        public_release.put_setting(query, execute, user_id, "search_serbia_hybrid", "0")
        public_release.put_setting(query, execute, user_id, "search_stop_countries", "Россия; РФ")
        row = query("""SELECT id,name,target_title,english_level,desired_countries,salary_min,notes,hard_exclude,hard_require,
                   preferred_regions,preferred_cities,preferred_companies,priority_titles,contact_email,cover_tone,cover_length,manual_review
                   FROM candidates WHERE id=? AND user_id=?""", (candidate_id, user_id))
    return json_safe(row.iloc[0].to_dict())


@app.post("/api/vacancies/cleanup")
def cleanup_vacancies(body: VacancyCleanupRequest, user_id: int = Depends(current_user)) -> dict[str, Any]:
    protected = "'sent','approved','applied','archived'"
    if body.mode == "reset":
        clause = f"status NOT IN ({protected})"
        message = "Текущая подборка перемещена в архив. Одобренные и отправленные отклики сохранены."
    elif body.mode == "ignored":
        clause = "status IN ('later','skip')"
        message = "Отложенные и скрытые карточки перенесены в архив."
    elif body.mode == "inactive":
        clause = f"status NOT IN ({protected}) AND id IN (SELECT vacancy_id FROM live_job_index WHERE user_id=? AND active=0)"
        message = "Неактивные карточки перенесены в архив."
    else:
        clause = f"status NOT IN ({protected}) AND (posted_at='' OR posted_at < ?)"
        message = "Устаревшие карточки перенесены в архив."
    parameters: tuple[Any, ...]
    if body.mode == "inactive":
        parameters = (user_id, user_id)
    elif body.mode == "all":
        from datetime import timedelta
        parameters = (user_id, (datetime.now(UTC) - timedelta(days=30)).date().isoformat())
    else:
        parameters = (user_id,)
    before = db.query(f"SELECT id FROM vacancies WHERE user_id=? AND {clause}", parameters)
    ids = [] if before.empty else [int(item) for item in before["id"].tolist()]
    if ids:
        marks = ",".join("?" for _ in ids)
        db.execute(f"UPDATE vacancies SET status='archived' WHERE user_id=? AND id IN ({marks})", (user_id, *ids))
        db.execute(f"UPDATE live_job_index SET active=0 WHERE user_id=? AND vacancy_id IN ({marks})", (user_id, *ids))
    return {"archived": len(ids), "message": message}


@app.post("/api/applications/prepare")
def prepare_applications(body: ApplicationPrepareRequest, user_id: int = Depends(current_user)) -> dict[str, Any]:
    """Prepare reviewable application drafts only.  This endpoint never sends email."""
    preferences = application_preferences_payload(user_id)
    daily_limit = int(preferences["daily_limit"])
    today = now_iso()[:10]
    created_today = db.query("SELECT COUNT(*) count FROM applications WHERE user_id=? AND substr(created_at,1,10)=?", (user_id, today))
    used = 0 if created_today.empty else int(created_today.iloc[0]["count"] or 0)
    if used >= daily_limit:
        raise HTTPException(status_code=429, detail=f"Лимит черновиков на сегодня: {daily_limit}. Письма не отправлялись.")
    marks = ",".join("?" for _ in body.vacancy_ids)
    rows = db.query(
        f"SELECT id,candidate_id,company,position,source,link FROM vacancies WHERE user_id=? AND id IN ({marks})",
        (user_id, *body.vacancy_ids),
    )
    prepared: list[int] = []
    for item in ([] if rows.empty else rows.to_dict("records")):
        if used + len(prepared) >= daily_limit:
            break
        existing = db.query("SELECT id FROM applications WHERE user_id=? AND vacancy_id=?", (user_id, int(item["id"])))
        if existing.empty:
            draft = build_application_draft(user_id, int(item["id"]), preferences=preferences)
            db.execute(
                """INSERT INTO applications(user_id,candidate_id,vacancy_id,company,position,source,link,method,status,notes,
                   cover_letter,recipient_email,subject,resume_id,resume_language,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (user_id, item["candidate_id"], item["id"], item["company"], item["position"], item["source"], item["link"], "review", "ready", "Черновик: отправка требует финального подтверждения пользователя.", draft["cover_letter"], draft["recipient_email"], draft["subject"], draft["resume"]["id"] if draft["resume"] else None, draft["resume"]["language"] if draft["resume"] else "", now_iso()),
            )
            prepared.append(int(item["id"]))
    return {"prepared": prepared, "sent": 0, "message": "Черновики готовы к вашей проверке. Письма и отклики не отправлялись."}


def application_preferences_payload(user_id: int) -> dict[str, Any]:
    """Return safe, owner-only drafting preferences; no mailbox credentials are stored."""
    default_email_frame = db.query("SELECT email FROM users WHERE id=?", (user_id,))
    default_email = "" if default_email_frame.empty else str(default_email_frame.iloc[0]["email"] or "")
    read = lambda key, default: public_release.read_setting(db.query, user_id, key, default)
    tone = read("application_tone", "formal")
    length = read("application_length", "compact")
    try:
        prepared_today = int(db.query("SELECT COUNT(*) count FROM applications WHERE user_id=? AND substr(created_at,1,10)=?", (user_id, now_iso()[:10])).iloc[0]["count"] or 0)
    except Exception:
        prepared_today = 0
    pro_enabled = read("application_pro_enabled", "0") == "1"
    return {
        "tone": tone if tone in {"formal", "friendly"} else "formal",
        "length": length if length in {"compact", "detailed"} else "compact",
        "include_certificates": read("application_certificates", "1") != "0",
        "include_achievements": read("application_achievements", "1") != "0",
        "from_email": read("application_from_email", default_email) or default_email,
        "daily_limit": 50 if pro_enabled else 30,
        "pro_enabled": pro_enabled,
        "prepared_today": prepared_today,
    }


def russian_public_name(name: str) -> str:
    kind = _candidate_kind(name)
    if kind == "qa_candidate":
        return "Демо-кандидат QA"
    if kind == "support_candidate":
        return "Демо-кандидат Support"
    return str(name or "").strip() or "Кандидат"


def english_public_name(name: str) -> str:
    kind = _candidate_kind(name)
    if kind == "qa_candidate":
        return "Demo QA Candidate"
    if kind == "support_candidate":
        return "Demo Support Candidate"
    return str(name or "").strip() or "Candidate"


def extract_telegram_contact(text: str) -> str:
    for match in re.findall(r"@[A-Za-z0-9_]{4,32}", text or ""):
        lowered = match.lower()
        if lowered not in {"@gmail", "@mail", "@email"}:
            return match
    link = re.search(r"(?:https?://)?t\.me/([A-Za-z0-9_]{4,32})", text or "", re.I)
    return f"@{link.group(1)}" if link else ""


def extract_application_instructions(text: str) -> str:
    clean = re.sub(r"\r", "", str(text or ""))
    patterns = (
        r"(?:как откликнуться|как откликаться|отклик|для отклика|при отклике|напишите немного о себе)[:\s]*([\s\S]{0,900})",
        r"(?:напишите|укажите|расскажите)[^.\n]{0,120}(?:о себе|опыт|vpn|почему)[\s\S]{0,700}",
    )
    for pattern in patterns:
        match = re.search(pattern, clean, re.I)
        if not match:
            continue
        chunk = match.group(1) if match.lastindex else match.group(0)
        chunk = re.split(r"(?:\n\s*\n|для связи|контакт|telegram|телеграм|@)", chunk, maxsplit=1, flags=re.I)[0]
        chunk = re.sub(r"\s+", " ", chunk).strip(" .:-")
        if len(chunk) >= 25:
            return chunk[:700]
    return ""


def build_telegram_reply(
    *,
    person: str,
    position: str,
    company: str,
    instructions: str,
    tone: str,
) -> str:
    is_qa_candidate = _candidate_kind(person) == "qa_candidate"
    greeting = "Здравствуйте!" if tone == "formal" else "Добрый день!"
    position_text = re.sub(r"\s+", " ", position or "вакансию").strip(" .")
    if is_qa_candidate:
        body = [
            f"{greeting} Меня зовут {person}.",
            "",
            f"Откликаюсь на вакансию «{position_text}».",
            "Есть опыт поддержки пользователей: год работала в контактном центре на входящей линии, принимала звонки, помогала клиентам и взаимодействовала со смежными отделами. С VPN знакома на пользовательском уровне, готова быстро разобраться в вашем сервисе, базе знаний и регламентах.",
            "Хочу работать с вами, потому что формат с небольшой ежедневной нагрузкой подходит для аккуратного совмещения, а задачи поддержки пользователей мне близки.",
        ]
    else:
        body = [
            f"{greeting} Меня зовут {person}.",
            "",
            f"Откликаюсь на вакансию «{position_text}».",
            "Есть опыт и интерес к задачам junior IT support/help desk: разбор обращений, аккуратная коммуникация, фиксация проблем и работа по инструкциям. С VPN знаком на пользовательском уровне и готов быстро изучить ваш сервис и порядок поддержки.",
            "Хочу работать с вами, потому что формат с небольшой ежедневной нагрузкой подходит для совмещения с обучением и практикой в IT-поддержке.",
        ]
    if instructions:
        body.append(f"В отклике учитываю ваши пункты: {instructions}.")
    body.extend(("", "Готова обсудить график и тестовое задание." if is_qa_candidate else "Готов обсудить график и тестовое задание."))
    if company and company.lower() not in {"компания не указана", "unknown"}:
        body[2] = f"Откликаюсь на вакансию «{position_text}» в {company}."
    return "\n".join(body)


def _join_clean(values: list[Any], limit: int = 8) -> str:
    return ", ".join(str(value).strip() for value in values if str(value).strip())[:900]


def _letter_skill_summary(values: list[Any], limit: int = 5) -> str:
    cleaned = []
    for value in values:
        label = re.split(r"\s[-—]\s", str(value or "").strip(), maxsplit=1)[0].strip()
        if label and label.lower() not in {item.lower() for item in cleaned}:
            cleaned.append(label)
        if len(cleaned) >= limit:
            break
    return ", ".join(cleaned)


LETTER_SKILL_ALIASES = {
    "manual qa": ("manual qa", "manual testing", "software tester", "тестиров", "ручн"),
    "manual qa basics": ("manual qa", "manual testing", "software tester", "тестиров", "qa trainee"),
    "api testing": ("api", "rest", "postman", "swagger", "openapi"),
    "postman": ("postman", "api testing", "rest api"),
    "sql": ("sql", "database", "баз дан", "postgres"),
    "postgresql": ("postgres", "postgresql", "sql", "database"),
    "regression testing": ("regression", "регрес"),
    "smoke testing": ("smoke", "смоук"),
    "bug reports": ("bug report", "баг-репорт", "дефект", "issue report"),
    "test documentation": ("test documentation", "test case", "checklist", "документац", "тест-кейс", "чек-лист"),
    "swagger/openapi": ("swagger", "openapi", "api documentation"),
    "chrome devtools": ("devtools", "browser tools", "chrome"),
    "mobile/web testing": ("mobile", "web testing", "ios", "android", "мобильн"),
    "it support": ("it support", "technical support", "help desk", "service desk", "техподдерж"),
    "help desk": ("help desk", "service desk", "it support", "support specialist"),
    "technical support": ("technical support", "it support", "product support", "техподдерж"),
    "user support": ("user support", "customer support", "client support", "поддержк", "обращен"),
    "troubleshooting": ("troubleshoot", "diagnos", "incident", "диагност", "неисправност"),
    "ticket systems": ("ticket", "jira", "zendesk", "service desk", "тикет"),
    "vpn clients": ("vpn", "network access", "удалённ", "remote access"),
    "windows/macos user support": ("windows", "macos", "desktop support", "workstation"),
}


def _cover_source_text(item: dict[str, Any]) -> str:
    snapshot_value = item.get("source_snapshot")
    if isinstance(snapshot_value, dict):
        snapshot = snapshot_value
    else:
        try:
            snapshot = json.loads(str(snapshot_value or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            snapshot = {}
    snapshot_text = " ".join(str(snapshot.get(key) or "") for key in ("description", "tags", "location", "job_type", "work_type"))
    return " ".join(str(item.get(key) or "") for key in (
        "position", "title", "company", "client", "remote_location", "location",
        "salary_text", "pay_text", "work_type", "work_format", "category", "strengths",
        "requirements_note", "final_salary_advice", "description",
    )) + " " + snapshot_text


def _matched_letter_skills(skills: list[Any], item: dict[str, Any], limit: int = 6) -> tuple[list[str], bool]:
    blob = _cover_source_text(item).lower()
    matched: list[str] = []
    available: list[str] = []
    for raw in skills:
        label = re.split(r"\s[-—]\s", str(raw or "").strip(), maxsplit=1)[0].strip()
        if not label or label.lower() in {value.lower() for value in available}:
            continue
        available.append(label)
        lowered = label.lower()
        aliases = LETTER_SKILL_ALIASES.get(lowered, (lowered,))
        if any(alias in blob for alias in aliases):
            matched.append(label)
        if len(matched) >= limit:
            break
    if matched:
        return matched, True
    return available[:limit], False


def _candidate_public_contacts(email: str, social_links: list[dict[str, Any]]) -> list[tuple[str, str]]:
    contacts: list[tuple[str, str]] = []
    if email.strip():
        contacts.append(("Email", email.strip()))
    preferred = {"github": 0, "portfolio": 1, "портфолио": 1}
    selected: list[tuple[int, str, str]] = []
    for link in social_links:
        platform = str(link.get("platform") or "").strip()
        url = str(link.get("url") or "").strip()
        key = platform.lower()
        if url and key in preferred:
            selected.append((preferred[key], "GitHub" if key == "github" else "Portfolio", url))
    for _order, label, url in sorted(selected):
        if (label, url) not in contacts:
            contacts.append((label, url))
    return contacts


def _cover_compensation_range(item: dict[str, Any], candidate_salary: int) -> tuple[int, int]:
    advice = str(item.get("final_salary_advice") or "")
    salary_text = str(item.get("salary_text") or item.get("pay_text") or "")
    combined = f"{advice} {salary_text}".replace(",", "")
    ranges = re.findall(r"\$\s*(\d{3,5})\s*[–—-]\s*\$?\s*(\d{3,5})", combined)
    low, high = (0, 0)
    for raw_low, raw_high in ranges:
        candidate_low, candidate_high = int(raw_low), int(raw_high)
        if 300 <= candidate_low <= 20000 and candidate_low <= candidate_high <= 25000:
            low, high = candidate_low, candidate_high
            break
    score = int(item.get("score") or 0)
    if not low:
        low = max(int(candidate_salary or 0), 700)
        high = low + (700 if low >= 1000 else 500)
        if score >= 85:
            high += 200
    low = max(low, int(candidate_salary or 0))
    high = max(high, low + (400 if low >= 1000 else 300))
    return low, high


def _cover_questions(item: dict[str, Any], candidate_name: str, candidate_salary: int, language: str) -> list[str]:
    text = _cover_source_text(item).lower()
    work_type = " ".join(str(item.get(key) or "") for key in ("work_type", "work_format", "category", "item_type")).lower()
    moonlight = bool(item.get("moonlight_compatible")) or any(
        marker in f"{work_type} {text}" for marker in ("part-time", "part time", "freelance", "project-based", "проект", "подработ", "совмещ")
    )
    part_time = moonlight or any(marker in work_type for marker in ("contract", "частичн"))
    equipment_mentioned = any(marker in text for marker in ("laptop", "macbook", "equipment", "ноутбук", "техник", "оборудован"))
    relocation = any(marker in text for marker in ("relocat", "visa sponsor", "work permit", "переезд", "релокац", "виза", "vietnam", "вьетнам", "da nang", "дананг"))
    low, high = _cover_compensation_range(item, candidate_salary)
    displayed_salary = str(item.get("salary_text") or item.get("pay_text") or "").strip()
    disclosed = bool(displayed_salary and not re.search(r"не указан|not specified|not disclosed|none", displayed_salary, re.I))
    own_low, own_high = low + 150, high + 250
    kind = _candidate_kind(candidate_name)
    ready_ru = "Готов" if kind == "support_candidate" else "Готова"
    if language == "EN":
        questions: list[str] = []
        if disclosed:
            questions.append(f"The vacancy lists {displayed_salary}; could you confirm that this range is still current?")
        if part_time:
            hourly_low, hourly_high = max(8, round(low / 160)), max(12, round(high / 120))
            equipment_note = "including company-provided equipment" if equipment_mentioned else f"with an adjustment to about ${hourly_low + 2}–${hourly_high + 3}/hour when using my own equipment"
            questions.append(f"My target for the expected workload is ${hourly_low}–${hourly_high}/hour, {equipment_note}. Would this range work for you?")
            questions.append("Would the cooperation be covered by a written services, project, or part-time contract?")
            questions.append("Can this role be combined with another job, and is there any exclusivity restriction?")
        else:
            equipment_note = "with company-provided equipment" if equipment_mentioned else f"with company-provided equipment, or ${own_low}–${own_high} gross/month when using my own equipment"
            questions.append(f"My compensation target is ${low}–${high} gross/month {equipment_note}. Would this range be within budget?")
            questions.append("Is formal employment or a written international employment/contractor agreement available?")
        if relocation:
            questions.append("Is a relocation package or document support for Vietnam available? My documents are in progress, I expect to be in Vietnam in early September, and I am ready to start remotely before the move or immediately after arrival.")
        return questions
    questions = []
    if disclosed:
        questions.append(f"В объявлении указана вилка {displayed_salary}; подскажите, пожалуйста, она ещё актуальна?")
    if part_time:
        hourly_low, hourly_high = max(8, round(low / 160)), max(12, round(high / 120))
        equipment_note = "при предоставлении рабочей техники" if equipment_mentioned else f"или около ${hourly_low + 2}–${hourly_high + 3}/час при работе на собственной технике"
        questions.append(f"Мой ориентир при такой загрузке — ${hourly_low}–${hourly_high}/час {equipment_note}. Подходит ли вам такой диапазон?")
        questions.append("Предусмотрен ли письменный договор на оказание услуг, проектную или частичную работу?")
        questions.append("Можно ли совмещать эту работу с другой занятостью и нет ли требования об эксклюзивности?")
    else:
        equipment_note = "с учётом предоставления рабочей техники" if equipment_mentioned else f"при предоставлении рабочей техники; при работе на собственной технике — ${own_low}–${own_high} gross/месяц"
        questions.append(f"Мой ориентир по компенсации — ${low}–${high} gross/месяц {equipment_note}. Рассматриваете ли вы такой диапазон?")
        questions.append("Возможно ли официальное оформление или письменный международный employment/contractor contract?")
    if relocation:
        questions.append(f"Предусмотрен ли релокационный пакет или помощь с документами для Вьетнама? Сейчас документы в процессе, ориентировочно в первых числах сентября буду во Вьетнаме; {ready_ru.lower()} начать удалённо до переезда или сразу после приезда.")
    return questions


def _compose_cover_variants(
    item: dict[str, Any],
    candidate: dict[str, Any],
    skills: list[Any],
    social_links: list[dict[str, Any]],
    language: str,
) -> tuple[str, str, str]:
    candidate_name = str(candidate.get("name") or "")
    person = english_public_name(candidate_name) if language == "EN" else russian_public_name(candidate_name)
    position = str(item.get("position") or item.get("title") or "позицию").strip()
    company = str(item.get("company") or item.get("client") or "компании").strip()
    kind = _candidate_kind(candidate_name)
    is_support_candidate = kind == "support_candidate"
    matched_skills, has_direct_matches = _matched_letter_skills(skills, item)
    skill_text = ", ".join(matched_skills)
    questions = _cover_questions(item, candidate_name, int(candidate.get("salary_min") or 0), language)
    contacts = _candidate_public_contacts(str(candidate.get("contact_email") or ""), social_links)
    if language == "EN":
        profile = (
            "I am an entry-level IT Support / Help Desk / QA candidate with practical skills in user support, troubleshooting, documentation, and careful manual checks."
            if is_support_candidate else
            "I am a Manual and API QA specialist with banking and fintech experience in API checks, SQL data validation, regression testing, bug reporting, and user support."
        )
        skill_intro = "The closest skill match for this role is" if has_direct_matches else "My relevant working toolkit includes"
        skill_sentence = f"{skill_intro}: {skill_text}." if skill_text else "I work carefully with documented tasks, user scenarios, and reproducible issue reports."
        question_block = "\n".join(questions)
        contact_block = "Contacts:\n" + "\n".join(f"{label}: {value}" for label, value in contacts)
        formal = (
            f"Hello!\n\nMy name is {person}. I am applying for the {position} position at {company}. "
            f"{profile}\n\n{skill_sentence}\n\n{question_block}\n\n{contact_block}\n\nKind regards,\n{person}"
        )
        friendly = (
            f"Hi!\n\nMy name is {person}. The {position} role at {company} caught my attention. "
            f"{profile}\n\n{skill_sentence} I would be happy to share practical examples and complete a relevant test task.\n\n"
            f"{question_block}\n\n{contact_block}\n\nBest regards,\n{person}"
        )
        detailed = (
            f"Hello!\n\nMy name is {person}, and I would like to apply for the {position} position at {company}.\n\n"
            f"{profile}\n\n{skill_sentence} I can explain how I use these skills in documented checks, issue reproduction, user-facing scenarios, and clear handoffs to the team.\n\n"
            f"Before moving forward, I would like to clarify a few practical points:\n{question_block}\n\n{contact_block}\n\nKind regards,\n{person}"
        )
        return formal, friendly, detailed
    profile = (
        "Я начинающий специалист IT Support / Help Desk / QA с практическими навыками поддержки пользователей, troubleshooting, документации и аккуратных ручных проверок."
        if is_support_candidate else
        "Я специалист Manual QA и API QA с опытом в банковских и финтех-продуктах: API-проверки, SQL-валидация данных, регрессия, баг-репорты и поддержка пользователей."
    )
    ready = "Готов" if is_support_candidate else "Готова"
    glad = "Буду рад" if is_support_candidate else "Буду рада"
    skill_intro = "С задачами вакансии особенно совпадают мои навыки" if has_direct_matches else "Из моего рабочего стека для роли полезны"
    skill_sentence = f"{skill_intro}: {skill_text}." if skill_text else "Умею аккуратно работать по задачам, инструкциям и регламентам."
    question_block = "\n".join(questions)
    contact_lines = []
    for label, value in contacts:
        contact_lines.append(f"{'Портфолио' if label == 'Portfolio' else label}: {value}")
    contact_block = "Контакты:\n" + "\n".join(contact_lines)
    formal = (
        f"Здравствуйте!\n\nМеня зовут {person}. Откликаюсь на позицию «{position}» в {company}. "
        f"{profile}\n\n{skill_sentence}\n\n{question_block}\n\n{contact_block}\n\nС уважением,\n{person}"
    )
    friendly = (
        f"Добрый день!\n\nМеня зовут {person}. Меня заинтересовала позиция «{position}» в {company}. "
        f"{profile}\n\n{skill_sentence} {ready} коротко рассказать о практическом опыте и выполнить релевантное тестовое задание.\n\n"
        f"{question_block}\n\n{contact_block}\n\nС уважением,\n{person}"
    )
    detailed = (
        f"Здравствуйте!\n\nМеня зовут {person}. Хочу откликнуться на позицию «{position}» в {company}.\n\n"
        f"{profile}\n\n{skill_sentence} Могу подробнее рассказать, как применяю эти навыки в проверках, воспроизведении проблем, пользовательских сценариях и понятной передаче результатов команде.\n\n"
        f"Перед следующим этапом хочу уточнить несколько практических вопросов:\n{question_block}\n\n"
        f"{contact_block}\n\n{glad} обсудить задачи и следующий этап.\n\nС уважением,\n{person}"
    )
    return formal, friendly, detailed


def build_application_draft(
    user_id: int,
    vacancy_id: int,
    *,
    preferences: dict[str, Any] | None = None,
    tone_override: str | None = None,
    length_override: str | None = None,
    include_salary: bool = False,
) -> dict[str, Any]:
    vacancy_frame = db.query(
        """SELECT v.id,v.candidate_id,v.company,v.position,v.source,v.link,v.salary_text,v.salary_min,
                  v.employer_email,v.employer_contact,v.remote_location,v.final_salary_advice,v.source_snapshot,
                  v.work_type,v.strengths,
                  c.name,c.target_title,c.english_level,c.salary_min candidate_salary,c.notes,c.contact_email,
                  c.cover_tone,c.cover_length,c.manual_review
           FROM vacancies v JOIN candidates c ON c.id=v.candidate_id
           WHERE v.id=? AND v.user_id=?""",
        (vacancy_id, user_id),
    )
    if vacancy_frame.empty:
        raise HTTPException(status_code=404, detail="Вакансия не найдена.")
    vacancy = vacancy_frame.iloc[0].to_dict()
    preferences = preferences or application_preferences_payload(user_id)
    tone = tone_override or str(vacancy.get("cover_tone") or preferences["tone"])
    length = length_override or str(vacancy.get("cover_length") or preferences["length"])
    candidate_id = int(vacancy["candidate_id"])
    skills_frame = db.query("SELECT skill FROM skills WHERE user_id=? AND candidate_id=? ORDER BY skill", (user_id, candidate_id))
    skills = [] if skills_frame.empty else skills_frame["skill"].tolist()
    certificates_frame = db.query(
        "SELECT title,issuer FROM certificates WHERE user_id=? AND candidate_id=? AND include_in_resume=1 ORDER BY id DESC LIMIT 4",
        (user_id, candidate_id),
    )
    certificates = [] if certificates_frame.empty else [
        f"{item.get('title')}{' — ' + str(item.get('issuer')) if item.get('issuer') else ''}"
        for item in certificates_frame.to_dict("records")
    ]
    try:
        snapshot = json.loads(str(vacancy.get("source_snapshot") or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        snapshot = {}
    original_text = " ".join((
        str(vacancy.get("position") or ""),
        str(vacancy.get("company") or ""),
        str(vacancy.get("source") or ""),
        str(vacancy.get("employer_contact") or ""),
        str(snapshot.get("description") or ""),
        str(snapshot.get("tags") or ""),
    ))
    cyrillic, latin = len(re.findall(r"[А-Яа-яЁё]", original_text)), len(re.findall(r"[A-Za-z]", original_text))
    vacancy_language = "RU" if cyrillic >= max(12, latin // 3) else "EN"
    resume_frame = db.query(
        """SELECT id,title,language,content FROM resumes WHERE user_id=? AND candidate_id=?
           ORDER BY CASE language WHEN ? THEN 0 WHEN 'RU' THEN 1 WHEN 'EN' THEN 2 ELSE 3 END,id DESC LIMIT 1""",
        (user_id, candidate_id, vacancy_language),
    )
    resume = None if resume_frame.empty else resume_frame.iloc[0].to_dict()
    person_raw = str(vacancy.get("name") or "")
    person = russian_public_name(person_raw) if vacancy_language == "RU" else english_public_name(person_raw)
    company, position = str(vacancy.get("company") or "компании"), str(vacancy.get("position") or "")
    telegram_contact = extract_telegram_contact(original_text)
    is_telegram_reply = bool(telegram_contact and vacancy_language == "RU")
    social_frame = db.query(
        "SELECT platform,url FROM social_links WHERE user_id=? AND candidate_id=? AND COALESCE(show_global,1)=1 ORDER BY id",
        (user_id, candidate_id),
    )
    social_links = [] if social_frame.empty else social_frame.to_dict("records")
    candidate = {
        "name": person_raw,
        "contact_email": str(vacancy.get("contact_email") or preferences.get("from_email") or ""),
        "salary_min": int(vacancy.get("candidate_salary") or 0),
    }
    cover_item = dict(vacancy)
    cover_item["source_snapshot"] = snapshot
    formal, friendly, detailed = _compose_cover_variants(cover_item, candidate, skills, social_links, vacancy_language)
    cover_letter = detailed if length == "detailed" else friendly if tone == "friendly" else formal
    if is_telegram_reply:
        lines = [cover_letter]
        subject = f"Сообщение в Telegram: {position} — {person}"
        resume_guidance = (
            f"Это не email. Скопируйте текст и отправьте в Telegram {telegram_contact}. "
            "Резюме прикладывайте только если рекрутер попросит."
        )
        recipient = ""
        delivery_channel = "telegram"
        recipient_contact = telegram_contact
        contact_label = telegram_contact
    elif vacancy_language == "EN":
        lines = [cover_letter]
        subject, resume_guidance = f"Application: {position} — {person}", "Вакансия на английском: приложите английское резюме."
        recipient = str(vacancy.get("employer_email") or "").strip()
        delivery_channel = "email" if recipient else "manual"
        recipient_contact = ""
        contact_label = recipient or "Контакт не указан"
    else:
        lines = [cover_letter]
        subject, resume_guidance = f"Отклик: {position} — {person}", "Вакансия на русском: приложите русское резюме."
        recipient = str(vacancy.get("employer_email") or "").strip()
        delivery_channel = "email" if recipient else "manual"
        recipient_contact = extract_telegram_contact(original_text)
        contact_label = recipient or recipient_contact or "Контакт не указан"
    return {
        "vacancy_id": int(vacancy["id"]), "candidate_id": candidate_id,
        "company": str(vacancy.get("company") or ""), "position": str(vacancy.get("position") or ""),
        "recipient_email": recipient, "subject": subject, "cover_letter": "\n".join(lines),
        "vacancy_language": vacancy_language, "resume_guidance": resume_guidance,
        "delivery_channel": delivery_channel, "recipient_contact": recipient_contact, "contact_label": contact_label,
        "from_email": str(vacancy.get("contact_email") or preferences.get("from_email") or ""),
        "manual_review": bool(int(vacancy.get("manual_review") if vacancy.get("manual_review") is not None else 1)),
        "resume": resume,
    }


def build_gig_application_draft(
    user_id: int,
    gig_id: int,
    *,
    tone_override: str | None = None,
    length_override: str | None = None,
) -> dict[str, Any]:
    frame = db.query(
        """SELECT g.id,g.candidate_id,g.title,g.client,g.source,g.link,g.location,g.category,
                  g.work_format,g.pay_text,g.score,g.description,g.contacts_json,g.source_snapshot,
                  c.name,c.salary_min candidate_salary,c.contact_email,c.cover_tone,c.cover_length,c.manual_review
           FROM side_gigs g JOIN candidates c ON c.id=g.candidate_id AND c.user_id=g.user_id
           WHERE g.id=? AND g.user_id=?""",
        (gig_id, user_id),
    )
    if frame.empty:
        raise HTTPException(status_code=404, detail="Подработка не найдена.")
    gig = frame.iloc[0].to_dict()
    preferences = application_preferences_payload(user_id)
    tone = tone_override or str(gig.get("cover_tone") or preferences["tone"])
    length = length_override or str(gig.get("cover_length") or preferences["length"])
    candidate_id = int(gig["candidate_id"])
    skills_frame = db.query(
        "SELECT skill FROM skills WHERE user_id=? AND candidate_id=? ORDER BY skill",
        (user_id, candidate_id),
    )
    skills = [] if skills_frame.empty else skills_frame["skill"].tolist()
    social_frame = db.query(
        "SELECT platform,url FROM social_links WHERE user_id=? AND candidate_id=? AND COALESCE(show_global,1)=1 ORDER BY id",
        (user_id, candidate_id),
    )
    social_links = [] if social_frame.empty else social_frame.to_dict("records")
    try:
        contacts = json.loads(str(gig.get("contacts_json") or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        contacts = {}
    try:
        snapshot = json.loads(str(gig.get("source_snapshot") or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        snapshot = {}
    original_text = " ".join((
        str(gig.get("title") or ""), str(gig.get("client") or ""),
        str(gig.get("description") or ""), str(gig.get("work_format") or ""),
        str(gig.get("source") or ""),
    ))
    cyrillic = len(re.findall(r"[А-Яа-яЁё]", original_text))
    latin = len(re.findall(r"[A-Za-z]", original_text))
    vacancy_language = "RU" if cyrillic >= max(12, latin // 3) else "EN"
    resume_frame = db.query(
        """SELECT id,title,language,content FROM resumes WHERE user_id=? AND candidate_id=?
           ORDER BY CASE language WHEN ? THEN 0 WHEN 'RU' THEN 1 WHEN 'EN' THEN 2 ELSE 3 END,id DESC LIMIT 1""",
        (user_id, candidate_id, vacancy_language),
    )
    resume = None if resume_frame.empty else resume_frame.iloc[0].to_dict()
    candidate = {
        "name": str(gig.get("name") or ""),
        "contact_email": str(gig.get("contact_email") or preferences.get("from_email") or ""),
        "salary_min": int(gig.get("candidate_salary") or 0),
    }
    cover_item = dict(gig)
    cover_item.update({
        "item_type": "side_gig",
        "moonlight_compatible": int(str(gig.get("category") or "").lower() != "стажировка"),
        "source_snapshot": snapshot,
    })
    formal, friendly, detailed = _compose_cover_variants(
        cover_item, candidate, skills, social_links, vacancy_language,
    )
    cover_letter = detailed if length == "detailed" else friendly if tone == "friendly" else formal
    emails = [str(value).strip() for value in (contacts.get("emails") or []) if str(value).strip()]
    telegram = [str(value).strip() for value in (contacts.get("telegram") or []) if str(value).strip()]
    telegram_contact = telegram[0] if telegram else extract_telegram_contact(original_text)
    recipient = emails[0] if emails else ""
    person_raw = str(gig.get("name") or "")
    person = russian_public_name(person_raw) if vacancy_language == "RU" else english_public_name(person_raw)
    position = str(gig.get("title") or "")
    if telegram_contact and not recipient:
        delivery_channel = "telegram"
        subject = f"Сообщение в Telegram: {position} — {person}"
        guidance = f"Скопируйте текст и отправьте в Telegram {telegram_contact}. Резюме прикладывайте по запросу."
    else:
        delivery_channel = "email" if recipient else "manual"
        subject = (f"Отклик: {position} — {person}" if vacancy_language == "RU" else f"Application: {position} — {person}")
        guidance = "Приложите русское резюме." if vacancy_language == "RU" else "Приложите английское резюме."
    return {
        "gig_id": int(gig["id"]), "candidate_id": candidate_id,
        "company": str(gig.get("client") or ""), "position": position,
        "recipient_email": recipient, "recipient_contact": telegram_contact,
        "contact_label": recipient or telegram_contact or "Контакт не указан",
        "delivery_channel": delivery_channel, "subject": subject, "cover_letter": cover_letter,
        "vacancy_language": vacancy_language, "resume_guidance": guidance,
        "from_email": str(candidate.get("contact_email") or ""),
        "manual_review": bool(int(gig.get("manual_review") if gig.get("manual_review") is not None else 1)),
        "resume": resume,
    }


@app.get("/api/application-settings")
def application_preferences(user_id: int = Depends(current_user)) -> dict[str, Any]:
    return application_preferences_payload(user_id)


@app.put("/api/application-settings")
def save_application_preferences(body: ApplicationPreferencesRequest, user_id: int = Depends(current_user)) -> dict[str, Any]:
    public_release.put_setting(db.query, db.execute, user_id, "application_tone", body.tone)
    public_release.put_setting(db.query, db.execute, user_id, "application_length", body.length)
    public_release.put_setting(db.query, db.execute, user_id, "application_certificates", "1" if body.include_certificates else "0")
    public_release.put_setting(db.query, db.execute, user_id, "application_achievements", "1" if body.include_achievements else "0")
    if body.from_email.strip():
        public_release.put_setting(db.query, db.execute, user_id, "application_from_email", body.from_email.strip().lower())
    return application_preferences_payload(user_id)


@app.post("/api/application-settings/pro-code")
def activate_pro_code(body: ProCodeRequest, user_id: int = Depends(current_user)) -> dict[str, Any]:
    expected = os.getenv("CAREERMOVE_PRO_CODE", "").strip()
    if not expected or not hmac.compare_digest(expected, body.code.strip()):
        raise HTTPException(status_code=403, detail="Код Профи не подошёл.")
    public_release.put_setting(db.query, db.execute, user_id, "application_pro_enabled", "1")
    return application_preferences_payload(user_id)


@app.post("/api/applications/compose")
def compose_application(body: ApplicationComposeRequest, user_id: int = Depends(current_user)) -> dict[str, Any]:
    return build_application_draft(user_id, body.vacancy_id, tone_override=body.tone, length_override=body.length, include_salary=body.include_salary)


@app.post("/api/gigs/{gig_id}/compose")
def compose_gig_application(
    gig_id: int,
    body: GigApplicationComposeRequest,
    user_id: int = Depends(current_user),
) -> dict[str, Any]:
    return build_gig_application_draft(
        user_id,
        gig_id,
        tone_override=body.tone,
        length_override=body.length,
    )


@app.post("/api/candidates/{candidate_id}/certificates", status_code=status.HTTP_201_CREATED)
def add_certificate(candidate_id: int, body: CertificateRequest, user_id: int = Depends(current_user)) -> dict[str, Any]:
    require_candidate(candidate_id, user_id)
    db.execute(
        "INSERT INTO certificates(user_id,candidate_id,title,issuer,credential_url,issued_at,notes,include_in_resume) VALUES(?,?,?,?,?,?,?,?)",
        (user_id, candidate_id, body.title.strip(), body.issuer.strip(), body.credential_url.strip(), body.issued_at.strip(), body.notes.strip(), int(body.include_in_resume)),
    )
    frame = db.query("SELECT id,title,issuer,credential_url,issued_at,notes,include_in_resume FROM certificates WHERE user_id=? AND candidate_id=? ORDER BY id DESC LIMIT 1", (user_id, candidate_id))
    return json_safe(frame.iloc[0].to_dict())


@app.delete("/api/candidates/{candidate_id}/certificates/{certificate_id}")
def delete_certificate(candidate_id: int, certificate_id: int, user_id: int = Depends(current_user)) -> dict[str, bool]:
    require_candidate(candidate_id, user_id)
    db.execute("DELETE FROM certificates WHERE id=? AND candidate_id=? AND user_id=?", (certificate_id, candidate_id, user_id))
    # FastAPI 0.110+ correctly rejects a 204 route with a JSON response model.
    # Return a tiny explicit acknowledgement instead, which is clearer to the
    # client and keeps serverless imports compatible with the current runtime.
    return {"deleted": True}


@app.get("/api/search-schedule")
def get_search_schedule(user_id: int = Depends(current_user)) -> dict[str, Any]:
    row = db.query("SELECT enabled,frequency,updated_at,last_run_at,last_run_status FROM search_schedules WHERE user_id=?", (user_id,))
    if row.empty:
        return {"enabled": 0, "frequency": "once", "updated_at": "", "last_run_at": "", "last_run_status": ""}
    return json_safe(row.iloc[0].to_dict())


@app.put("/api/search-schedule")
def save_search_schedule(body: SearchScheduleRequest, user_id: int = Depends(current_user)) -> dict[str, Any]:
    now = now_iso()
    db.execute(
        """INSERT INTO search_schedules(user_id,enabled,frequency,updated_at,last_run_at,last_run_status)
           VALUES(?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET enabled=excluded.enabled,frequency=excluded.frequency,updated_at=excluded.updated_at""",
        (user_id, int(body.enabled), body.frequency, now, "", ""),
    )
    # The worker is a real GitHub Actions schedule.  The frequency preference is
    # saved here; a twice-daily cron must be enabled in that repository workflow.
    return get_search_schedule(user_id)


def telegram_bot_settings_payload(user_id: int, *, query=None) -> dict[str, Any]:
    read = query or db.query
    token = (
        public_release.read_setting(read, user_id, "telegram_bot_token", "").strip()
        or os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    )
    username = public_release.read_setting(read, user_id, "telegram_bot_username", "").strip().lstrip("@")
    chat_id = public_release.read_setting(read, user_id, "telegram_chat_id", "").strip()
    connect_code = public_release.read_setting(read, user_id, "telegram_connect_code", "").strip()
    return {
        "connected": bool(token and username),
        "subscribed": bool(token and chat_id),
        "username": username,
        "chat_id": "connected" if chat_id else "",
        "start_url": f"https://t.me/{username}?start={connect_code}" if username and connect_code else "",
        "schedule": "10:00 и 18:00 МСК",
        "detail": (
            "CareerBot подключён и будет присылать две отдельные подборки для QA-кандидата и Support-кандидата."
            if token and chat_id else
            "Откройте CareerBot и нажмите Start, чтобы привязать этот кабинет."
            if token and username else
            "CareerBot ещё не подключён к кабинету."
        ),
    }


def _telegram_api_call(token: str, method: str, payload: dict[str, Any], *, timeout: int = 12) -> dict[str, Any]:
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/{method}",
            json=payload,
            timeout=timeout,
        )
        data = response.json() if response.content else {}
    except (requests.RequestException, ValueError) as error:
        raise RuntimeError("Telegram временно недоступен.") from error
    if not response.ok or not bool(data.get("ok")):
        description = str(data.get("description") or "Telegram отклонил запрос.")
        raise RuntimeError(description[:240])
    return data


def _telegram_digest_items(query, user_id: int, candidate_id: int) -> list[dict[str, Any]]:
    rows = live_jobs.latest_jobs(query, user_id, [candidate_id], limit=180)
    ranked = sorted(
        rows,
        key=lambda item: (
            int(item.get("favorite") or 0),
            int(item.get("score") or 0),
            str(item.get("last_seen") or item.get("posted_at") or ""),
        ),
        reverse=True,
    )
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in ranked:
        if str(item.get("status") or "") in {"skip", "archived", "done"}:
            continue
        key = live_jobs.canonical_url(item.get("link")) or f"{item.get('company')}|{item.get('position')}".lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
        if len(unique) >= 6:
            break
    return unique


def _telegram_digest_message(candidate_name: str, jobs: list[dict[str, Any]]) -> tuple[str, list[list[dict[str, str]]]]:
    safe_name = html.escape(candidate_name or "кандидата")
    lines = [f"<b>Золотые вакансии для {safe_name}</b>", "Свежие карточки до 30 дней. Перед отправкой откройте оригинал."]
    buttons: list[list[dict[str, str]]] = []
    for index, job in enumerate(jobs, start=1):
        score = int(job.get("score") or 0)
        priority = "🟢" if score >= 80 else "🟡" if score >= live_jobs.GOLDEN_SCORE else "⚪️"
        title = html.escape(str(job.get("position") or "Вакансия")[:120])
        company = html.escape(str(job.get("company") or "Компания не указана")[:100])
        location = html.escape(str(job.get("remote_location") or "Удалённо")[:120])
        work_type = html.escape(str(job.get("work_type") or "формат уточнить")[:80])
        salary = html.escape(str(job.get("salary_text") or "не указана")[:100])
        strengths = html.escape(str(job.get("strengths") or "совпадение с профилем")[:180])
        contacts = html.escape(
            " · ".join(
                value for value in (
                    str(job.get("employer_email") or "").strip(),
                    str(job.get("employer_contact") or "").strip(),
                ) if value
            )[:140] or "в оригинале"
        )
        link = html.escape(str(job.get("link") or ""), quote=True)
        source = html.escape(str(job.get("source") or "источник")[:80])
        source_line = f'<a href="{link}">{source}</a>' if link else source
        lines.extend([
            "",
            f"{priority} <b>{index}. {title}</b> · {score}%",
            f"{company} · {location} · {work_type}",
            f"Зарплата: {salary}",
            f"Стек/совпадение: {strengths}",
            f"Контакты: {contacts} · {source_line}",
        ])
        vacancy_id = int(job.get("id") or 0)
        if vacancy_id:
            buttons.append([
                {"text": f"⭐ {index} В избранное", "callback_data": f"like:{vacancy_id}"},
                {"text": f"Не подходит {index}", "callback_data": f"dislike:{vacancy_id}"},
            ])
    if not jobs:
        lines.extend(["", "Новых карточек по безопасным условиям пока нет. Следующая проверка пройдёт по расписанию."])
    return "\n".join(lines), buttons


def send_telegram_golden_digest(user_id: int) -> dict[str, Any]:
    settings = telegram_bot_settings_payload(user_id)
    if not settings["subscribed"]:
        return {"channel": "telegram-careerbot", "status": "not_configured", "detail": settings["detail"]}
    token = public_release.read_setting(db.query, user_id, "telegram_bot_token", "").strip() or os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = public_release.read_setting(db.query, user_id, "telegram_chat_id", "").strip()
    candidates = db.query("SELECT id,name FROM candidates WHERE user_id=? ORDER BY id", (user_id,))
    sent = 0
    for candidate in ([] if candidates.empty else candidates.to_dict("records")):
        jobs = _telegram_digest_items(db.query, user_id, int(candidate["id"]))
        message, buttons = _telegram_digest_message(str(candidate.get("name") or ""), jobs)
        _telegram_api_call(token, "sendMessage", {
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
            "reply_markup": {"inline_keyboard": buttons},
        })
        sent += 1
    return {
        "channel": "telegram-careerbot",
        "status": "sent" if sent else "empty",
        "detail": f"Отправлено сообщений: {sent}.",
    }


@app.get("/api/telegram-bot")
def get_telegram_bot(user_id: int = Depends(current_user)) -> dict[str, Any]:
    return telegram_bot_settings_payload(user_id)


@app.post("/api/telegram-bot/connect")
def connect_telegram_bot(
    body: TelegramBotConnectRequest,
    request: FastAPIRequest,
    user_id: int = Depends(current_user),
) -> dict[str, Any]:
    token = body.bot_token.strip()
    if not re.fullmatch(r"\d{6,15}:[A-Za-z0-9_-]{25,}", token):
        raise HTTPException(status_code=400, detail="BotFather выдал ключ в неожиданном формате.")
    try:
        identity = _telegram_api_call(token, "getMe", {})["result"]
    except RuntimeError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    username = str(identity.get("username") or "").strip().lstrip("@")
    if not username:
        raise HTTPException(status_code=400, detail="У бота нет публичного username.")
    webhook_secret = secrets.token_urlsafe(30)
    connect_code = secrets.token_urlsafe(12)
    api_url = (
        os.getenv("CAREERMOVE_API_PUBLIC_URL", "").strip().rstrip("/")
        or str(request.base_url).rstrip("/")
    )
    try:
        _telegram_api_call(token, "setWebhook", {
            "url": f"{api_url}/api/telegram/webhook",
            "secret_token": webhook_secret,
            "allowed_updates": ["message", "callback_query"],
            "drop_pending_updates": True,
        })
    except RuntimeError as error:
        raise HTTPException(status_code=502, detail=f"Не удалось включить webhook: {error}") from error
    with db.transaction() as (query, execute):
        public_release.put_setting(query, execute, user_id, "telegram_bot_token", token)
        public_release.put_setting(query, execute, user_id, "telegram_bot_username", username)
        public_release.put_setting(query, execute, user_id, "telegram_webhook_secret", webhook_secret)
        public_release.put_setting(query, execute, user_id, "telegram_connect_code", connect_code)
        public_release.put_setting(query, execute, user_id, "telegram_chat_id", "")
    return telegram_bot_settings_payload(user_id)


@app.post("/api/telegram-bot/test")
def test_telegram_bot(user_id: int = Depends(current_user)) -> dict[str, Any]:
    try:
        return send_telegram_golden_digest(user_id)
    except RuntimeError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error


@app.post("/api/telegram/webhook")
def telegram_webhook(
    update: dict[str, Any],
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> dict[str, bool]:
    secret = str(x_telegram_bot_api_secret_token or "")
    if not secret:
        raise HTTPException(status_code=404, detail="Not found")
    owner = db.query(
        "SELECT user_id FROM settings WHERE key='telegram_webhook_secret' AND value=? LIMIT 1",
        (secret,),
    )
    if owner.empty:
        raise HTTPException(status_code=404, detail="Not found")
    user_id = int(owner.iloc[0]["user_id"])
    token = public_release.read_setting(db.query, user_id, "telegram_bot_token", "").strip() or os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    message = update.get("message") if isinstance(update.get("message"), dict) else {}
    callback = update.get("callback_query") if isinstance(update.get("callback_query"), dict) else {}
    if message:
        chat = message.get("chat") if isinstance(message.get("chat"), dict) else {}
        chat_id = str(chat.get("id") or "")
        text_value = str(message.get("text") or "").strip()
        command, _, code = text_value.partition(" ")
        expected_code = public_release.read_setting(db.query, user_id, "telegram_connect_code", "").strip()
        existing_chat = public_release.read_setting(db.query, user_id, "telegram_chat_id", "").strip()
        if command.startswith("/start") and chat_id and (hmac.compare_digest(code.strip(), expected_code) or chat_id == existing_chat):
            public_release.put_setting(db.query, db.execute, user_id, "telegram_chat_id", chat_id)
            _telegram_api_call(token, "sendMessage", {
                "chat_id": chat_id,
                "text": "CareerBot подключён. Подборки QA-кандидата и Support-кандидата будут приходить в 10:00 и 18:00 МСК. Кнопки под вакансиями синхронизируются с CareerMove.",
            })
        elif command.startswith("/start") and chat_id:
            _telegram_api_call(token, "sendMessage", {
                "chat_id": chat_id,
                "text": "Откройте бота по персональной ссылке из настроек CareerMove.",
            })
    if callback:
        callback_id = str(callback.get("id") or "")
        data = str(callback.get("data") or "")
        callback_message = callback.get("message") if isinstance(callback.get("message"), dict) else {}
        callback_chat = callback_message.get("chat") if isinstance(callback_message.get("chat"), dict) else {}
        chat_id = str(callback_chat.get("id") or "")
        expected_chat = public_release.read_setting(db.query, user_id, "telegram_chat_id", "").strip()
        answer = "Действие не распознано."
        if chat_id and hmac.compare_digest(chat_id, expected_chat):
            action, separator, raw_id = data.partition(":")
            if separator and raw_id.isdigit() and action in {"like", "dislike"}:
                vacancy_id = int(raw_id)
                row = db.query("SELECT id FROM vacancies WHERE id=? AND user_id=?", (vacancy_id, user_id))
                if not row.empty:
                    if action == "like":
                        db.execute("UPDATE vacancies SET favorite=1 WHERE id=? AND user_id=?", (vacancy_id, user_id))
                        answer = "Добавлено в избранное и поднято в очереди."
                    else:
                        db.execute("UPDATE vacancies SET favorite=0,status='skip' WHERE id=? AND user_id=?", (vacancy_id, user_id))
                        answer = "Вакансия скрыта как неподходящая."
                    try:
                        sync_google_sheet_snapshot(user_id)
                    except Exception:
                        pass
        if callback_id:
            try:
                _telegram_api_call(token, "answerCallbackQuery", {"callback_query_id": callback_id, "text": answer})
            except RuntimeError:
                pass
    return {"ok": True}


def update_search_run(run_id: str, **values: Any) -> None:
    allowed = {"status", "stage", "detail", "result_json", "error"}
    entries = [(key, value) for key, value in values.items() if key in allowed]
    if not entries:
        return
    assignments = ",".join(f"{key}=?" for key, _ in entries)
    params = tuple(str(value) for _, value in entries) + (now_iso(), run_id)
    db.execute(
        f"UPDATE search_runs_v2 SET {assignments},updated_at=? WHERE run_id=?",
        params,
    )


def dispatch_search_workflow(run_id: str, user_id: int, use_ai: bool) -> tuple[bool, str]:
    """Start the long-running search outside the serverless API.

    The Vercel Hobby API is intentionally kept request-only. Vacancy collection
    runs in GitHub Actions, where it is not cancelled when the HTTP function
    returns or reaches its duration limit.
    """

    token = (
        os.getenv("GITHUB_SEARCH_TOKEN", "").strip()
        or os.getenv("CAREERMOVE_GITHUB_BACKUP_TOKEN", "").strip()
    )
    repository = os.getenv(
        "GITHUB_SEARCH_REPOSITORY",
        os.getenv("CAREERMOVE_GITHUB_BACKUP_REPOSITORY", "your-github-account/careermove-showcase"),
    ).strip()
    workflow = os.getenv("GITHUB_SEARCH_WORKFLOW", "daily-search.yml").strip()
    ref = os.getenv("GITHUB_SEARCH_REF", "main").strip()
    if not token or "/" not in repository:
        return (
            False,
            "Поиск поставлен в очередь и будет выполнен ближайшим ежедневным запуском.",
        )

    import requests

    url = f"https://api.github.com/repos/{repository}/actions/workflows/{workflow}/dispatches"
    try:
        response = requests.post(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            json={
                "ref": ref,
                "inputs": {
                    "run_id": run_id,
                    "user_id": str(user_id),
                    "use_ai": "true" if use_ai else "false",
                },
            },
            timeout=5,
        )
        if response.status_code == 204:
            return True, "Безопасный фоновый поиск запущен. Эту страницу можно закрыть."
    except requests.RequestException:
        pass
    return (
        False,
        "Поиск сохранён в очереди. Если быстрый запуск недоступен, его подхватит ежедневная проверка.",
    )


def run_search_task(
    run_id: str,
    user_id: int,
    use_ai: bool,
    source_names: tuple[str, ...] | list[str] | None = None,
    quick: bool = False,
) -> None:
    try:
        # Vercel Blob persists SQLite as a private snapshot.  Calling the
        # generic db helpers from every source callback used to download and
        # upload that snapshot dozens of times, so the serverless request was
        # terminated mid-search.  Keep the whole deterministic pass in one
        # transaction and publish a single final snapshot instead.
        last_progress = {"stage": "sources", "detail": "Подключаю источники вакансий…"}
        with db.transaction() as (query, execute):
            # A search may be clicked before the dashboard has loaded.  Run
            # the narrowly-scoped legacy migration here too, otherwise an old
            # Belgrade + "exclude Vietnam" profile can make a valid Vietnam
            # search look empty.
            migrate_legacy_serbia_strategy(user_id, query, execute)
            candidates = query("SELECT id FROM candidates WHERE user_id=? ORDER BY id", (user_id,))
            candidate_ids = [int(value) for value in candidates["id"].tolist()] if not candidates.empty else []
            if not candidate_ids:
                raise RuntimeError("Сначала добавьте хотя бы один профиль кандидата.")
            try:
                max_age = int(public_release.read_setting(query, user_id, "search_max_age_days", "30"))
            except ValueError:
                max_age = 30
            max_age = max(3, min(max_age, 30))

            def progress(stage: str, detail: str) -> None:
                # Preserve the useful final state without forcing a remote
                # database snapshot for every provider/batch.
                last_progress["stage"] = stage
                last_progress["detail"] = detail

            with temporary_environ(user_ai_runtime_env(query, user_id)):
                result = live_jobs.run_search(
                    query,
                    execute,
                    user_id,
                    candidate_ids,
                    # Only save useful leads. Hard eligibility gates still reject
                    # senior, wrong-country and language-blocked roles before scoring.
                    min_score=live_jobs.BROAD_REVIEW_SCORE if quick else live_jobs.REVIEW_SCORE,
                    max_age_days=max_age,
                    # Vietnam office/hybrid roles are valid for the current profiles.
                    remote_only=False,
                    job_types={"full-time", "contract", "part-time", "freelance", "internship"},
                    limit_per_candidate=100 if quick else 80,
                    force_refresh=True,
                    # A manual click must finish on a free serverless function. It
                    # uses the deterministic match engine first; the scheduled full
                    # run can still add the optional multi-model review afterwards.
                    use_open_model=use_ai and not quick,
                    progress=progress,
                    source_names=source_names,
                    # The interactive serverless function must return a first useful
                    # packet before the platform freezes its background work.
                    max_source_wait_seconds=28.0 if quick else None,
                )
            gig_result = sync_side_gigs_from_jobs(
                query,
                execute,
                user_id,
                result.get("saved", []),
                max_age_days=max_age,
            )
            internship_result = sync_internships_from_jobs(
                query,
                execute,
                user_id,
                result.get("saved", []),
                max_age_days=max_age,
            )
            result["gigs"] = gig_result
            result["internships"] = internship_result
            public_release.put_setting(query, execute, user_id, "last_live_search_at", now_iso())
        sheet_result = sync_google_sheet_snapshot(user_id)
        notification_statuses = []
        app_url = os.getenv("APP_URL", "http://localhost:5173").strip()
        try:
            notification_statuses.extend(public_release.deliver_digest_notifications(
                db.query,
                db.execute,
                user_id,
                result.get("saved", []),
                app_url=app_url,
            ))
        except Exception:
            pass
        try:
            notification_statuses.extend(push_notifications.deliver_push_notifications(
                db.query,
                db.execute,
                user_id,
                result.get("saved", []),
                run_id=run_id,
                app_url=app_url,
            ))
        except Exception:
            pass
        update_search_run(
            run_id,
            status="completed",
            stage="done",
            detail=(
                "Быстрый онлайн-поиск готов: вакансии и подработки обновлены, закрытые карточки скрыты."
                if quick else "Подборка обновлена. Можно проверять карточки."
            ),
            result_json=json.dumps(json_safe({
                "raw_count": result.get("raw_count", 0),
                # Expose the persisted-card count as a user-visible health
                # signal.  It makes a source/feed problem distinguishable from
                # a successful search where strict matching found no fit.
                "saved_count": min(50, len({
                    live_jobs.canonical_url(item.get("url") or item.get("link"))
                    or f"{item.get('company')}|{item.get('title')}|{item.get('source')}"
                    for item in result.get("saved", [])
                })),
                "golden_count": result.get("golden_count", 0),
                "new_count": int(result.get("new_count") or 0),
                "updated_count": int(result.get("updated_count") or 0),
                "rechecked_count": int(result.get("rechecked_count") or 0),
                "archived_count": result.get("archived_count", 0),
                "gigs_saved": int(result.get("gigs", {}).get("saved", 0)),
                "gigs_refreshed": int(result.get("gigs", {}).get("refreshed", 0)),
                "internships_saved": int(result.get("internships", {}).get("saved", 0)),
                "internships_refreshed": int(result.get("internships", {}).get("refreshed", 0)),
                "candidate_stats": result.get("candidate_stats", []),
                "sheet_sync": sheet_result,
                "notifications": notification_statuses,
            }), ensure_ascii=False),
        )
    except Exception as error:
        # A storage outage must not turn this into an opaque HTTP 500.  The
        # best-effort status write can fail for the same infrastructure reason,
        # therefore never let it mask the original safe error path.
        try:
            update_search_run(
                run_id,
                status="failed",
                stage="error",
                detail="Поиск не завершён. Старые карточки сохранены.",
                error=live_jobs.safe_network_error(error),
            )
        except Exception as status_error:  # pragma: no cover - hosted storage outage
            print(f"CareerMove search status persistence failed: {type(status_error).__name__}")
        print(f"CareerMove search failed: {type(error).__name__}")


def _scheduled_slot_id(frequency: str, moment: datetime | None = None) -> str:
    """Return the Moscow delivery slot that is open at ``moment``.

    Slots are calendar based instead of duration based. A morning search that
    finishes late must never postpone or suppress the 18:00 delivery.
    """
    current = moment or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    moscow = current.astimezone(MOSCOW_TIMEZONE)
    slot = ""
    if frequency == "twice" and moscow.hour >= 18:
        slot = "evening"
    elif moscow.hour >= 10:
        slot = "morning"
    return f"{moscow.date().isoformat()}:{slot}" if slot else ""


def _claim_scheduled_slot(user_id: int, key: str, slot_id: str) -> bool:
    """Atomically claim one scheduled action across every active scheduler."""
    if not slot_id:
        return False
    claimed = db.query(
        """
        INSERT INTO settings(user_id,key,value) VALUES(?,?,?)
        ON CONFLICT(user_id,key) DO UPDATE SET value=excluded.value
        WHERE settings.value<>excluded.value
        RETURNING value
        """,
        (user_id, key, slot_id),
    )
    return not claimed.empty


def _release_scheduled_slot(user_id: int, key: str, slot_id: str) -> None:
    db.execute(
        "UPDATE settings SET value='' WHERE user_id=? AND key=? AND value=?",
        (user_id, key, slot_id),
    )


def _send_scheduled_telegram_digest(user_id: int, slot_id: str) -> dict[str, Any]:
    """Send once per Moscow slot, even when Vercel and GitHub trigger together."""
    marker_key = "last_scheduled_digest_slot"
    if not _claim_scheduled_slot(user_id, marker_key, slot_id):
        return {"channel": "telegram-careerbot", "status": "already_sent", "slot": slot_id}
    try:
        result = send_telegram_golden_digest(user_id)
    except Exception:
        # Let the completion step or another scheduler retry a transient
        # Telegram failure during the same delivery window.
        _release_scheduled_slot(user_id, marker_key, slot_id)
        raise
    return {**result, "slot": slot_id}


def _scheduled_run_state(slot_id: str) -> dict[str, Any]:
    state = interactive_search_initial_state()
    state["schedule_slot"] = slot_id
    return state


def run_due_scheduled_searches(use_ai: bool = False) -> dict[str, int]:
    """Run every cabinet whose saved search schedule is due.

    This is deliberately kept in the API module so the GitHub worker and the
    Vercel daily cron use exactly the same ownership, pause and de-duplication
    rules.  A second trigger on the same day simply reports the profile as
    skipped instead of making another noisy collection pass.
    """
    users = db.query(
        """
        SELECT DISTINCT u.id
        FROM users u
        JOIN candidates c ON c.user_id=u.id
        JOIN search_schedules s ON s.user_id=u.id AND s.enabled=1
        ORDER BY u.id
        """
    )
    completed = 0
    skipped = 0
    now = datetime.now(timezone.utc)
    for value in ([] if users.empty else users["id"].tolist()):
        scheduled_user_id = int(value)
        schedule = db.query(
            "SELECT enabled,frequency,last_run_at FROM search_schedules WHERE user_id=?",
            (scheduled_user_id,),
        )
        enabled = False if schedule.empty else bool(int(schedule.iloc[0].get("enabled") or 0))
        frequency = "once" if schedule.empty else str(schedule.iloc[0].get("frequency") or "once")
        slot_id = _scheduled_slot_id(frequency, now)
        if not enabled or not slot_id:
            skipped += 1
            continue
        try:
            _send_scheduled_telegram_digest(scheduled_user_id, slot_id)
        except Exception:
            pass
        if not _claim_scheduled_slot(scheduled_user_id, "last_scheduled_search_slot", slot_id):
            skipped += 1
            continue
        pending = db.query(
            """
            SELECT run_id FROM search_runs_v2
            WHERE user_id=? AND status IN ('queued','running')
            ORDER BY created_at ASC LIMIT 1
            """,
            (scheduled_user_id,),
        )
        if pending.empty:
            run_id = str(uuid.uuid4())
            created = now_iso()
            db.execute(
                """
                INSERT INTO search_runs_v2(run_id,user_id,status,stage,detail,result_json,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?)
                """,
                (
                    run_id, scheduled_user_id, "queued", "cron", "Плановый поиск запущен.",
                    json.dumps(_scheduled_run_state(slot_id), ensure_ascii=False), created, created,
                ),
            )
        else:
            run_id = str(pending.iloc[0]["run_id"])
        try:
            run_search_task(run_id, scheduled_user_id, use_ai, live_jobs.FAST_SOURCE_NAMES, True)
            db.execute(
                """INSERT INTO search_schedules(user_id,enabled,frequency,updated_at,last_run_at,last_run_status)
                   VALUES(?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET last_run_at=excluded.last_run_at,last_run_status=excluded.last_run_status""",
                (scheduled_user_id, int(enabled), frequency, now.isoformat(), now.isoformat(), "completed"),
            )
            completed += 1
        except Exception as exc:
            _release_scheduled_slot(scheduled_user_id, "last_scheduled_search_slot", slot_id)
            db.execute(
                """INSERT INTO search_schedules(user_id,enabled,frequency,updated_at,last_run_at,last_run_status)
                   VALUES(?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET last_run_at=excluded.last_run_at,last_run_status=excluded.last_run_status""",
                (scheduled_user_id, int(enabled), frequency, now.isoformat(), now.isoformat(), f"failed: {str(exc)[:180]}"),
            )
    return {"users": len(users.index), "completed": completed, "skipped": skipped}


def _scheduled_search_due(
    last_raw: str,
    frequency: str,
    moment: datetime | None = None,
) -> bool:
    current = moment or datetime.now(timezone.utc)
    current_slot = _scheduled_slot_id(frequency, current)
    if not current_slot:
        return False
    try:
        last_run = datetime.fromisoformat(last_raw.replace("Z", "+00:00")) if last_raw else None
        if last_run and last_run.tzinfo is None:
            last_run = last_run.replace(tzinfo=timezone.utc)
    except ValueError:
        last_run = None
    return not last_run or _scheduled_slot_id(frequency, last_run) != current_slot


def _complete_scheduled_search(
    user_id: int,
    run_id: str,
    frequency: str,
    slot_id: str,
) -> dict[str, Any]:
    completed_at = now_iso()
    db.execute(
        """INSERT INTO search_schedules(user_id,enabled,frequency,updated_at,last_run_at,last_run_status)
           VALUES(?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET
             last_run_at=excluded.last_run_at,last_run_status=excluded.last_run_status,updated_at=excluded.updated_at""",
        (user_id, 1, frequency, completed_at, completed_at, "completed"),
    )
    sheet = sync_google_sheet_snapshot(user_id)
    telegram: dict[str, Any] = {"status": "already_sent", "slot": slot_id}
    try:
        telegram = _send_scheduled_telegram_digest(user_id, slot_id)
    except Exception as error:
        telegram = {"status": "deferred", "detail": live_jobs.safe_network_error(error), "slot": slot_id}
    return {"sheet": sheet, "telegram": telegram}


def run_scheduled_search_steps(*, start_due: bool, max_steps: int = 2) -> dict[str, int]:
    """Start or resume bounded search batches within a serverless request.

    A full 98-source pass is intentionally split across repeated cron calls.
    Progress lives in search_runs_v2, so a cold start or provider timeout never
    discards already saved cards and the next invocation resumes the same run.
    """
    users = db.query(
        """
        SELECT DISTINCT u.id
        FROM users u
        JOIN candidates c ON c.user_id=u.id
        JOIN search_schedules s ON s.user_id=u.id AND s.enabled=1
        ORDER BY u.id
        """
    )
    summary = {"users": len(users.index), "started": 0, "resumed": 0, "completed": 0, "failed": 0, "skipped": 0}
    for value in ([] if users.empty else users["id"].tolist()):
        user_id = int(value)
        schedule = db.query(
            "SELECT enabled,frequency,last_run_at FROM search_schedules WHERE user_id=?",
            (user_id,),
        )
        if schedule.empty or not bool(int(schedule.iloc[0].get("enabled") or 0)):
            summary["skipped"] += 1
            continue
        frequency = str(schedule.iloc[0].get("frequency") or "once")
        last_raw = str(schedule.iloc[0].get("last_run_at") or "")
        slot_id = _scheduled_slot_id(frequency)
        if start_due and slot_id:
            try:
                _send_scheduled_telegram_digest(user_id, slot_id)
            except Exception:
                pass
        pending = db.query(
            """
            SELECT run_id,status,stage,result_json,updated_at FROM search_runs_v2
            WHERE user_id=? AND status IN ('queued','running')
            ORDER BY created_at ASC LIMIT 1
            """,
            (user_id,),
        )
        if not pending.empty:
            if start_due:
                if not slot_id or not _claim_scheduled_slot(user_id, "last_scheduled_search_slot", slot_id):
                    summary["skipped"] += 1
                    continue
            active = pending.iloc[0]
            updated = live_jobs.parse_datetime(active.get("updated_at"))
            if updated and (datetime.now(timezone.utc) - updated).total_seconds() < 75:
                summary["skipped"] += 1
                continue
            run_id = str(active["run_id"])
            try:
                state = json.loads(str(active.get("result_json") or "{}"))
            except json.JSONDecodeError:
                state = {}
            if start_due:
                state["schedule_slot"] = slot_id
                update_search_run(run_id, result_json=json.dumps(json_safe(state), ensure_ascii=False))
            summary["resumed"] += 1
        elif start_due and _scheduled_search_due(last_raw, frequency) and _claim_scheduled_slot(
            user_id, "last_scheduled_search_slot", slot_id,
        ):
            run_id = str(uuid.uuid4())
            created = now_iso()
            db.execute(
                """INSERT INTO search_runs_v2(run_id,user_id,status,stage,detail,result_json,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (
                    run_id, user_id, "queued", "cron", "Плановый поиск подготовлен.",
                    json.dumps(_scheduled_run_state(slot_id), ensure_ascii=False), created, created,
                ),
            )
            summary["started"] += 1
        else:
            summary["skipped"] += 1
            continue

        for _ in range(max(1, min(int(max_steps), 3))):
            payload = continue_interactive_search(run_id, user_id, retryable=True)
            status_value = str(payload.get("status") or "")
            if status_value == "completed":
                result_state = payload.get("result") if isinstance(payload.get("result"), dict) else {}
                completion_slot = str(result_state.get("schedule_slot") or slot_id)
                _complete_scheduled_search(user_id, run_id, frequency, completion_slot)
                summary["completed"] += 1
                break
            if status_value == "failed":
                db.execute(
                    "UPDATE search_schedules SET last_run_status=?,updated_at=? WHERE user_id=?",
                    ("failed", now_iso(), user_id),
                )
                summary["failed"] += 1
                break
            if status_value == "queued":
                break
    return summary


@app.get("/api/cron/daily-search")
def daily_search_cron(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    """Authenticated fallback scheduler for the public Vercel deployment."""
    secret = os.getenv("CRON_SECRET", "").strip()
    expected = f"Bearer {secret}"
    if not secret or not authorization or not hmac.compare_digest(authorization, expected):
        # Do not disclose whether the fallback scheduler is configured.
        raise HTTPException(status_code=404, detail="Not found")
    # The persistent production database is migrated separately. Local and
    # first-run SQLite installations still bootstrap through this helper.
    try:
        ensure_runtime_schema()
        return run_scheduled_search_steps(start_due=True, max_steps=2)
    except Exception as error:  # pragma: no cover - only an infrastructure outage
        # Preserve the public API's availability and avoid returning database
        # details.  The scheduler can try again tomorrow; user data and any
        # existing vacancy cards remain untouched.
        print(f"CareerMove daily scheduler deferred: {type(error).__name__}")
        return {"users": 0, "completed": 0, "skipped": 0, "status": "deferred"}


@app.get("/api/cron/search-step")
def scheduled_search_step(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    """Resume an existing scheduled search without creating an extra run."""
    secret = os.getenv("CRON_SECRET", "").strip()
    expected = f"Bearer {secret}"
    if not secret or not authorization or not hmac.compare_digest(authorization, expected):
        raise HTTPException(status_code=404, detail="Not found")
    try:
        ensure_runtime_schema()
        return run_scheduled_search_steps(start_due=False, max_steps=2)
    except Exception as error:  # pragma: no cover - hosted infrastructure outage
        print(f"CareerMove scheduled step deferred: {type(error).__name__}")
        return {"users": 0, "completed": 0, "skipped": 0, "status": "deferred"}


def interactive_source_batches() -> tuple[tuple[str, ...], ...]:
    names = tuple(dict.fromkeys(live_jobs.FAST_SOURCE_NAMES))
    return tuple(
        tuple(names[index:index + INTERACTIVE_SOURCE_BATCH_SIZE])
        for index in range(0, len(names), INTERACTIVE_SOURCE_BATCH_SIZE)
    )


def interactive_search_initial_state() -> dict[str, Any]:
    return {
        "started_at": now_iso(),
        "next_batch": 0,
        "batch_count": len(interactive_source_batches()),
        "sources_total": len(live_jobs.FAST_SOURCE_NAMES),
        "sources_processed": 0,
    }


def search_run_payload(run_id: str, user_id: int) -> dict[str, Any]:
    frame = db.query(
        """
        SELECT run_id,status,stage,detail,result_json,error,created_at,updated_at
        FROM search_runs_v2 WHERE run_id=? AND user_id=?
        """,
        (run_id, user_id),
    )
    if frame.empty:
        raise HTTPException(status_code=404, detail="Запуск поиска не найден.")
    row = frame.iloc[0].to_dict()
    if row.get("result_json"):
        try:
            row["result"] = json.loads(str(row["result_json"]))
        except json.JSONDecodeError:
            row["result"] = None
    row.pop("result_json", None)
    return json_safe(row)


def continue_interactive_search(
    run_id: str,
    user_id: int,
    *,
    retryable: bool = False,
) -> dict[str, Any]:
    current = search_run_payload(run_id, user_id)
    if current.get("status") in {"completed", "failed"}:
        return current
    state = current.get("result") if isinstance(current.get("result"), dict) else {}
    batches = interactive_source_batches()
    batch_index = max(0, int(state.get("next_batch") or 0))
    if batch_index >= len(batches):
        update_search_run(run_id, status="completed", stage="done", detail="Подборка полностью обновлена.")
        return search_run_payload(run_id, user_id)

    source_names = batches[batch_index]
    update_search_run(
        run_id,
        status="running",
        stage=f"batch-{batch_index + 1}",
        detail=f"Проверяю пакет {batch_index + 1} из {len(batches)}: {len(source_names)} источников.",
    )
    try:
        with db.transaction() as (query, execute):
            migrate_legacy_serbia_strategy(user_id, query, execute)
            candidates = query("SELECT id FROM candidates WHERE user_id=? ORDER BY id", (user_id,))
            candidate_ids = [] if candidates.empty else [int(value) for value in candidates["id"].tolist()]
            if not candidate_ids:
                raise RuntimeError("Сначала добавьте хотя бы один профиль кандидата.")
            try:
                max_age = int(public_release.read_setting(query, user_id, "search_max_age_days", "30"))
            except ValueError:
                max_age = 30
            max_age = max(3, min(max_age, 30))
            result = live_jobs.run_search(
                query,
                execute,
                user_id,
                candidate_ids,
                min_score=live_jobs.BROAD_REVIEW_SCORE,
                max_age_days=max_age,
                remote_only=False,
                job_types={"full-time", "contract", "part-time", "freelance", "internship"},
                # Keep enough review candidates from every source packet to
                # maintain a 30–50 card active queue for two profiles. This
                # is a visibility target, not a promise to fabricate 30 new
                # market postings when providers have not published them.
                limit_per_candidate=12,
                force_refresh=True,
                use_open_model=False,
                source_names=source_names,
                max_source_wait_seconds=12.0,
            )
            gig_result = sync_side_gigs_from_jobs(
                query,
                execute,
                user_id,
                result.get("saved", []),
                max_age_days=max_age,
            )
            internship_result = sync_internships_from_jobs(
                query,
                execute,
                user_id,
                result.get("saved", []),
                max_age_days=max_age,
            )
            public_release.put_setting(query, execute, user_id, "last_live_search_at", now_iso())

        diagnostics = result.get("diagnostics", [])
        next_batch = batch_index + 1
        state.update({
            "retry_count": 0,
            "next_batch": next_batch,
            "batch_count": len(batches),
            "sources_total": len(live_jobs.FAST_SOURCE_NAMES),
            "sources_processed": min(next_batch * INTERACTIVE_SOURCE_BATCH_SIZE, len(live_jobs.FAST_SOURCE_NAMES)),
            "raw_count": int(state.get("raw_count") or 0) + int(result.get("raw_count") or 0),
            "new_count": int(state.get("new_count") or 0) + int(result.get("new_count") or 0),
            "updated_count": int(state.get("updated_count") or 0) + int(result.get("updated_count") or 0),
            "rechecked_count": int(state.get("rechecked_count") or 0) + int(result.get("rechecked_count") or 0),
            "archived_count": int(state.get("archived_count") or 0) + int(result.get("archived_count") or 0),
            "gigs_saved": int(state.get("gigs_saved") or 0) + int(gig_result.get("saved") or 0),
            "gigs_refreshed": int(state.get("gigs_refreshed") or 0) + int(gig_result.get("refreshed") or 0),
            "internships_saved": int(state.get("internships_saved") or 0) + int(internship_result.get("saved") or 0),
            "internships_refreshed": int(state.get("internships_refreshed") or 0) + int(internship_result.get("refreshed") or 0),
            "deferred_sources": int(state.get("deferred_sources") or 0) + sum(
                1 for item in diagnostics if str(item.get("status") or "") in {"deferred", "error"}
            ),
        })
        completed = next_batch >= len(batches)
        if completed:
            with db.transaction() as (query, _execute):
                candidates = query("SELECT id,name FROM candidates WHERE user_id=? ORDER BY id", (user_id,))
                candidate_ids = [] if candidates.empty else [int(value) for value in candidates["id"].tolist()]
                visible = live_jobs.latest_jobs(query, user_id, candidate_ids, limit=240)
            unique_links = {
                str(item.get("link") or f"{item.get('company')}|{item.get('position')}|{item.get('source')}").lower()
                for item in visible
            }
            state["saved_count"] = min(50, len(unique_links))
            state["active_count"] = len(unique_links)
            state["golden_count"] = sum(
                1 for item in visible if int(item.get("score") or 0) >= live_jobs.GOLDEN_SCORE
            )
            state["candidate_stats"] = [
                {
                    "candidate": str(row.get("name") or ""),
                    "review": sum(1 for item in visible if int(item.get("candidate_id") or 0) == int(row["id"])),
                    "golden": sum(
                        1 for item in visible
                        if int(item.get("candidate_id") or 0) == int(row["id"])
                        and int(item.get("score") or 0) >= live_jobs.GOLDEN_SCORE
                    ),
                }
                for row in ([] if candidates.empty else candidates.to_dict("records"))
            ]
            state["sheet_sync"] = sync_google_sheet_snapshot(user_id)
        update_search_run(
            run_id,
            status="completed" if completed else "running",
            stage="done" if completed else f"batch-{next_batch}",
            detail=(
                f"Поиск завершён: проверено {state['sources_processed']} источников; "
                f"новых карточек {state.get('new_count', 0)}, обновлено {state.get('updated_count', 0)}, "
                f"повторно проверено {state.get('rechecked_count', 0)}, снято {state.get('archived_count', 0)}; "
                f"в активной выдаче {state.get('saved_count', 0)}."
                if completed else
                f"Пакет {next_batch} из {len(batches)} готов. Карточки уже доступны; продолжаю проверку."
            ),
            result_json=json.dumps(json_safe(state), ensure_ascii=False),
            error="",
        )
    except Exception as error:
        retry_count = int(state.get("retry_count") or 0) + 1
        state["retry_count"] = retry_count
        can_retry = retryable and retry_count <= 3
        update_search_run(
            run_id,
            status="queued" if can_retry else "failed",
            stage=f"retry-{batch_index + 1}" if can_retry else "error",
            detail=(
                "Источник временно не ответил. Прогресс сохранён; плановый поиск продолжится автоматически."
                if can_retry else
                "Пакет поиска не завершён. Уже сохранённые карточки не удалены."
            ),
            error=live_jobs.safe_network_error(error),
            result_json=json.dumps(json_safe(state), ensure_ascii=False),
        )
    return search_run_payload(run_id, user_id)


@app.post("/api/search", status_code=status.HTTP_200_OK)
def start_search(
    body: SearchRequest,
    user_id: int = Depends(current_user),
) -> dict[str, Any]:
    running = db.query(
        """
        SELECT run_id,status,stage,updated_at FROM search_runs_v2
        WHERE user_id=? AND status IN ('queued','running')
        ORDER BY created_at DESC LIMIT 1
        """,
        (user_id,),
    )
    if not running.empty:
        # A free serverless instance can be stopped after it writes the queue
        # row but before a background task begins.  Such a row must never lock
        # the person out of all later searches.  A genuinely active request
        # completes in seconds, therefore a 12-minute row is safe to mark as
        # deferred and replace with a bounded foreground search.
        active = running.iloc[0]
        updated = live_jobs.parse_datetime(active.get("updated_at"))
        now = datetime.now(timezone.utc)
        age_seconds = (now - updated).total_seconds() if updated else 999999
        # A queued row has not actually begun collecting.  If a serverless
        # process dies before its first progress update, waiting twelve
        # minutes makes the button appear broken.  A real foreground pass
        # changes queued -> running almost immediately, so 45 seconds is a
        # safe recovery window for queued work; running work keeps the longer
        # allowance.
        active_stage = str(active.get("stage") or "")
        active_status = str(active.get("status") or "")
        if active_stage == "dispatched":
            active_limit = 60
        elif active_stage == "quick":
            active_limit = 90
        elif active_status == "queued":
            active_limit = 45
        else:
            active_limit = 5 * 60
        if age_seconds < active_limit:
            return search_run_payload(str(active["run_id"]), user_id)
        db.execute(
            "UPDATE search_runs_v2 SET status='failed',stage='deferred',detail=?,error=?,updated_at=? WHERE run_id=? AND user_id=?",
            (
                "Старый запуск был остановлен хостингом; создан новый быстрый запуск.",
                "stale serverless queue",
                now_iso(),
                str(active["run_id"]),
                user_id,
            ),
        )
    run_id = str(uuid.uuid4())
    created = now_iso()
    db.execute(
        """
        INSERT INTO search_runs_v2(run_id,user_id,status,stage,detail,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?)
        """,
        (
            run_id,
            user_id,
            "running",
            "ready",
            "Поиск подготовлен. Начинаю последовательную проверку источников.",
            created,
            created,
        ),
    )
    update_search_run(
        run_id,
        result_json=json.dumps(interactive_search_initial_state(), ensure_ascii=False),
    )
    return search_run_payload(run_id, user_id)


@app.post("/api/search/{run_id}/continue", status_code=status.HTTP_200_OK)
def continue_search(run_id: str, user_id: int = Depends(current_user)) -> dict[str, Any]:
    return continue_interactive_search(run_id, user_id)


@app.get("/api/search/{run_id}")
def search_status(run_id: str, user_id: int = Depends(current_user)) -> dict[str, Any]:
    return search_run_payload(run_id, user_id)


def tracker_language(vacancy: dict[str, Any]) -> str:
    text = " ".join(str(vacancy.get(key) or "") for key in (
        "position", "title", "company", "client", "language", "description", "source_snapshot",
    ))
    language = str(vacancy.get("language") or "").upper()
    return "Русская коммуникация" if language.startswith("RU") or len(re.findall(r"[а-яё]", text, re.IGNORECASE)) >= 8 else "Английская коммуникация"


def ensure_tracker_entry(
    query,
    execute,
    user_id: int,
    vacancy_id: int,
    *,
    workflow_status: str = "in_progress",
) -> int:
    frame = query(
        """
        SELECT v.id vacancy_id,v.candidate_id,c.name candidate,v.position,v.company,
          v.salary_text,v.language,v.link,v.source,v.source_snapshot,v.score,
          COALESCE(v.favorite,0) favorite,v.work_type
        FROM vacancies v JOIN candidates c ON c.id=v.candidate_id AND c.user_id=v.user_id
        WHERE v.id=? AND v.user_id=?
        """,
        (vacancy_id, user_id),
    )
    if frame.empty:
        raise HTTPException(status_code=404, detail="Карточка вакансии не найдена.")
    vacancy = frame.iloc[0].to_dict()
    applied_at = datetime.now(UTC).date().isoformat() if workflow_status == "sent" else ""
    now = now_iso()
    score = int(vacancy.get("score") or 0)
    priority = "Золотая" if score >= live_jobs.GOLDEN_SCORE else "Высокая" if score >= live_jobs.REVIEW_SCORE else "Проверить"
    work_type = str(vacancy.get("work_type") or "").lower()
    item_type = "Подработка" if any(marker in work_type for marker in ("part-time", "freelance", "contract", "проект")) else "Вакансия"
    formal = build_application_draft(user_id, vacancy_id, tone_override="formal", length_override="compact")
    friendly = build_application_draft(user_id, vacancy_id, tone_override="friendly", length_override="compact")
    detailed = build_application_draft(user_id, vacancy_id, tone_override="formal", length_override="detailed")
    execute(
        """
        INSERT INTO application_tracker(
          user_id,vacancy_id,candidate_id,candidate,applied_at,position,company,
          salary_range,language,vacancy_link,vacancy_source,sync_status,updated_at,
          item_type,score,priority,favorite,status,cover_formal,cover_friendly,
          cover_detailed,from_email
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(user_id,vacancy_id) DO UPDATE SET
          candidate_id=excluded.candidate_id,candidate=excluded.candidate,
          applied_at=CASE WHEN excluded.applied_at<>'' THEN excluded.applied_at ELSE application_tracker.applied_at END,
          position=excluded.position,company=excluded.company,
          salary_range=CASE WHEN application_tracker.salary_range='' THEN excluded.salary_range ELSE application_tracker.salary_range END,
          language=excluded.language,vacancy_link=excluded.vacancy_link,
          vacancy_source=excluded.vacancy_source,item_type=excluded.item_type,
          score=excluded.score,priority=excluded.priority,favorite=excluded.favorite,
          status=excluded.status,cover_formal=excluded.cover_formal,
          cover_friendly=excluded.cover_friendly,cover_detailed=excluded.cover_detailed,
          from_email=excluded.from_email,sync_status='pending',updated_at=excluded.updated_at
        """,
        (
            user_id, vacancy_id, int(vacancy["candidate_id"]), str(vacancy.get("candidate") or ""),
            applied_at, str(vacancy.get("position") or ""), str(vacancy.get("company") or ""),
            str(vacancy.get("salary_text") or ""), tracker_language(vacancy),
            str(vacancy.get("link") or ""), str(vacancy.get("source") or ""), "pending", now,
            item_type, score, priority, int(vacancy.get("favorite") or 0),
            "Отправлено" if workflow_status == "sent" else "В работу",
            formal["cover_letter"], friendly["cover_letter"], detailed["cover_letter"],
            str(formal.get("from_email") or ""),
        ),
    )
    tracker = query(
        "SELECT id FROM application_tracker WHERE user_id=? AND vacancy_id=?",
        (user_id, vacancy_id),
    )
    return int(tracker.iloc[0]["id"])


def google_sheets_settings_payload(user_id: int, *, query=None) -> dict[str, Any]:
    read = query or db.query
    spreadsheet_url = public_release.read_setting(read, user_id, "google_sheets_spreadsheet_url", "")
    webhook_url = public_release.read_setting(read, user_id, "google_sheets_webhook_url", "")
    secret = public_release.read_setting(read, user_id, "google_sheets_webhook_secret", "")
    return {
        "spreadsheet_url": spreadsheet_url,
        "webhook_url": webhook_url,
        "secret_configured": bool(secret),
        "connected": bool(webhook_url and secret),
        "last_sync_at": public_release.read_setting(read, user_id, "google_sheets_last_sync_at", ""),
        "last_sync_status": public_release.read_setting(read, user_id, "google_sheets_last_sync_status", ""),
        "last_sync_detail": public_release.read_setting(read, user_id, "google_sheets_last_sync_detail", ""),
    }


def tracker_rows(user_id: int, *, query=None) -> list[dict[str, Any]]:
    read = query or db.query
    frame = read(
        """
        SELECT id,vacancy_id,candidate_id,candidate,applied_at,response_at,position,company,
          result,comments,salary_range,language,vacancy_link,vacancy_source,
          sync_status,sync_error,synced_at,updated_at,item_type,score,priority,
          favorite,status,cover_formal,cover_friendly,cover_detailed,from_email
        FROM application_tracker WHERE user_id=?
        ORDER BY favorite DESC,score DESC,applied_at DESC,id DESC
        """,
        (user_id,),
    )
    return [] if frame.empty else json_safe(frame.to_dict("records"))


def _sheet_status(status_value: Any, favorite: Any = 0) -> str:
    status_key = str(status_value or "found")
    labels = {
        "in_progress": "В работу",
        "ready": "В работу",
        "sent": "Отправлено",
        "approved": "В работу",
        "later": "Позже",
        "skip": "Скрыто",
        "done": "Отправлено",
    }
    return labels.get(status_key, "Избранное" if int(favorite or 0) else "Новая")


def _sheet_priority(item: dict[str, Any]) -> dict[str, Any]:
    if item.get("priority_label"):
        return {
            "priority_label": str(item.get("priority_label")),
            "priority_rank": int(item.get("priority_rank") or 0),
        }
    raw = {
        "title": item.get("position") or item.get("title"),
        "description": item.get("description") or item.get("requirements_note"),
        "tags": item.get("matches") or item.get("strengths"),
        "location": item.get("remote_location") or item.get("location"),
        "salary": item.get("salary_text") or item.get("pay_text"),
        "score": int(item.get("score") or 0),
    }
    return live_jobs.job_priority(raw)


def _sheet_cover_variants(
    item: dict[str, Any],
    candidate: dict[str, Any],
    skills: list[Any],
    social_links: list[dict[str, Any]],
) -> tuple[str, str, str]:
    """Create three review-ready variants with the same rules as the API draft."""
    language = "RU" if tracker_language(item) == "Русская коммуникация" else "EN"
    return _compose_cover_variants(item, candidate, skills, social_links, language)


def sheet_queue_rows(user_id: int) -> list[dict[str, Any]]:
    with db.transaction() as (query, _execute):
        candidates = query("SELECT id,name,contact_email,salary_min FROM candidates WHERE user_id=? ORDER BY id", (user_id,))
        candidate_records = [] if candidates.empty else candidates.to_dict("records")
        candidate_ids = [int(row["id"]) for row in candidate_records]
        candidate_map = {int(row["id"]): row for row in candidate_records}
        skills_frame = query("SELECT candidate_id,skill FROM skills WHERE user_id=? ORDER BY id", (user_id,))
        links_frame = query(
            "SELECT candidate_id,platform,url FROM social_links WHERE user_id=? AND COALESCE(show_global,1)=1 ORDER BY id",
            (user_id,),
        )
        skills_by_candidate: dict[int, list[Any]] = {candidate_id: [] for candidate_id in candidate_ids}
        for skill_row in ([] if skills_frame.empty else skills_frame.to_dict("records")):
            skills_by_candidate.setdefault(int(skill_row["candidate_id"]), []).append(skill_row.get("skill"))
        links_by_candidate: dict[int, list[dict[str, Any]]] = {candidate_id: [] for candidate_id in candidate_ids}
        for link_row in ([] if links_frame.empty else links_frame.to_dict("records")):
            links_by_candidate.setdefault(int(link_row["candidate_id"]), []).append(link_row)
        jobs = live_jobs.latest_jobs(query, user_id, candidate_ids, limit=260)
        gigs = query(
            """SELECT g.id,g.candidate_id,c.name candidate,c.contact_email,g.title,g.client,g.source,g.link,
                 g.location,g.category,g.work_format,g.pay_text,g.description,g.contacts_json,g.score,g.status,
                 COALESCE(g.favorite,0) favorite,g.posted_at,g.requirements_note
               FROM side_gigs g JOIN candidates c ON c.id=g.candidate_id AND c.user_id=g.user_id
               WHERE g.user_id=? AND COALESCE(g.is_active,1)=1 AND COALESCE(g.status,'found')<>'skip'
               ORDER BY COALESCE(g.favorite,0) DESC,g.score DESC,g.id DESC LIMIT 120""",
            (user_id,),
        )

    rows: list[dict[str, Any]] = []
    known_links: set[tuple[int, str]] = set()
    for item in jobs:
        candidate_id = int(item.get("candidate_id") or 0)
        candidate = candidate_map.get(candidate_id, {})
        candidate_name = str(item.get("candidate") or candidate.get("name") or "")
        link = str(item.get("link") or "")
        known_links.add((candidate_id, live_jobs.canonical_url(link)))
        candidate = {**candidate, "name": candidate_name}
        formal, friendly, detailed = _sheet_cover_variants(
            item, candidate, skills_by_candidate.get(candidate_id, []), links_by_candidate.get(candidate_id, []),
        )
        contacts = item.get("contacts") or {}
        contact_text = ", ".join(
            str(value)
            for value in [
                *((contacts.get("emails") or [])[:3]),
                *((contacts.get("telegram") or [])[:3]),
                *((contacts.get("phones") or [])[:2]),
            ]
            if value
        )
        score = int(item.get("score") or 0)
        work_type = str(item.get("work_type") or "")
        priority = _sheet_priority(item)
        rows.append({
            "id": int(item.get("id") or 0),
            "candidate": candidate_name,
            "item_type": "Подработка" if any(marker in work_type.lower() for marker in ("part-time", "freelance", "contract", "проект")) else "Вакансия",
            "priority": priority["priority_label"],
            "priority_rank": priority["priority_rank"],
            "golden": score >= live_jobs.GOLDEN_SCORE,
            "score": score,
            "status": _sheet_status(item.get("status"), item.get("favorite")),
            "favorite": int(item.get("favorite") or 0),
            "posted_at": str(item.get("posted_at") or "")[:10],
            "position": str(item.get("position") or ""),
            "company": str(item.get("company") or ""),
            "work_format": work_type or str(item.get("remote_location") or ""),
            "location": str(item.get("remote_location") or ""),
            "salary_range": str(item.get("salary_text") or ""),
            "language": tracker_language(item),
            "matches": str(item.get("strengths") or "")[:900],
            "source": str(item.get("source") or ""),
            "link": link,
            "contacts": contact_text,
            "from_email": str(candidate.get("contact_email") or ""),
            "cover_formal": formal,
            "cover_friendly": friendly,
            "cover_detailed": detailed,
            "comments": str(item.get("risk") or "")[:900],
        })
    cutoff = datetime.now(UTC) - timedelta(days=30)
    for item in ([] if gigs.empty else gigs.to_dict("records")):
        candidate_id = int(item.get("candidate_id") or 0)
        link = str(item.get("link") or "")
        posted = live_jobs.parse_datetime(item.get("posted_at"))
        if posted and posted < cutoff:
            continue
        if (candidate_id, live_jobs.canonical_url(link)) in known_links:
            continue
        candidate_name = str(item.get("candidate") or "")
        candidate = {**candidate_map.get(candidate_id, {}), "name": candidate_name}
        formal, friendly, detailed = _sheet_cover_variants(
            item, candidate, skills_by_candidate.get(candidate_id, []), links_by_candidate.get(candidate_id, []),
        )
        try:
            contacts = json.loads(str(item.get("contacts_json") or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            contacts = {}
        contact_text = ", ".join(
            str(value)
            for value in [*((contacts.get("emails") or [])[:3]), *((contacts.get("telegram") or [])[:3])]
            if value
        )
        score = int(item.get("score") or 0)
        favorite = int(item.get("favorite") or 0)
        priority = _sheet_priority(item)
        rows.append({
            "id": 1_000_000 + int(item.get("id") or 0),
            "candidate": candidate_name,
            "item_type": str(item.get("category") or "Подработка"),
            "priority": priority["priority_label"],
            "priority_rank": priority["priority_rank"],
            "golden": score >= live_jobs.GOLDEN_SCORE,
            "score": score,
            "status": _sheet_status(item.get("status"), favorite),
            "favorite": favorite,
            "posted_at": str(item.get("posted_at") or "")[:10],
            "position": str(item.get("title") or ""),
            "company": str(item.get("client") or ""),
            "work_format": str(item.get("work_format") or ""),
            "location": str(item.get("location") or ""),
            "salary_range": str(item.get("pay_text") or ""),
            "language": tracker_language(item),
            "matches": str(item.get("requirements_note") or "")[:900],
            "source": str(item.get("source") or ""),
            "link": link,
            "contacts": contact_text,
            "from_email": str(item.get("contact_email") or ""),
            "cover_formal": formal,
            "cover_friendly": friendly,
            "cover_detailed": detailed,
            "comments": str(item.get("requirements_note") or "")[:900],
        })
    rows.sort(
        key=lambda item: (
            int(item["favorite"]), int(item.get("priority_rank") or 0),
            int(item["score"]), item["posted_at"],
        ),
        reverse=True,
    )
    return rows[:100]


def sync_google_sheet_snapshot(user_id: int) -> dict[str, Any]:
    settings = google_sheets_settings_payload(user_id)
    if not settings["connected"]:
        return {"status": "pending", "rows": 0, "detail": "Google Sheets ещё не подключён."}
    secret = public_release.read_setting(db.query, user_id, "google_sheets_webhook_secret", "")
    rows = sheet_queue_rows(user_id)
    try:
        response = requests.post(
            str(settings["webhook_url"]),
            json={"secret": secret, "action": "snapshot", "rows": rows},
            timeout=(10, 6),
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("ok") is False:
            raise RuntimeError(str(payload.get("error") or "Google Sheets rejected the snapshot"))
        result = {"status": "synced", "rows": len(rows), "detail": "Очереди кандидатов обновлены."}
        public_release.put_setting(db.query, db.execute, user_id, "google_sheets_last_sync_at", now_iso())
        public_release.put_setting(db.query, db.execute, user_id, "google_sheets_last_sync_status", "synced")
        public_release.put_setting(db.query, db.execute, user_id, "google_sheets_last_sync_detail", f"Передано строк: {len(rows)}")
        return result
    except requests.exceptions.ReadTimeout:
        # Apps Script keeps processing an accepted request after the caller's
        # read timeout. Report that state honestly and keep search endpoints
        # responsive; the workbook is updated by the in-flight execution.
        detail = f"Google принял {len(rows)} строк; таблица обновляется в фоне."
        public_release.put_setting(db.query, db.execute, user_id, "google_sheets_last_sync_at", now_iso())
        public_release.put_setting(db.query, db.execute, user_id, "google_sheets_last_sync_status", "queued")
        public_release.put_setting(db.query, db.execute, user_id, "google_sheets_last_sync_detail", detail)
        return {"status": "queued", "rows": len(rows), "detail": detail}
    except Exception as error:
        detail = live_jobs.safe_network_error(error)[:500]
        public_release.put_setting(db.query, db.execute, user_id, "google_sheets_last_sync_at", now_iso())
        public_release.put_setting(db.query, db.execute, user_id, "google_sheets_last_sync_status", "error")
        public_release.put_setting(db.query, db.execute, user_id, "google_sheets_last_sync_detail", detail)
        return {"status": "error", "rows": 0, "detail": detail}


def pull_google_sheet_tracker(user_id: int) -> dict[str, Any]:
    settings = google_sheets_settings_payload(user_id)
    if not settings["connected"]:
        return {"status": "pending", "updated": 0}
    secret = public_release.read_setting(db.query, user_id, "google_sheets_webhook_secret", "")
    try:
        response = requests.post(
            str(settings["webhook_url"]),
            json={"secret": secret, "action": "pull"},
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("ok") is False:
            raise RuntimeError(str(payload.get("error") or "Google Sheets rejected the pull"))
        updated = 0
        allowed_results = {"", *TRACKER_RESULTS}
        for item in payload.get("rows") or []:
            try:
                tracker_id = int(item.get("number") or 0)
            except (TypeError, ValueError):
                continue
            result = str(item.get("result") or "")
            response_at = str(item.get("response_at") or "")[:10]
            if result not in allowed_results or (response_at and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", response_at)):
                continue
            db.execute(
                """UPDATE application_tracker SET response_at=?,result=?,comments=?,salary_range=?,
                   sync_status='synced',sync_error='',synced_at=?,updated_at=? WHERE id=? AND user_id=?""",
                (
                    response_at,
                    result,
                    str(item.get("comments") or "")[:3000],
                    str(item.get("salary_range") or "")[:500],
                    now_iso(),
                    now_iso(),
                    tracker_id,
                    user_id,
                ),
            )
            updated += 1
        return {"status": "synced", "updated": updated}
    except Exception as error:
        return {"status": "error", "updated": 0, "detail": live_jobs.safe_network_error(error)[:500]}


def sync_tracker_record(user_id: int, tracker_id: int) -> dict[str, Any]:
    settings = google_sheets_settings_payload(user_id)
    if not settings["connected"]:
        return {"id": tracker_id, "status": "pending", "detail": "Google Sheets ещё не подключён."}
    secret_frame = db.query("SELECT value FROM settings WHERE user_id=? AND key=?", (user_id, "google_sheets_webhook_secret"))
    secret = "" if secret_frame.empty else str(secret_frame.iloc[0].get("value") or "")
    frame = db.query("SELECT * FROM application_tracker WHERE id=? AND user_id=?", (tracker_id, user_id))
    if frame.empty:
        return {"id": tracker_id, "status": "missing", "detail": "Строка отклика не найдена."}
    row = frame.iloc[0].to_dict()
    payload = {
        "secret": secret,
        "action": "upsert",
        "row": {
            "number": int(row["id"]),
            "candidate": str(row.get("candidate") or ""),
            "applied_at": str(row.get("applied_at") or ""),
            "response_at": str(row.get("response_at") or ""),
            "position": str(row.get("position") or ""),
            "company": str(row.get("company") or ""),
            "result": str(row.get("result") or ""),
            "comments": str(row.get("comments") or ""),
            "salary_range": str(row.get("salary_range") or ""),
            "language": str(row.get("language") or ""),
            "item_type": str(row.get("item_type") or "Вакансия"),
            "score": int(row.get("score") or 0),
            "priority": str(row.get("priority") or ""),
            "favorite": int(row.get("favorite") or 0),
            "status": str(row.get("status") or "В работу"),
            "vacancy_link": str(row.get("vacancy_link") or ""),
            "vacancy_source": str(row.get("vacancy_source") or ""),
            "cover_formal": str(row.get("cover_formal") or ""),
            "cover_friendly": str(row.get("cover_friendly") or ""),
            "cover_detailed": str(row.get("cover_detailed") or ""),
            "from_email": str(row.get("from_email") or ""),
        },
    }
    try:
        response = requests.post(str(settings["webhook_url"]), json=payload, timeout=10)
        response.raise_for_status()
        result = response.json() if "application/json" in str(response.headers.get("content-type") or "") else {"ok": True}
        if result.get("ok") is False:
            raise RuntimeError(str(result.get("error") or "Google Sheets rejected the row"))
        db.execute(
            "UPDATE application_tracker SET sync_status='synced',sync_error='',synced_at=?,updated_at=? WHERE id=? AND user_id=?",
            (now_iso(), now_iso(), tracker_id, user_id),
        )
        return {"id": tracker_id, "status": "synced", "detail": "Строка отправлена в Google Sheets."}
    except Exception as error:
        detail = live_jobs.safe_network_error(error)[:500]
        db.execute(
            "UPDATE application_tracker SET sync_status='error',sync_error=?,updated_at=? WHERE id=? AND user_id=?",
            (detail, now_iso(), tracker_id, user_id),
        )
        return {"id": tracker_id, "status": "error", "detail": detail}


def special_attention_payload(user_id: int, *, query=None) -> dict[str, Any]:
    read = query or db.query
    candidates = read("SELECT id FROM candidates WHERE user_id=? ORDER BY id", (user_id,))
    candidate_ids = [] if candidates.empty else [int(value) for value in candidates["id"].tolist()]
    jobs = live_jobs.latest_jobs(read, user_id, candidate_ids, limit=400)
    jobs = [job for job in jobs if str(job.get("source") or "") in live_jobs.SPECIAL_SOURCE_NAMES]
    cache = read(
        "SELECT source,fetched_at,expires_at,error,payload FROM live_source_cache WHERE source IN (?,?,?)",
        tuple(live_jobs.SPECIAL_SOURCE_NAMES),
    )
    cache_by_source = {} if cache.empty else {str(row["source"]): row for row in cache.to_dict("records")}
    sources = []
    for name in live_jobs.SPECIAL_SOURCE_NAMES:
        row = cache_by_source.get(name, {})
        try:
            cached_jobs = json.loads(str(row.get("payload") or "[]"))
            checked_count = live_jobs.source_checked_count(cached_jobs if isinstance(cached_jobs, list) else [])
        except (TypeError, ValueError, json.JSONDecodeError):
            checked_count = 0
        matched = sum(1 for job in jobs if str(job.get("source") or "") == name and str(job.get("status") or "") != "skip")
        error = str(row.get("error") or "")
        if error:
            source_detail = error
        elif not row:
            source_detail = "Источник ещё не проверялся в этом кабинете."
        elif checked_count < 50:
            source_detail = (
                f"Просмотрено {checked_count} из целевых 50: "
                f"столько карточек отдал публичный источник; подошло {matched}."
            )
        else:
            source_detail = f"Просмотрено 50; международный фильтр пропустил {matched}."
        sources.append({
            "name": name,
            "url": str(live_jobs.SOURCE_SPECS[name]["url"]),
            "status": "error" if error and not checked_count else "checked" if row else "pending",
            "checked": checked_count,
            "target": 50,
            "matched": matched,
            "last_checked_at": str(row.get("fetched_at") or ""),
            "detail": source_detail,
        })
    return json_safe({"jobs": jobs, "sources": sources, "checked_at": now_iso()})


@app.get("/api/special-attention")
def special_attention(user_id: int = Depends(current_user)) -> dict[str, Any]:
    return special_attention_payload(user_id)


@app.post("/api/special-attention/search")
def search_special_attention(user_id: int = Depends(current_user)) -> dict[str, Any]:
    with db.transaction() as (query, execute):
        candidates = query("SELECT id FROM candidates WHERE user_id=? ORDER BY id", (user_id,))
        candidate_ids = [] if candidates.empty else [int(value) for value in candidates["id"].tolist()]
        if not candidate_ids:
            raise HTTPException(status_code=400, detail="Сначала добавьте хотя бы один профиль кандидата.")
        result = live_jobs.run_search(
            query, execute, user_id, candidate_ids,
            min_score=live_jobs.BROAD_REVIEW_SCORE, max_age_days=30, remote_only=True,
            job_types={"full-time", "contract", "part-time", "freelance"},
            limit_per_candidate=35, force_refresh=True, use_open_model=False,
            source_names=live_jobs.SPECIAL_SOURCE_NAMES, max_source_wait_seconds=28.0,
        )
        public_release.put_setting(query, execute, user_id, "last_special_search_at", now_iso())
    payload = special_attention_payload(user_id)
    payload["summary"] = {
        "checked": sum(int(item.get("checked") or 0) for item in payload.get("sources") or []),
        "saved": len(result.get("saved") or []),
        "archived": int(result.get("archived_count") or 0),
    }
    payload["sheet_sync"] = sync_google_sheet_snapshot(user_id)
    return json_safe(payload)


@app.get("/api/application-tracker")
def application_tracker(user_id: int = Depends(current_user)) -> dict[str, Any]:
    return {
        "rows": tracker_rows(user_id),
        "results": list(TRACKER_RESULTS),
        "google_sheets": google_sheets_settings_payload(user_id),
    }


@app.patch("/api/application-tracker/{tracker_id}")
def update_application_tracker(
    tracker_id: int,
    body: TrackerUpdateRequest,
    user_id: int = Depends(current_user),
) -> dict[str, Any]:
    if body.result is not None and body.result not in {"", *TRACKER_RESULTS}:
        raise HTTPException(status_code=400, detail="Выберите результат из списка.")
    assignments: list[str] = []
    values: list[Any] = []
    for field in ("response_at", "result", "comments", "salary_range"):
        value = getattr(body, field)
        if value is not None:
            assignments.append(f"{field}=?")
            values.append(value.strip())
    if not assignments:
        raise HTTPException(status_code=400, detail="Нет изменений для сохранения.")
    assignments.extend(("sync_status='pending'", "updated_at=?"))
    values.extend((now_iso(), tracker_id, user_id))
    db.execute(
        f"UPDATE application_tracker SET {','.join(assignments)} WHERE id=? AND user_id=?",
        tuple(values),
    )
    sync = sync_tracker_record(user_id, tracker_id)
    row = db.query("SELECT * FROM application_tracker WHERE id=? AND user_id=?", (tracker_id, user_id))
    if row.empty:
        raise HTTPException(status_code=404, detail="Строка отклика не найдена.")
    return {"row": json_safe(row.iloc[0].to_dict()), "sync": sync}


@app.put("/api/application-tracker/google-sheets")
def save_google_sheets_settings(
    body: GoogleSheetsSettingsRequest,
    user_id: int = Depends(current_user),
) -> dict[str, Any]:
    webhook_url = body.webhook_url.strip()
    spreadsheet_url = body.spreadsheet_url.strip()
    if webhook_url:
        parsed = urlsplit(webhook_url)
        if parsed.scheme != "https" or parsed.hostname not in {"script.google.com", "script.googleusercontent.com"}:
            raise HTTPException(status_code=400, detail="Укажите HTTPS-ссылку опубликованного Google Apps Script Web App.")
    if spreadsheet_url and "docs.google.com/spreadsheets/" not in spreadsheet_url:
        raise HTTPException(status_code=400, detail="Укажите ссылку на Google Таблицу.")
    with db.transaction() as (query, execute):
        public_release.put_setting(query, execute, user_id, "google_sheets_spreadsheet_url", spreadsheet_url)
        public_release.put_setting(query, execute, user_id, "google_sheets_webhook_url", webhook_url)
        if body.webhook_secret.strip():
            public_release.put_setting(query, execute, user_id, "google_sheets_webhook_secret", body.webhook_secret.strip())
    return google_sheets_settings_payload(user_id)


@app.post("/api/application-tracker/sync")
def sync_application_tracker(user_id: int = Depends(current_user)) -> dict[str, Any]:
    frame = db.query(
        "SELECT id FROM application_tracker WHERE user_id=? AND sync_status<>'synced' ORDER BY id LIMIT 100",
        (user_id,),
    )
    results = [] if frame.empty else [sync_tracker_record(user_id, int(value)) for value in frame["id"].tolist()]
    snapshot = sync_google_sheet_snapshot(user_id)
    pulled = pull_google_sheet_tracker(user_id)
    return {
        "results": results,
        "synced": sum(1 for item in results if item["status"] == "synced"),
        "snapshot": snapshot,
        "pulled": pulled,
    }


@app.patch("/api/jobs/{vacancy_id}")
def update_job_status(
    vacancy_id: int,
    body: JobStatusRequest,
    user_id: int = Depends(current_user),
) -> dict[str, Any]:
    tracker_id: int | None = None
    with db.transaction() as (query, execute):
        frame = query(
            "SELECT id,status FROM vacancies WHERE id=? AND user_id=?",
            (vacancy_id, user_id),
        )
        if frame.empty:
            raise HTTPException(status_code=404, detail="Карточка вакансии не найдена.")
        if body.status is None and body.favorite is None:
            raise HTTPException(status_code=400, detail="Выберите действие для вакансии.")
        assignments: list[str] = []
        values: list[Any] = []
        if body.status is not None:
            assignments.append("status=?")
            values.append(body.status)
        if body.favorite is not None:
            assignments.append("favorite=?")
            values.append(int(body.favorite))
        values.extend([vacancy_id, user_id])
        execute(
            f"UPDATE vacancies SET {','.join(assignments)} WHERE id=? AND user_id=?",
            tuple(values),
        )
        if body.status in {"in_progress", "sent"}:
            tracker_id = ensure_tracker_entry(
                query,
                execute,
                user_id,
                vacancy_id,
                workflow_status=str(body.status or "in_progress"),
            )
        updated = query(
            "SELECT id,status,COALESCE(favorite,0) favorite FROM vacancies WHERE id=? AND user_id=?",
            (vacancy_id, user_id),
        )
    payload = json_safe(updated.iloc[0].to_dict())
    if tracker_id is not None:
        payload["tracker_sync"] = sync_tracker_record(user_id, tracker_id)
    payload["sheet_sync"] = sync_google_sheet_snapshot(user_id)
    return payload


@app.get("/api/jobs/{vacancy_id}/preview")
def job_preview(vacancy_id: int, user_id: int = Depends(current_user)) -> dict[str, Any]:
    """Return the saved, structured public vacancy snapshot for its owner only."""
    frame = db.query(
        """SELECT id,company,position,source,link,remote_location,salary_text,posted_at,
                  employer_email,employer_contact,source_snapshot,industry_tag,risk,
                  final_salary_advice,ai_analysis,company_rating
           FROM vacancies WHERE id=? AND user_id=?""",
        (vacancy_id, user_id),
    )
    if frame.empty:
        raise HTTPException(status_code=404, detail="Карточка вакансии не найдена.")
    row = frame.iloc[0].to_dict()
    try:
        snapshot = json.loads(str(row.get("source_snapshot") or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        snapshot = {}
    description = str(snapshot.get("description") or "")
    details = live_jobs.vacancy_presentation({
        "title": row.get("position"), "company": row.get("company"), "description": description,
        "tags": snapshot.get("tags") or "", "location": row.get("remote_location"),
        "salary": row.get("salary_text"), "source": row.get("source"), "url": row.get("link"),
    })
    return json_safe({
        **{key: row.get(key) for key in ("id", "company", "position", "source", "link", "remote_location", "salary_text", "posted_at")},
        "is_active": 1,
        "active_checked_at": snapshot.get("verified_at") or "",
        "links": snapshot.get("links") or ([{"url": row.get("link"), "source": row.get("source")} ] if row.get("link") else []),
        "preview": {"description": description, "tags": details["tags"], "benefits": details["benefits"]},
        **details,
    })


def candidate_search_profile(user_id: int, candidate_id: int) -> dict[str, Any]:
    frame = db.query(
        "SELECT target_title,english_level,desired_countries,salary_min,notes,hard_exclude,hard_require FROM candidates WHERE id=? AND user_id=?",
        (candidate_id, user_id),
    )
    if frame.empty:
        raise HTTPException(status_code=404, detail="Профиль кандидата не найден.")
    profile = frame.iloc[0].to_dict()
    skills = db.query("SELECT skill FROM skills WHERE user_id=? AND candidate_id=? ORDER BY skill", (user_id, candidate_id))
    profile["skills"] = [] if skills.empty else skills["skill"].tolist()
    resume = db.query("SELECT content FROM resumes WHERE user_id=? AND candidate_id=? ORDER BY id DESC LIMIT 1", (user_id, candidate_id))
    profile["resume_text"] = "" if resume.empty else str(resume.iloc[0]["content"] or "")
    profile["allow_vietnam_hybrid"] = True
    profile["base_country"] = "Vietnam"
    return profile


@app.post("/api/vacancies/manual", status_code=status.HTTP_201_CREATED)
def add_manual_vacancy(body: ManualVacancyRequest, user_id: int = Depends(current_user)) -> dict[str, Any]:
    """Add a user-provided public vacancy with the same strict ranking as feeds."""
    profile = candidate_search_profile(user_id, body.candidate_id)
    job = {
        "title": body.position.strip(), "company": body.company.strip(), "url": body.link.strip(),
        "source": body.source.strip() or "Ручная проверка", "location": body.location.strip(),
        "description": body.description.strip(), "salary": body.salary_text.strip(), "posted_at": body.posted_at.strip(),
        "remote": any(item in f"{body.location} {body.description}".lower() for item in ("remote", "удал")),
        "job_type": "full-time",
    }
    score, reasons, blocked = live_jobs.score_job(job, profile)
    presentation = live_jobs.vacancy_presentation(job)
    snapshot = {
        "description": body.description.strip(), "tags": presentation["tags"], "verified_at": now_iso(),
        "links": ([{"url": body.link.strip(), "source": body.source.strip() or "Ручная проверка", "posted_at": body.posted_at.strip()}] if body.link.strip() else []),
    }
    db.execute(
        """INSERT INTO vacancies(user_id,candidate_id,fetched_at,posted_at,source,service,company,position,link,
           remote_location,salary_text,score,status,strengths,risk,blocked_reason,employer_email,employer_contact,
           source_snapshot,industry_tag,work_type,final_salary_advice,ai_analysis,ai_review_status)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (user_id, body.candidate_id, now_iso(), body.posted_at.strip(), job["source"], job["source"], body.company.strip(), body.position.strip(), body.link.strip(),
         body.location.strip(), body.salary_text.strip(), int(score), "found", "; ".join(reasons), presentation["risk"], blocked,
         ", ".join(presentation["contacts"]["emails"]), ", ".join(presentation["contacts"]["phones"] + presentation["contacts"]["telegram"]),
         json.dumps(snapshot, ensure_ascii=False), presentation["sector"], "manual", f"Минимальный ориентир кандидата: ${int(profile.get('salary_min') or 0)}/мес.",
         "Оценка выполнена локальными правилами: уровень, опыт, география, язык и требования сверены с профилем.", "local"),
    )
    return {"ok": True, "score": int(score), "blocked_reason": blocked}


@app.patch("/api/gigs/{gig_id}")
def update_gig_status(gig_id: int, body: JobStatusRequest, user_id: int = Depends(current_user)) -> dict[str, Any]:
    row = db.query("SELECT id,status,favorite FROM side_gigs WHERE id=? AND user_id=?", (gig_id, user_id))
    if row.empty:
        raise HTTPException(status_code=404, detail="Подработка не найдена.")
    if body.status is None and body.favorite is None:
        raise HTTPException(status_code=400, detail="Выберите действие для подработки.")
    current = row.iloc[0].to_dict()
    next_status = body.status if body.status is not None else str(current.get("status") or "found")
    favorite = int(body.favorite) if body.favorite is not None else int(current.get("favorite") or 0)
    db.execute("UPDATE side_gigs SET status=?,favorite=? WHERE id=? AND user_id=?", (next_status, favorite, gig_id, user_id))
    return {
        "id": gig_id, "status": next_status, "favorite": favorite,
        "sheet_sync": sync_google_sheet_snapshot(user_id),
    }


@app.post("/api/gigs/manual", status_code=status.HTTP_201_CREATED)
def add_manual_gig(body: ManualGigRequest, user_id: int = Depends(current_user)) -> dict[str, Any]:
    profile = candidate_search_profile(user_id, body.candidate_id)
    raw = " ".join((body.title, body.category, body.work_format, body.location, body.description)).lower()
    suitable = any(term in raw for term in ("qa", "test", "support", "визуал", "landscape", "ландшафт", "copy", "текст", "electr", "монтаж", "мастер", "двор", "озелен"))
    vietnam_or_remote = any(term in raw for term in ("vietnam", "вьетнам", "da nang", "danang", "дананг", "hanoi", "хано", "ho chi minh", "удал", "remote", "worldwide"))
    score = min(92, 50 + (20 if suitable else 0) + (12 if vietnam_or_remote else 0) + (8 if body.pay_text.strip() else 0) + (6 if body.link.strip() else 0))
    presentation = live_jobs.vacancy_presentation({"title": body.title, "description": body.description, "location": body.location})
    snapshot = {"links": ([{"url": body.link.strip(), "source": "Ручная проверка", "posted_at": body.posted_at.strip()}] if body.link.strip() else [])}
    db.execute(
        """INSERT INTO side_gigs(user_id,candidate_id,title,client,source,link,location,category,work_format,pay_text,
           description,contacts_json,score,status,posted_at,active_checked_at,is_active,safety_note,requirements_note,source_snapshot)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (user_id, body.candidate_id, body.title.strip(), body.client.strip(), "Добавлено вручную", body.link.strip(), body.location.strip(), body.category.strip(), body.work_format.strip(), body.pay_text.strip(), body.description.strip(),
         json.dumps(presentation["contacts"], ensure_ascii=False), score, "found", body.posted_at.strip(), now_iso(), 1,
         "Проверьте личность заказчика, договорённости об оплате и право работать из Вьетнама до начала.",
         "Это проектная/частичная работа: сверяйте сроки, объём и возможность совмещения.", json.dumps(snapshot, ensure_ascii=False)),
    )
    return {"ok": True, "score": score, "sheet_sync": sync_google_sheet_snapshot(user_id)}


@app.post("/api/gigs/collect")
def collect_gigs(user_id: int = Depends(current_user)) -> dict[str, Any]:
    """Collect only concrete public part-time / contract leads.

    Closed freelance accounts are intentionally not scraped.  This route is
    still useful: it reads the same open feeds as the main search, discards
    channel digests and saves a card only when a specific project page remains.
    """
    with db.transaction() as (query, execute):
        candidates = query("SELECT id,name,english_level FROM candidates WHERE user_id=? ORDER BY id", (user_id,))
        if candidates.empty:
            raise HTTPException(status_code=400, detail="Сначала создайте профиль кандидата.")
        try:
            jobs, diagnostics = live_jobs.collect_live_jobs(
                query, execute, force=True, source_names=live_jobs.FAST_SOURCE_NAMES,
                max_wait_seconds=20,
            )
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Открытые источники подработок временно недоступны.") from exc
        directions = {
            "qa_candidate": re.compile(
                r"visuali[sz]|3d|render|ландшафт|озелен|copywrit|копирайт|transcrib|оцифров|"
                r"manual\s*qa|qa\s*(?:test|testing)|тестиров|product support|customer support|"
                r"customer success|customer service|customer care|community support|client success|"
                r"onboarding|implementation specialist|support operations|uat tester|content reviewer|"
                r"data annotat|ai evaluat|trust (?:and|&) safety|ky[bc] analyst|fraud analyst|virtual assistant",
                re.IGNORECASE,
            ),
            "support_candidate": re.compile(
                r"help.?desk|service.?desk|it support|technical support|product support|customer support|"
                r"customer service|customer care|community support|it technician|support operations|"
                r"application support|manual\s*qa|qa\s*(?:test|testing)|uat tester|website test|"
                r"content reviewer|data annotat|ai evaluat|virtual assistant|электромонтаж|electric|handyman|мастер",
                re.IGNORECASE,
            ),
        }
        eligible: list[dict[str, Any]] = []
        current = datetime.now(timezone.utc)
        for candidate in candidates.to_dict("records"):
            candidate_id, candidate_name = int(candidate["id"]), str(candidate.get("name") or "")
            profile = live_jobs.candidate_profile(query, user_id, candidate_id)
            matcher = directions.get(_candidate_kind(candidate_name), directions["qa_candidate"])
            for job in jobs:
                text = " ".join(str(job.get(key) or "") for key in ("title", "description", "tags", "job_type", "location"))
                posted = live_jobs.parse_datetime(job.get("posted_at"))
                if not posted or current - posted > timedelta(days=30):
                    continue
                if not matcher.search(text) or not SIDE_GIG_SIGNAL_RE.search(text):
                    continue
                if gig_english_blocked(text, str(candidate.get("english_level") or "")):
                    continue
                if live_jobs.hard_block(job, profile):
                    continue
                if not bool(job.get("remote")) and not live_jobs.vietnam_local_job(job):
                    continue
                item = dict(job)
                item["candidate_id"] = candidate_id
                item["score"] = live_jobs.score_job(job, profile)[0]
                eligible.append(item)
        sync = sync_side_gigs_from_jobs(query, execute, user_id, eligible)
    checked = sum(int(item.get("count") or 0) for item in diagnostics)
    changed = sync["saved"] + sync["refreshed"]
    message = (
        f"Подработки обновлены: новых {sync['saved']}, повторно проверено {sync['refreshed']}, устаревших скрыто {sync['archived']}."
        if changed or sync["archived"] else
        "Сегодня среди свежих открытых источников не нашлось конкретных подходящих подработок. Подборки каналов и закрытые фриланс-профили не выдаются как вакансии."
    )
    return {
        "saved": sync["saved"], "refreshed": sync["refreshed"],
        "archived": sync["archived"], "checked": checked, "message": message,
        "sheet_sync": sync_google_sheet_snapshot(user_id),
    }


def internship_pay_priority(salary: str) -> tuple[bool, str]:
    text = str(salary or "").lower()
    if not text or "не указ" in text or "not specified" in text:
        return False, "Оплата не указана"
    amount = live_jobs.salary_floor(salary)
    if amount <= 0:
        return True, "Оплата указана, сумму проверьте вручную"
    if re.search(r"₽|руб|rur|rub", text) and amount < 30000:
        return False, "Оплата слишком символическая для стажировки"
    if re.search(r"\$|usd|доллар", text) and amount < 300:
        return False, "Оплата слишком символическая для стажировки"
    if re.search(r"€|eur|евро", text) and amount < 280:
        return False, "Оплата слишком символическая для стажировки"
    if re.search(r"\$|usd|доллар", text) and amount >= 700:
        return True, "Приоритет: оплата от $700/мес"
    if re.search(r"€|eur|евро", text) and amount >= 650:
        return True, "Приоритет: оплата примерно от $700/мес"
    if re.search(r"₽|руб|rur|rub", text) and amount >= 65000:
        return True, "Приоритет: оплата примерно от $700/мес"
    return True, "Оплата указана, но ниже желательного ориентира $700/мес"


def internship_without_experience(item: dict[str, Any]) -> bool:
    """Admit explicit zero-experience, intern/trainee, or trained entry-level starts."""
    title = str(item.get("title") or item.get("position") or "")
    text = " ".join(
        str(item.get(key) or "")
        for key in ("title", "position", "description", "tags", "requirements_note", "work_format")
    )
    explicit_zero_experience = re.search(
        r"без (?:коммерческого )?опыта|опыт (?:не требуется|не обязателен)|можно без опыта|"
        r"no (?:prior |previous |professional |commercial )?experience(?!\s+(?:in|with|using)\b)(?: required| needed)?|"
        r"experience (?:is )?not required|0\s*[-–—]?\s*1\s*(?:year|years|год|года|лет)|"
        r"старт с нуля|всему научим|обучим с нуля|full training (?:is )?provided|training from scratch",
        text,
        re.I,
    )
    internship_title = re.search(
        r"стаж[её]р|стажиров|internship|\bintern\b|\btrainee\b|apprentice",
        title,
        re.I,
    )
    entry_level_title = re.search(
        r"\bjunior\b|entry[- ]level|graduate|early career|начинающ|\bджуниор\b",
        title,
        re.I,
    )
    training_signal = re.search(
        r"обуч|наставн|mentor|mentorship|onboarding|academy|bootcamp|"
        r"training (?:is )?provided|learn on the job|всему науч|готовы научить",
        text,
        re.I,
    )
    experience_required = re.search(
        r"(?:от\s*)?[1-9]\d*\+?\s*(?:лет|года?|years?)\s+(?:опыта|experience)|"
        r"(?:опыт|experience)\s*(?:от|required|обязател)[^\n,.]{0,20}[1-9]?|"
        r"commercial experience required|опыт работы обязателен",
        text,
        re.I,
    )
    if experience_required and not explicit_zero_experience:
        return False
    return bool(explicit_zero_experience or internship_title or (entry_level_title and training_signal))


def sync_internships_from_jobs(
    query, execute, user_id: int, jobs: list[dict[str, Any]], *, max_age_days: int = 30,
) -> dict[str, int]:
    """Mirror internship/trainee matches from the shared search pass.

    This keeps vacancies, side work, special sources and internships in one
    refresh.  The dedicated internship button may still run a narrower search,
    but it no longer has to fetch the same public feeds for the main update.
    """
    current = datetime.now(timezone.utc)
    cutoff = current - timedelta(days=max_age_days)
    role_markers = re.compile(
        r"qa|test|тестиров|support|help.?desk|technical support|it support|"
        r"техподдерж|поддержк|java|swift|ios|android|frontend|backend|python|"
        r"разработ|developer|программист|service.?desk|cyber|security|безопасност|"
        r"data.?annotat|ai.?evaluat",
        re.I,
    )
    training_markers = re.compile(r"обуч|настав|mentor|mentorship|onboarding|всему науч|научим|training", re.I)
    equipment_markers = re.compile(r"техник|ноутбук|laptop|equipment|оборудован|компьютер|рабочее место", re.I)

    stale = query(
        "SELECT id,posted_at,active_checked_at FROM side_gigs WHERE user_id=? AND category='Стажировка' AND COALESCE(is_active,1)=1",
        (user_id,),
    )
    archived = 0
    for row in ([] if stale.empty else stale.to_dict("records")):
        posted = live_jobs.parse_datetime(row.get("posted_at"))
        checked = live_jobs.parse_datetime(row.get("active_checked_at"))
        if (posted and posted < cutoff) or (not posted and checked and checked < cutoff):
            execute(
                "UPDATE side_gigs SET is_active=0,active_checked_at=? WHERE id=? AND user_id=?",
                (now_iso(), int(row["id"]), user_id),
            )
            archived += 1

    candidates = query("SELECT id,english_level FROM candidates WHERE user_id=?", (user_id,))
    levels = {} if candidates.empty else {
        int(row["id"]): str(row.get("english_level") or "")
        for row in candidates.to_dict("records")
    }
    saved = 0
    refreshed = 0
    for job in jobs:
        try:
            candidate_id = int(job.get("candidate_id") or 0)
        except (TypeError, ValueError):
            candidate_id = 0
        if candidate_id not in levels:
            continue
        title = str(job.get("title") or "")
        text = " ".join(
            str(job.get(key) or "")
            for key in ("title", "description", "tags", "job_type", "work_type", "location", "salary")
        )
        if not role_markers.search(title) or not internship_without_experience(job):
            continue
        posted = live_jobs.parse_datetime(job.get("posted_at"))
        verified = live_jobs.parse_datetime(job.get("verified_at"))
        if (posted and posted < cutoff) or (not posted and (not verified or current - verified > timedelta(hours=48))):
            continue
        if not live_jobs.explicitly_remote_job(job) and not live_jobs.vietnam_local_job(job):
            continue
        link = live_jobs.canonical_url(job.get("url") or job.get("link"))
        if not link or live_jobs.concrete_vacancy_reason(job):
            continue
        salary_ok, pay_note = internship_pay_priority(str(job.get("salary") or ""))
        has_training = bool(training_markers.search(text))
        has_equipment = bool(equipment_markers.search(text))
        language_warning = bool(live_jobs.HIGH_ENGLISH_RE.search(text)) and levels[candidate_id].strip().upper() in {"A1", "A2", "B1"}
        presentation = live_jobs.vacancy_presentation(job)
        score = min(
            94,
            max(55, int(job.get("score") or 0))
            + (10 if has_training else 0)
            + (8 if has_equipment else 0)
            + (5 if salary_ok else 0),
        )
        source = str(job.get("source") or "Открытый источник")
        snapshot = json.dumps({
            "links": [{"url": link, "source": source, "posted_at": str(job.get("posted_at") or "")}],
            "verified_at": now_iso(),
        }, ensure_ascii=False)
        values = (
            title or "Стажировка",
            str(job.get("company") or "Компания не указана"),
            source,
            link,
            str(job.get("location") or ""),
            "Стажировка",
            str(job.get("job_type") or job.get("work_type") or "internship"),
            str(job.get("salary") or pay_note),
            str(job.get("description") or ""),
            json.dumps(presentation["contacts"], ensure_ascii=False),
            score,
            str(job.get("posted_at") or ""),
            now_iso(),
            "Проверьте работодателя, договор, оплату, технику и право работать из Вьетнама.",
            "; ".join(filter(None, (
                "Есть обучение или наставник" if has_training else "",
                "Упомянута техника" if has_equipment else "",
                "Английский выше текущего уровня: уточнить, готовы ли рассмотреть при параллельном обучении" if language_warning else "",
                "Оплату и договор уточнить до отклика" if not salary_ok else pay_note,
            ))),
            snapshot,
        )
        existing = query(
            "SELECT id FROM side_gigs WHERE user_id=? AND candidate_id=? AND link=? AND category='Стажировка' ORDER BY id DESC LIMIT 1",
            (user_id, candidate_id, link),
        )
        if existing.empty:
            execute(
                """INSERT INTO side_gigs(user_id,candidate_id,title,client,source,link,location,category,work_format,pay_text,
                   description,contacts_json,score,status,posted_at,active_checked_at,is_active,safety_note,requirements_note,source_snapshot)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'found',?,?,1,?,?,?)""",
                (user_id, candidate_id, *values),
            )
            saved += 1
        else:
            execute(
                """UPDATE side_gigs SET title=?,client=?,source=?,link=?,location=?,category=?,work_format=?,pay_text=?,
                   description=?,contacts_json=?,score=?,posted_at=?,active_checked_at=?,is_active=1,safety_note=?,requirements_note=?,source_snapshot=?
                   WHERE id=? AND user_id=?""",
                (*values, int(existing.iloc[0]["id"]), user_id),
            )
            refreshed += 1
    return {"saved": saved, "refreshed": refreshed, "archived": archived}


INTERNSHIP_SOURCE_NAMES = (
    "Telegram · Junior Internships",
    "Telegram · Junior Internships · Стажировка",
    "Telegram · Junior Internships · Без опыта",
    "Telegram · Junior Internships · QA",
    "Telegram · Junior Internships · Support",
    "Himalayas · Junior QA",
    "Himalayas · QA intern",
    "Himalayas · Software testing intern",
    "Himalayas · Technical support intern",
    "Himalayas · Software trainee",
    "Himalayas · IT support",
    "Jobicy · Intern",
    "Jobicy · Testing",
    "Remotive · Quality assurance",
    "Remote OK",
)


def stored_jobs_for_internship_scan(
    query, user_id: int, candidate_ids: list[int], *, limit: int = 180,
) -> list[dict[str, Any]]:
    """Restore the original job text from current cards for internship checks."""
    if not candidate_ids:
        return []
    placeholders = ",".join("?" for _ in candidate_ids)
    frame = query(
        f"""
        SELECT v.id,v.candidate_id,v.position,v.company,v.source,v.link,v.remote_location,
               v.salary_text,v.posted_at,v.work_type,v.score,v.source_snapshot,
               i.last_seen,i.source_posted
        FROM vacancies v
        JOIN live_job_index i ON i.vacancy_id=v.id AND i.user_id=v.user_id
          AND i.candidate_id=v.candidate_id
        WHERE v.user_id=? AND v.candidate_id IN ({placeholders}) AND i.active=1
          AND COALESCE(v.status,'found') NOT IN ('archived','skip')
        ORDER BY i.last_seen DESC,v.score DESC
        LIMIT ?
        """,
        tuple([user_id, *candidate_ids, int(limit)]),
    )
    jobs: list[dict[str, Any]] = []
    seen: set[int] = set()
    for row in ([] if frame.empty else frame.to_dict("records")):
        vacancy_id = int(row.get("id") or 0)
        if not vacancy_id or vacancy_id in seen:
            continue
        seen.add(vacancy_id)
        try:
            snapshot = json.loads(str(row.get("source_snapshot") or "{}"))
        except (TypeError, json.JSONDecodeError):
            snapshot = {}
        location = live_jobs.clean_text(row.get("remote_location"), 300)
        jobs.append({
            "candidate_id": int(row.get("candidate_id") or 0),
            "title": live_jobs.clean_text(row.get("position"), 300),
            "company": live_jobs.clean_text(row.get("company"), 300),
            "source": live_jobs.clean_text(row.get("source"), 120),
            "url": live_jobs.canonical_url(row.get("link")),
            "location": location,
            "salary": live_jobs.clean_text(row.get("salary_text"), 300),
            "posted_at": live_jobs.clean_text(row.get("posted_at") or row.get("source_posted"), 100),
            "verified_at": live_jobs.clean_text(snapshot.get("verified_at") or row.get("last_seen"), 100),
            "job_type": live_jobs.clean_text(row.get("work_type"), 100),
            "description": live_jobs.clean_text(snapshot.get("description"), 5000),
            "tags": snapshot.get("tags") or "",
            "remote": bool(re.search(r"remote|удал[её]н|worldwide|anywhere|global", location, re.I)),
            "score": int(row.get("score") or 0),
        })
    return jobs


@app.post("/api/internships/collect")
def collect_internships(user_id: int = Depends(current_user)) -> dict[str, Any]:
    """Refresh explicit no-experience starts without rerunning the full catalogue."""
    title_role_markers = re.compile(
        r"qa|test|тестиров|support|help.?desk|technical support|it support|"
        r"техподдерж|поддержк|java|swift|ios|android|frontend|backend|python|"
        r"разработ|developer|программист|service.?desk|саппорт|cyber|security|"
        r"безопасност|data.?annotat|ai.?evaluat",
        re.I,
    )
    blocked_title_markers = re.compile(
        r"аккаунт|account|продюсер|producer|smm|маркетолог|sales|продаж|копирайт|"
        r"дизайн|дизайнер|менеджер маркет|контент|оператор call|колл[- ]?центр|"
        r"\bozon\b|озон|москва|moscow|санкт[- ]?петербург|spb",
        re.I,
    )
    diagnostics: list[dict[str, Any]] = []
    rejection_counts: dict[str, int] = {}

    def reject(reason: str) -> None:
        rejection_counts[reason] = rejection_counts.get(reason, 0) + 1

    collection_warning = False
    with db.transaction() as (query, execute):
        candidates = query("SELECT id,name,english_level FROM candidates WHERE user_id=? ORDER BY id", (user_id,))
        if candidates.empty:
            raise HTTPException(status_code=400, detail="Сначала создайте профиль кандидата.")
        candidate_ids = [int(value) for value in candidates["id"].tolist()]
        stored_jobs = stored_jobs_for_internship_scan(query, user_id, candidate_ids)
        try:
            online_jobs, diagnostics = live_jobs.collect_live_jobs(
                query, execute, force=True, source_names=INTERNSHIP_SOURCE_NAMES,
                max_wait_seconds=20,
            )
        except Exception:
            # The already verified shared search remains useful even when one
            # public board is temporarily slow.  A dedicated section must not
            # turn that provider outage into a destructive 503 response.
            online_jobs = []
            collection_warning = True

        eligible: list[dict[str, Any]] = []
        current = datetime.now(timezone.utc)
        for candidate in candidates.to_dict("records"):
            candidate_id = int(candidate["id"])
            profile = live_jobs.candidate_profile(query, user_id, candidate_id)
            for job in [*stored_jobs, *online_jobs]:
                stored_candidate_id = int(job.get("candidate_id") or 0)
                if stored_candidate_id and stored_candidate_id != candidate_id:
                    continue
                title = str(job.get("title") or "")
                text = " ".join(
                    str(job.get(key) or "")
                    for key in ("title", "description", "tags", "job_type", "location", "salary", "source")
                )
                if blocked_title_markers.search(title):
                    reject("blocked_title")
                    continue
                if not title_role_markers.search(title):
                    reject("unrelated_title")
                    continue
                if not internship_without_experience(job):
                    reject("experience_not_confirmed")
                    continue
                if not live_jobs.explicitly_remote_job(job) and not live_jobs.vietnam_local_job(job):
                    reject("not_remote_or_vietnam")
                    continue
                block_reason = live_jobs.hard_block(job, profile)
                if block_reason:
                    reject(f"hard_block: {block_reason}")
                    continue
                if live_jobs.concrete_vacancy_reason(job):
                    reject("not_a_concrete_vacancy")
                    continue
                posted = live_jobs.parse_datetime(job.get("posted_at"))
                verified = live_jobs.parse_datetime(job.get("verified_at"))
                if (posted and current - posted > timedelta(days=30)) or (
                    not posted and (not verified or current - verified > timedelta(hours=48))
                ):
                    reject("stale")
                    continue
                item = dict(job)
                item["candidate_id"] = candidate_id
                item["score"] = max(int(item.get("score") or 0), live_jobs.score_job(job, profile)[0])
                eligible.append(item)

        sync = sync_internships_from_jobs(query, execute, user_id, eligible)
        active = query(
            "SELECT COUNT(*) count FROM side_gigs WHERE user_id=? AND category='Стажировка' AND COALESCE(is_active,1)=1 AND COALESCE(status,'found')<>'skip'",
            (user_id,),
        )
        total = 0 if active.empty else int(active.iloc[0]["count"] or 0)

    checked = len(stored_jobs) + sum(int(item.get("count") or 0) for item in diagnostics)
    if total:
        message = (
            f"Стажировки обновлены: новых {sync['saved']}, повторно проверено {sync['refreshed']}, "
            f"в разделе сейчас {total}."
        )
    elif collection_warning:
        message = (
            f"Проверено карточек: {checked}. Сохранённые вакансии проверены, но профильные онлайн-источники отвечают медленно. "
            "Среди проверенных карточек сейчас нет подтверждённых remote IT-стартов без опыта."
        )
    else:
        message = (
            f"Проверено {checked} карточек из {len(diagnostics)} источников: сейчас нет подтверждённых remote IT-стартов без опыта. "
            "Проверяются intern/trainee, явное «без опыта» и junior/entry-level с обучением или наставником."
        )
    sheet_sync = sync_google_sheet_snapshot(user_id)
    return {
        "saved": sync["saved"], "refreshed": sync["refreshed"],
        "archived": sync["archived"], "total": total, "checked": checked,
        "sources_checked": len(diagnostics), "sheet_sync": sheet_sync,
        "rejection_counts": dict(sorted(rejection_counts.items(), key=lambda item: item[1], reverse=True)),
        "message": message,
    }


@app.get("/api/hh/status")
def hh_status(user_id: int = Depends(current_user)) -> dict[str, Any]:
    return {
        "configured": False,
        "connected": False,
        "detail": "HH остаётся проверяемым источником вакансий. Официальный API соискателя для авторизации и откликов недоступен, поэтому логин и пароль HH не запрашиваются и не хранятся.",
    }


@app.post("/api/hh/connect")
def hh_connect(user_id: int = Depends(current_user)) -> dict[str, Any]:
    raise HTTPException(status_code=410, detail="Безопасное подключение HH-соискателя через API недоступно. Откройте оригинал вакансии и отправьте отклик в HH вручную.")
