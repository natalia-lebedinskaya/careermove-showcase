"""Public-release features for CareerMove AI.

The legacy Streamlit application is intentionally kept compatible while this
module owns the public account boundary: password migration, persistent
sessions, optional TOTP, isolated onboarding, appearance preferences and safe
batch draft preparation.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import smtplib
import ssl
import struct
import time
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from typing import Any, Callable
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen


UTC = timezone.utc
PASSWORD_ITERATIONS = 310_000
SESSION_DAYS = 30
SESSION_TOUCH_MINUTES = 10
PASSWORD_RESET_MINUTES = 30
OWNER_EMAIL = os.getenv("CAREERMOVE_OWNER_EMAIL", "owner@example.com").strip().lower()
THEMES = {
    "system-light": "Системная светлая",
    "system-dark": "Системная тёмная",
    "cyber-aurora": "Cyber Aurora",
}
LAYOUT_MODES = {
    "auto": "Авто",
    "compact": "Компактно",
    "comfortable": "Просторно",
}
ALLOWED_APPLICATION_SOURCES = {
    "talanto", "telegram abroad", "habr career", "arbeitnow", "remote ok",
    "we work remotely", "remotive", "jobicy", "manual/social",
}
EMAIL_RE = re.compile(r"^[A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?(?:\.[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?)+$", re.I)


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.replace(tzinfo=parsed.tzinfo or UTC).astimezone(UTC)
    except (TypeError, ValueError):
        return None


def valid_email(value: Any) -> bool:
    email = str(value or "").strip().lower()
    return bool(EMAIL_RE.fullmatch(email)) and len(email) <= 254


def valid_source_url(value: Any) -> bool:
    try:
        parts = urlsplit(str(value or "").strip())
        return parts.scheme in {"http", "https"} and bool(parts.netloc) and "." in parts.netloc
    except ValueError:
        return False


def detect_language(value: Any) -> str:
    text = str(value or "")
    cyrillic = len(re.findall(r"[А-Яа-яЁё]", text))
    latin = len(re.findall(r"[A-Za-z]", text))
    return "RU" if cyrillic >= max(12, round(latin * 0.28)) else "EN"


def password_hash(password: str, *, salt: bytes | None = None, iterations: int = PASSWORD_ITERATIONS) -> str:
    salt = salt or secrets.token_bytes(18)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return "pbkdf2_sha256${}${}${}".format(
        iterations,
        base64.urlsafe_b64encode(salt).decode("ascii").rstrip("="),
        base64.urlsafe_b64encode(digest).decode("ascii").rstrip("="),
    )


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def legacy_password_hash(password: str) -> str:
    return hashlib.sha256(("career64" + password).encode("utf-8")).hexdigest()


def verify_password(password: str, encoded: Any) -> tuple[bool, bool]:
    """Return (valid, needs_upgrade)."""
    stored = str(encoded or "")
    if stored.startswith("pbkdf2_sha256$"):
        try:
            _, iterations, salt, expected = stored.split("$", 3)
            actual = hashlib.pbkdf2_hmac(
                "sha256", password.encode("utf-8"), _b64decode(salt), int(iterations)
            )
            return hmac.compare_digest(actual, _b64decode(expected)), False
        except (ValueError, TypeError):
            return False, False
    valid = hmac.compare_digest(legacy_password_hash(password), stored)
    return valid, valid


def make_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def totp_code(secret: str, *, at_time: int | None = None, step: int = 30) -> str:
    counter = int((at_time if at_time is not None else time.time()) // step)
    key = base64.b32decode(secret.upper() + "=" * (-len(secret) % 8), casefold=True)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = (struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF) % 1_000_000
    return f"{number:06d}"


def verify_totp(secret: str, code: Any, *, at_time: int | None = None) -> bool:
    normalized = re.sub(r"\D", "", str(code or ""))
    if len(normalized) != 6:
        return False
    current = int(at_time if at_time is not None else time.time())
    return any(hmac.compare_digest(totp_code(secret, at_time=current + offset), normalized) for offset in (-30, 0, 30))


def _safe_execute(execute: Callable[..., Any], sql: str, params: tuple[Any, ...] = ()) -> bool:
    try:
        execute(sql, params)
        return True
    except Exception:
        return False


def ensure_schema(execute: Callable[..., Any], *, postgres: bool) -> None:
    user_columns = (
        ("password_version TEXT DEFAULT 'pbkdf2_sha256'", "password_version TEXT DEFAULT 'pbkdf2_sha256'"),
        ("totp_secret TEXT DEFAULT ''", "totp_secret TEXT DEFAULT ''"),
        ("totp_enabled INTEGER DEFAULT 0", "totp_enabled INTEGER DEFAULT 0"),
        ("last_login_at TEXT DEFAULT ''", "last_login_at TEXT DEFAULT ''"),
    )
    for sqlite_col, pg_col in user_columns:
        if postgres:
            _safe_execute(execute, f"ALTER TABLE users ADD COLUMN IF NOT EXISTS {pg_col}")
        else:
            _safe_execute(execute, f"ALTER TABLE users ADD COLUMN {sqlite_col}")

    execute(
        """
        CREATE TABLE IF NOT EXISTS auth_sessions(
          token_hash TEXT PRIMARY KEY,
          user_id INTEGER NOT NULL,
          created_at TEXT NOT NULL,
          expires_at TEXT NOT NULL,
          last_seen_at TEXT NOT NULL,
          revoked INTEGER DEFAULT 0,
          user_agent TEXT DEFAULT ''
        )
        """
    )
    execute(
        """
        CREATE TABLE IF NOT EXISTS password_reset_tokens(
          token_hash TEXT PRIMARY KEY,
          user_id INTEGER NOT NULL,
          created_at TEXT NOT NULL,
          expires_at TEXT NOT NULL,
          used_at TEXT DEFAULT ''
        )
        """
    )
    execute(
        """
        CREATE TABLE IF NOT EXISTS security_events(
          id INTEGER PRIMARY KEY,
          user_id INTEGER,
          event_type TEXT NOT NULL,
          detail TEXT DEFAULT '',
          created_at TEXT NOT NULL
        )
        """ if not postgres else
        """
        CREATE TABLE IF NOT EXISTS security_events(
          id BIGSERIAL PRIMARY KEY,
          user_id BIGINT,
          event_type TEXT NOT NULL,
          detail TEXT DEFAULT '',
          created_at TEXT NOT NULL
        )
        """
    )
    execute(
        """
        CREATE TABLE IF NOT EXISTS application_batches(
          batch_key TEXT PRIMARY KEY,
          user_id INTEGER NOT NULL,
          status TEXT NOT NULL,
          created_at TEXT NOT NULL
        )
        """
    )
    execute(
        """
        CREATE TABLE IF NOT EXISTS application_batch_items(
          item_key TEXT PRIMARY KEY,
          batch_key TEXT NOT NULL,
          user_id INTEGER NOT NULL,
          vacancy_id INTEGER NOT NULL,
          resume_id INTEGER,
          status TEXT NOT NULL,
          error TEXT DEFAULT '',
          created_at TEXT NOT NULL
        )
        """
    )
    execute(
        """
        CREATE TABLE IF NOT EXISTS notification_events(
          event_key TEXT PRIMARY KEY,
          user_id INTEGER NOT NULL,
          channel TEXT NOT NULL,
          status TEXT NOT NULL,
          detail TEXT DEFAULT '',
          created_at TEXT NOT NULL
        )
        """
    )
    execute(
        """
        CREATE TABLE IF NOT EXISTS push_subscriptions(
          id INTEGER PRIMARY KEY,
          user_id INTEGER NOT NULL,
          endpoint TEXT UNIQUE NOT NULL,
          p256dh TEXT NOT NULL,
          auth TEXT NOT NULL,
          enabled INTEGER DEFAULT 1,
          created_at TEXT NOT NULL,
          last_used_at TEXT DEFAULT ''
        )
        """ if not postgres else
        """
        CREATE TABLE IF NOT EXISTS push_subscriptions(
          id BIGSERIAL PRIMARY KEY,
          user_id BIGINT NOT NULL,
          endpoint TEXT UNIQUE NOT NULL,
          p256dh TEXT NOT NULL,
          auth TEXT NOT NULL,
          enabled INTEGER DEFAULT 1,
          created_at TEXT NOT NULL,
          last_used_at TEXT DEFAULT ''
        )
        """
    )
    _safe_execute(execute, "CREATE INDEX IF NOT EXISTS idx_auth_sessions_user ON auth_sessions(user_id)")
    _safe_execute(execute, "CREATE INDEX IF NOT EXISTS idx_batch_items_user ON application_batch_items(user_id,batch_key)")
    _safe_execute(execute, "CREATE INDEX IF NOT EXISTS idx_push_subscriptions_user ON push_subscriptions(user_id,enabled)")


def put_setting(
    query: Callable[..., Any], execute: Callable[..., Any], user_id: int, key: str, value: Any,
) -> None:
    existing = query("SELECT value FROM settings WHERE user_id=? AND key=?", (user_id, key))
    if existing.empty:
        execute("INSERT INTO settings(user_id,key,value) VALUES(?,?,?)", (user_id, key, str(value)))
    else:
        execute("UPDATE settings SET value=? WHERE user_id=? AND key=?", (str(value), user_id, key))


def read_setting(query: Callable[..., Any], user_id: int, key: str, default: str = "") -> str:
    frame = query("SELECT value FROM settings WHERE user_id=? AND key=?", (user_id, key))
    return default if frame.empty else str(frame.iloc[0]["value"] or default)


def seed_public_account(
    query: Callable[..., Any],
    execute: Callable[..., Any],
    user_id: int,
    email: str,
    source_catalog: list[tuple[Any, ...]] | tuple[tuple[Any, ...], ...],
) -> None:
    """Create neutral settings only; never clone an owner's candidates or history."""
    if query("SELECT id FROM user_emails WHERE user_id=? AND lower(email)=lower(?)", (user_id, email)).empty:
        execute(
            "INSERT INTO user_emails(user_id,email,label,enabled) VALUES(?,?,?,?)",
            (user_id, email, "primary", 1),
        )
    defaults = {
        "ui_theme": "system-light",
        "font_scale": "100",
        "layout_density": "auto",
        "application_from_email": email,
        "application_from_name": email.split("@", 1)[0],
        "search_base_country": "Vietnam",
        "search_remote": "1",
        "search_vietnam_hybrid": "1",
        "search_salary_min": "1300",
        "search_max_age_days": "14",
        "search_stop_companies": "Sber; Sberbank; Сбер; Сбербанк",
        "search_stop_countries": "Russia-only; Москва; Санкт-Петербург; Moscow office; Saint Petersburg office",
        "live_jobs_schedule_hours": "6",
        "session_days": str(SESSION_DAYS),
        "auto_send_enabled": "0",
        "onboarding_completed": "0",
    }
    for key, value in defaults.items():
        if query("SELECT value FROM settings WHERE user_id=? AND key=?", (user_id, key)).empty:
            execute("INSERT INTO settings(user_id,key,value) VALUES(?,?,?)", (user_id, key, value))
    existing_sources = query("SELECT id FROM job_sources WHERE user_id=? LIMIT 1", (user_id,))
    if existing_sources.empty:
        for source in source_catalog:
            if len(source) < 5:
                continue
            service, source_type, region, url, notes = source[:5]
            execute(
                "INSERT INTO job_sources(user_id,service,source_type,region,url,enabled,notes) VALUES(?,?,?,?,?,?,?)",
                (user_id, service, source_type, region, url, 1, notes),
            )


def create_account(
    query: Callable[..., Any],
    execute: Callable[..., Any],
    email: Any,
    password: Any,
    source_catalog: list[tuple[Any, ...]] | tuple[tuple[Any, ...], ...],
) -> tuple[bool, str, int | None]:
    clean = str(email or "").strip().lower()
    password_text = str(password or "")
    if not valid_email(clean):
        return False, "Проверьте формат email.", None
    if len(password_text) < 8:
        return False, "Пароль должен содержать не менее 8 символов.", None
    existing = query("SELECT id,password_hash FROM users WHERE lower(email)=lower(?)", (clean,))
    if not existing.empty:
        valid, _ = verify_password(password_text, existing.iloc[0]["password_hash"])
        if valid:
            return True, "Аккаунт уже создан — вход выполнен.", int(existing.iloc[0]["id"])
        return False, "Этот email уже зарегистрирован. Используйте форму входа.", None
    try:
        execute(
            "INSERT INTO users(email,password_hash,password_version) VALUES(?,?,?)",
            (clean, password_hash(password_text), "pbkdf2_sha256"),
        )
        created = query("SELECT id FROM users WHERE lower(email)=lower(?)", (clean,))
        if created.empty:
            return False, "Аккаунт создан не полностью. Повторите вход через несколько секунд.", None
        user_id = int(created.iloc[0]["id"])
        # Seeding is deliberately best-effort. A secondary setup failure must not
        # turn a successfully created account into the old misleading error state.
        try:
            seed_public_account(query, execute, user_id, clean, source_catalog)
        except Exception:
            pass
        return True, "Личный кабинет создан. Вход выполнен.", user_id
    except Exception as error:
        existing = query("SELECT id,password_hash FROM users WHERE lower(email)=lower(?)", (clean,))
        if not existing.empty:
            valid, _ = verify_password(password_text, existing.iloc[0]["password_hash"])
            if valid:
                return True, "Личный кабинет создан. Вход выполнен.", int(existing.iloc[0]["id"])
        return False, f"Не удалось завершить регистрацию: {str(error)[:240]}", None


def authenticate(
    query: Callable[..., Any], execute: Callable[..., Any], email: Any, password: Any,
) -> dict[str, Any]:
    clean = str(email or "").strip().lower()
    frame = query(
        "SELECT id,email,password_hash,totp_secret,totp_enabled FROM users WHERE lower(email)=lower(?)",
        (clean,),
    )
    if frame.empty:
        return {"ok": False, "message": "Неверный email или пароль."}
    row = frame.iloc[0]
    valid, upgrade = verify_password(str(password or ""), row["password_hash"])
    if not valid:
        return {"ok": False, "message": "Неверный email или пароль."}
    user_id = int(row["id"])
    if upgrade:
        execute(
            "UPDATE users SET password_hash=?,password_version=? WHERE id=?",
            (password_hash(str(password)), "pbkdf2_sha256", user_id),
        )
    return {
        "ok": True,
        "user_id": user_id,
        "email": str(row["email"]),
        "requires_totp": bool(row.get("totp_enabled") or 0),
        "totp_secret": str(row.get("totp_secret") or ""),
    }


def issue_session(
    execute: Callable[..., Any], user_id: int, *, user_agent: str = "", days: int = SESSION_DAYS,
) -> str:
    token = secrets.token_urlsafe(36)
    token_digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    created = datetime.now(UTC)
    expires = created + timedelta(days=max(1, min(int(days), 90)))
    execute(
        """
        INSERT INTO auth_sessions(token_hash,user_id,created_at,expires_at,last_seen_at,revoked,user_agent)
        VALUES(?,?,?,?,?,?,?)
        """,
        (
            token_digest, user_id, created.replace(microsecond=0).isoformat(),
            expires.replace(microsecond=0).isoformat(), created.replace(microsecond=0).isoformat(),
            0, str(user_agent or "")[:300],
        ),
    )
    execute("UPDATE users SET last_login_at=? WHERE id=?", (now_iso(), user_id))
    return token


def restore_session(
    query: Callable[..., Any], execute: Callable[..., Any], token: Any,
) -> int | None:
    raw = str(token or "").strip()
    if len(raw) < 32:
        return None
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    frame = query(
        "SELECT user_id,expires_at,last_seen_at,revoked FROM auth_sessions WHERE token_hash=?",
        (digest,),
    )
    if frame.empty:
        return None
    row = frame.iloc[0]
    expires = parse_iso(row["expires_at"])
    if bool(row.get("revoked") or 0) or not expires or expires <= datetime.now(UTC):
        execute("UPDATE auth_sessions SET revoked=1 WHERE token_hash=?", (digest,))
        return None
    last_seen = parse_iso(row.get("last_seen_at"))
    if not last_seen or datetime.now(UTC) - last_seen >= timedelta(minutes=SESSION_TOUCH_MINUTES):
        execute("UPDATE auth_sessions SET last_seen_at=? WHERE token_hash=?", (now_iso(), digest))
    return int(row["user_id"])


def revoke_session(execute: Callable[..., Any], token: Any) -> None:
    raw = str(token or "").strip()
    if raw:
        execute(
            "UPDATE auth_sessions SET revoked=1 WHERE token_hash=?",
            (hashlib.sha256(raw.encode("utf-8")).hexdigest(),),
        )


def issue_password_reset(
    query: Callable[..., Any],
    execute: Callable[..., Any],
    email: Any,
    *,
    minutes: int = PASSWORD_RESET_MINUTES,
) -> tuple[str, str] | None:
    """Create a short-lived reset token without revealing whether an account exists."""
    clean = str(email or "").strip().lower()
    if not valid_email(clean):
        return None
    frame = query("SELECT id,email FROM users WHERE lower(email)=lower(?)", (clean,))
    if frame.empty:
        return None
    user_id = int(frame.iloc[0]["id"])
    created = datetime.now(UTC)
    recent = query(
        """
        SELECT created_at FROM password_reset_tokens
        WHERE user_id=? ORDER BY created_at DESC LIMIT 1
        """,
        (user_id,),
    )
    if not recent.empty:
        last_request = parse_iso(recent.iloc[0]["created_at"])
        if last_request and (created - last_request).total_seconds() < 15:
            return None
    expires = created + timedelta(minutes=max(5, min(int(minutes), 60)))
    token = secrets.token_urlsafe(36)
    token_digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    execute(
        """
        INSERT INTO password_reset_tokens(token_hash,user_id,created_at,expires_at,used_at)
        VALUES(?,?,?,?,?)
        """,
        (
            token_digest,
            user_id,
            created.replace(microsecond=0).isoformat(),
            expires.replace(microsecond=0).isoformat(),
            "",
        ),
    )
    return str(frame.iloc[0]["email"]), token


def reset_password(
    query: Callable[..., Any],
    execute: Callable[..., Any],
    token: Any,
    new_password: Any,
) -> tuple[bool, str]:
    raw = str(token or "").strip()
    password_text = str(new_password or "")
    if len(password_text) < 8:
        return False, "Пароль должен содержать не менее 8 символов."
    if len(raw) < 32:
        return False, "Ссылка недействительна или уже использована."
    token_digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    frame = query(
        """
        SELECT user_id,expires_at,used_at
        FROM password_reset_tokens WHERE token_hash=?
        """,
        (token_digest,),
    )
    if frame.empty:
        return False, "Ссылка недействительна или уже использована."
    row = frame.iloc[0]
    expires = parse_iso(row["expires_at"])
    if str(row.get("used_at") or "").strip() or not expires or expires <= datetime.now(UTC):
        return False, "Ссылка недействительна или уже использована."
    user_id = int(row["user_id"])
    used_at = now_iso()
    execute(
        "UPDATE password_reset_tokens SET used_at=? WHERE user_id=? AND used_at=''",
        (used_at, user_id),
    )
    execute(
        "UPDATE users SET password_hash=?,password_version=? WHERE id=?",
        (password_hash(password_text), "pbkdf2_sha256", user_id),
    )
    execute("UPDATE auth_sessions SET revoked=1 WHERE user_id=?", (user_id,))
    execute(
        "INSERT INTO security_events(user_id,event_type,detail,created_at) VALUES(?,?,?,?)",
        (user_id, "password_reset", "Password changed through one-time email link", used_at),
    )
    return True, "Пароль изменён. Теперь войдите с новым паролем."


def _query_params(st: Any) -> Any:
    return getattr(st, "query_params", None)


def consume_browser_session(st: Any, query: Callable[..., Any], execute: Callable[..., Any]) -> int | None:
    params = _query_params(st)
    token = ""
    if params is not None:
        token = str(params.get("cm_session", "") or "")
    else:
        token = str(st.experimental_get_query_params().get("cm_session", [""])[0] or "")
    if not token:
        return None
    user_id = restore_session(query, execute, token)
    if user_id:
        st.session_state["uid"] = user_id
        st.session_state["_auth_token"] = token
        st.session_state["_auth_persist"] = True
    else:
        st.session_state["_clear_saved_session"] = True
    try:
        params.clear()
    except Exception:
        st.experimental_set_query_params()
    return user_id


def session_bridge(st: Any, components: Any, *, token: str = "", clear: bool = False, restore: bool = True) -> None:
    script = f"""
    <script>
      (() => {{
        const parentWindow = window.parent;
        const storage = parentWindow.localStorage;
        const key = 'careermove.refresh.v12';
        const incoming = {json.dumps(str(token or ""))};
        const clear = {str(bool(clear)).lower()};
        const restore = {str(bool(restore)).lower()};
        if (clear) storage.removeItem(key);
        if (incoming) storage.setItem(key, incoming);
        const current = new URL(parentWindow.location.href);
        if (restore && !incoming && !current.searchParams.get('cm_session')) {{
          const saved = storage.getItem(key);
          if (saved) {{
            current.searchParams.set('cm_session', saved);
            parentWindow.location.replace(current.toString());
          }}
        }}
      }})();
    </script>
    """
    components.html(script, height=0)


def autofill_and_mobile_bridge(components: Any) -> None:
    components.html(
        """
        <script>
        (() => {
          const doc = window.parent.document;
          const configure = () => {
            const inputs = [...doc.querySelectorAll('input')];
            for (const input of inputs) {
              const block = input.closest('[data-testid="stTextInput"]');
              const label = (block?.innerText || '').toLowerCase();
              if (label.includes('email') || label.includes('почт')) {
                input.setAttribute('autocomplete', label.includes('регистрац') ? 'email' : 'username');
                input.setAttribute('inputmode', 'email');
              }
              if (input.type === 'password') {
                input.setAttribute('autocomplete', label.includes('нов') || label.includes('создать')
                  ? 'new-password' : 'current-password');
              }
            }
            if (window.parent.innerWidth <= 1024) {
              const side = doc.querySelector('[data-testid="stSidebar"]');
              const collapseSidebar = () => {
                const container = doc.querySelector('[data-testid="stSidebarCollapseButton"]');
                const collapse = container?.querySelector('button') ||
                  (container?.tagName === 'BUTTON' ? container : null);
                if (collapse) collapse.click();
              };
              if (!doc.body.dataset.cmNavAutoClose) {
                doc.body.dataset.cmNavAutoClose = '1';
                doc.body.addEventListener('change', (event) => {
                  const input = event.target;
                  if (!input || input.tagName !== 'INPUT' || input.type !== 'radio') return;
                  if (!input.closest('[data-testid="stSidebar"] [role="radiogroup"]')) return;
                  collapseSidebar();
                }, true);
              }
              if (side && !side.dataset.cmAutoClose) {
                side.dataset.cmAutoClose = '1';
                side.addEventListener('click', (event) => {
                  if (!event.target.closest('[role="radiogroup"] label')) return;
                  collapseSidebar();
                }, true);
              }
            }
          };
          configure();
          new MutationObserver(configure).observe(doc.body, {subtree:true, childList:true});
        })();
        </script>
        """,
        height=0,
    )


def collapse_sidebar_on_mobile(components: Any) -> None:
    """Collapse the navigation after Streamlit has rendered the selected route."""
    components.html(
        """
        <script>
        (() => {
          if (window.parent.innerWidth > 1024) return;
          window.setTimeout(() => {
            const doc = window.parent.document;
            const container = doc.querySelector('[data-testid="stSidebarCollapseButton"]');
            const collapse = container?.querySelector('button') ||
              (container?.tagName === 'BUTTON' ? container : null);
            if (collapse) collapse.click();
          }, 180);
        })();
        </script>
        """,
        height=0,
    )


def _finish_login(
    st: Any, execute: Callable[..., Any], user_id: int, *, persist: bool = True,
) -> None:
    token = issue_session(execute, user_id, days=SESSION_DAYS if persist else 1)
    st.session_state["uid"] = user_id
    st.session_state["_auth_token"] = token
    st.session_state["_auth_persist"] = bool(persist)
    st.session_state.pop("_pending_totp_uid", None)
    st.session_state.pop("_pending_totp_secret", None)
    st.rerun()


def render_auth(
    st: Any,
    components: Any,
    query: Callable[..., Any],
    execute: Callable[..., Any],
    source_catalog: list[tuple[Any, ...]] | tuple[tuple[Any, ...], ...],
) -> None:
    clear = bool(st.session_state.pop("_clear_saved_session", False))
    session_bridge(st, components, clear=clear, restore=not clear)
    autofill_and_mobile_bridge(components)
    st.markdown(
        """
        <div class="cm-public-auth">
          <div class="cm-auth-mark">CM</div>
          <div>
            <div class="cm-kicker">CAREERMOVE · PUBLIC BETA</div>
            <h1>Поиск работы без шума</h1>
            <p>Строгий подбор по вашему реальному опыту, проверка каждой карточки и только контролируемые отклики.</p>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    pending_uid = st.session_state.get("_pending_totp_uid")
    if pending_uid:
        st.subheader("Подтверждение входа")
        st.caption("Введите шестизначный код из приложения-аутентификатора.")
        with st.form("totp_login_form"):
            code = st.text_input("Код 2FA", max_chars=6, placeholder="000000")
            submitted = st.form_submit_button("Подтвердить и войти", type="primary", use_container_width=True)
        if submitted:
            secret = st.session_state.get("_pending_totp_secret", "")
            if verify_totp(secret, code):
                _finish_login(
                    st, execute, int(pending_uid),
                    persist=bool(st.session_state.get("_pending_remember", True)),
                )
            else:
                st.error("Код неверный или уже истёк. Введите новый код.")
        if st.button("Вернуться к входу"):
            st.session_state.pop("_pending_totp_uid", None)
            st.session_state.pop("_pending_totp_secret", None)
            st.session_state.pop("_pending_remember", None)
            st.rerun()
        return

    login_tab, register_tab = st.tabs(["Войти", "Создать аккаунт"])
    with login_tab:
        with st.form("public_login_form"):
            email = st.text_input(
                "Email", key="public_login_email", placeholder="name@example.com",
            )
            password = st.text_input(
                "Пароль", type="password", key="public_login_password",
                placeholder="Ваш пароль",
            )
            remember = st.checkbox("Оставаться в системе на этом устройстве", value=True)
            login_submitted = st.form_submit_button("Войти", type="primary", use_container_width=True)
        if login_submitted:
            result = authenticate(query, execute, email, password)
            if not result["ok"]:
                st.error(result["message"])
            elif result["requires_totp"]:
                st.session_state["_pending_totp_uid"] = int(result["user_id"])
                st.session_state["_pending_totp_secret"] = result["totp_secret"]
                st.session_state["_pending_remember"] = bool(remember)
                st.rerun()
            else:
                _finish_login(st, execute, int(result["user_id"]), persist=bool(remember))
    with register_tab:
        st.caption("Новый кабинет будет пустым: чужие профили, вакансии и история в него не копируются.")
        with st.form("public_registration_form", clear_on_submit=False):
            reg_email = st.text_input(
                "Email для регистрации", key="public_register_email",
                placeholder="name@example.com",
            )
            reg_password = st.text_input(
                "Новый пароль", type="password", key="public_register_password",
                placeholder="Минимум 8 символов",
            )
            privacy = st.checkbox("Я принимаю Privacy Policy и Terms публичной beta-версии")
            register_submitted = st.form_submit_button(
                "Создать личный кабинет", type="primary", use_container_width=True,
            )
        if register_submitted:
            if not privacy:
                st.error("Чтобы создать аккаунт, подтвердите согласие с Privacy Policy и Terms.")
            else:
                ok, message, user_id = create_account(
                    query, execute, reg_email, reg_password, source_catalog,
                )
                if ok and user_id:
                    st.session_state["just_registered"] = "Аккаунт создан. Заполните три обязательных шага."
                    _finish_login(st, execute, int(user_id), persist=True)
                else:
                    st.error(message)


def logout(st: Any, components: Any, execute: Callable[..., Any]) -> None:
    revoke_session(execute, st.session_state.get("_auth_token", ""))
    st.session_state.clear()
    session_bridge(st, components, clear=True, restore=False)
    st.success("Вы вышли из аккаунта. Сохранённая сессия на этом устройстве удалена.")
    components.html(
        """
        <script>
          window.parent.localStorage.removeItem('careermove.refresh.v12');
          setTimeout(() => window.parent.location.replace(window.parent.location.origin + window.parent.location.pathname), 250);
        </script>
        """,
        height=0,
    )
    st.stop()


def theme_css(theme: str, font_scale: int, layout_density: str = "auto") -> str:
    scale = max(85, min(int(font_scale), 125))
    density = layout_density if layout_density in LAYOUT_MODES else "auto"
    density_variables = {
        "auto": """
          --cm-content-width:1180px;--cm-control-height:46px;--cm-control-radius:11px;
          --cm-card-radius:14px;--cm-page-pad-y:1.15rem;--cm-page-pad-x:1.35rem;
        """,
        "compact": """
          --cm-content-width:1320px;--cm-control-height:44px;--cm-control-radius:9px;
          --cm-card-radius:12px;--cm-page-pad-y:.75rem;--cm-page-pad-x:1rem;
        """,
        "comfortable": """
          --cm-content-width:1040px;--cm-control-height:52px;--cm-control-radius:14px;
          --cm-card-radius:18px;--cm-page-pad-y:1.5rem;--cm-page-pad-x:1.7rem;
        """,
    }[density]
    light = """
      --cm-bg:#f7f9fc;--cm-panel:#ffffff;--cm-panel2:#f2f5fa;--cm-panel3:#e9eef6;
      --cm-text:#101828;--cm-muted:#475467;--cm-line:#d0d5dd;--cm-line-strong:#98a2b3;
      --cm-accent:#155eef;--cm-accent-hover:#004eeb;--cm-accent-soft:#eef4ff;--cm-link:#155eef;
      --cm-accent2:#087e8b;--cm-success:#067647;--cm-warning:#854a0e;--cm-danger:#b42318;
      --cm-disabled-bg:#e4e7ec;--cm-disabled-text:#475467;
      --cm-shadow:0 10px 30px rgba(16,24,40,.07);--cm-shadow-lg:0 20px 55px rgba(16,24,40,.11);
      --cm-alert-info:#eff8ff;--cm-alert-warning:#fffaeb;--cm-alert-success:#ecfdf3;
    """
    dark = """
      --cm-bg:#0c111d;--cm-panel:#151d2c;--cm-panel2:#1c2638;--cm-panel3:#253147;
      --cm-text:#f5f7fa;--cm-muted:#c0c9d6;--cm-line:#35445b;--cm-line-strong:#52647e;
      --cm-accent:#315fd5;--cm-accent-hover:#2851bd;--cm-accent-soft:#1f315b;--cm-link:#91adff;
      --cm-accent2:#41c7bb;--cm-success:#65d9af;--cm-warning:#f4c26b;--cm-danger:#ff9baf;
      --cm-disabled-bg:#27344a;--cm-disabled-text:#b8c2d0;
      --cm-shadow:0 12px 34px rgba(0,0,0,.24);--cm-shadow-lg:0 24px 64px rgba(0,0,0,.34);
      --cm-alert-info:#15283e;--cm-alert-warning:#312817;--cm-alert-success:#123128;
    """
    cyber = """
      --cm-bg:#07101a;--cm-panel:#0d1b29;--cm-panel2:#12263a;--cm-panel3:#18324a;
      --cm-text:#f4fbff;--cm-muted:#bfd0dc;--cm-line:#2c5870;--cm-line-strong:#4b7f96;
      --cm-accent:#7048d8;--cm-accent-hover:#5f3bc0;--cm-accent-soft:#2b214d;--cm-link:#c0aaff;
      --cm-accent2:#23c9b8;--cm-success:#5ee6bd;--cm-warning:#ffc66d;--cm-danger:#ff91ae;
      --cm-disabled-bg:#21364a;--cm-disabled-text:#b6c5d1;
      --cm-shadow:0 12px 38px rgba(0,0,0,.28);--cm-shadow-lg:0 24px 70px rgba(0,0,0,.38);
      --cm-alert-info:#102b3d;--cm-alert-warning:#342a17;--cm-alert-success:#11352d;
    """
    variables = light if theme == "system-light" else dark if theme == "system-dark" else cyber
    cyber_background = (
        "radial-gradient(circle at 8% 4%,rgba(189,92,255,.18),transparent 30%),"
        "radial-gradient(circle at 92% 18%,rgba(18,231,210,.13),transparent 32%),var(--cm-bg)"
        if theme == "cyber-aurora" else "var(--cm-bg)"
    )
    return f"""
    <style id="cmPublicTheme">
      :root{{{variables}{density_variables}--cm-font-scale:{scale / 100:.2f};--cm-body-size:calc(16px * var(--cm-font-scale));}}
      html,body{{font-size:16px!important;}}
      body,.stApp,.stApp button,.stApp input,.stApp textarea,.stApp select{{
        font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Inter,Roboto,Arial,sans-serif!important;
        -webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility;
      }}
      [data-testid="stAppViewContainer"]{{background:{cyber_background}!important;color:var(--cm-text)!important;}}
      [data-testid="stHeader"]{{height:3rem;background:var(--cm-bg)!important;}}
      [data-testid="stDecoration"]{{height:2px!important;background:linear-gradient(90deg,var(--cm-accent),var(--cm-accent2))!important;}}
      [data-testid="stStatusWidget"]{{display:none!important;}}
      [data-testid="element-container"]:has(> iframe[data-testid="stIFrame"][height="0"]),
      [data-testid="element-container"]:has(style#cmPublicTheme){{display:none!important;}}
      .block-container{{max-width:var(--cm-content-width)!important;
        padding:calc(3rem + var(--cm-page-pad-y)) var(--cm-page-pad-x) 5rem!important;}}
      main p,main li,main label,main input,main textarea,main [data-baseweb="select"],
      [data-testid="stSidebar"] p,[data-testid="stSidebar"] label{{
        font-size:var(--cm-body-size)!important;line-height:1.55!important;
      }}
      .stApp h1{{font-size:clamp(2rem,4vw,2.65rem)!important;line-height:1.12!important;letter-spacing:-.035em!important;}}
      .stApp h2{{font-size:clamp(1.55rem,3vw,2rem)!important;line-height:1.2!important;letter-spacing:-.025em!important;}}
      .stApp h3{{font-size:clamp(1.15rem,2vw,1.4rem)!important;line-height:1.3!important;}}
      [data-testid="stSidebar"]{{
        background:var(--cm-panel)!important;border-right:1px solid var(--cm-line)!important;
        box-shadow:8px 0 28px rgba(20,35,58,.08)!important;
      }}
      [data-testid="stSidebar"],[data-testid="stSidebar"] *{{color:var(--cm-text)!important;}}
      [data-testid="stSidebarContent"]{{padding:.75rem .85rem 1.5rem!important;}}
      [data-testid="stSidebar"] .stRadio label{{
        min-height:42px;padding:8px 10px!important;margin:1px 0!important;border-radius:10px!important;
        font-weight:600!important;transition:background .15s ease,color .15s ease!important;
      }}
      [data-testid="stSidebar"] .stRadio label:hover{{background:var(--cm-accent-soft)!important;transform:none!important;}}
      [data-testid="stSidebar"] .stRadio label:has(input:checked){{background:var(--cm-accent-soft)!important;color:var(--cm-accent)!important;}}
      [data-testid="stSidebar"] .stRadio label span{{font-size:clamp(15px,var(--cm-body-size),18px)!important;}}
      .stApp,.stApp p,.stApp li,.stApp label,.stApp h1,.stApp h2,.stApp h3,.stApp h4,.stApp h5,.stApp h6{{
        color:var(--cm-text)!important;
      }}
      .stCaption,.stCaptionContainer,.stApp small,.muted,.small{{color:var(--cm-muted)!important;}}
      a,.stApp a{{color:var(--cm-link)!important;text-underline-offset:3px;}}

      div[data-testid="stForm"],div[data-testid="stExpander"],div[data-testid="stVerticalBlockBorderWrapper"],
      div[data-testid="stStatus"],.card,.job,.candidate,.candidate-focus,.candidate-side,.email-card,.legal-card,
      .mail-ready,.contact-box,.section-panel,.ai-card,.cm-panel,.portfolio-card,.today-stat,.vacancy-top,
      .persona-help,.assistant,.gpt-bridge,.ai-console,.map-card,.quota-card,.counter-note,.clock-box,.editable-hint{{
        background:var(--cm-panel)!important;border:1px solid var(--cm-line)!important;color:var(--cm-text)!important;
        box-shadow:none!important;backdrop-filter:none!important;
      }}
      div[data-testid="stExpander"]{{border-radius:var(--cm-card-radius)!important;overflow:hidden!important;}}
      div[data-testid="stExpander"] details summary{{min-height:var(--cm-control-height)!important;padding:.2rem .25rem!important;}}
      div[data-testid="stExpander"] details summary:hover{{background:var(--cm-panel2)!important;}}
      [data-testid="stTabs"] [role="tablist"]{{gap:4px!important;border-bottom:1px solid var(--cm-line)!important;}}
      [data-testid="stTabs"] button[role="tab"]{{color:var(--cm-muted)!important;font-weight:650!important;}}
      [data-testid="stTabs"] button[role="tab"][aria-selected="true"]{{color:var(--cm-accent)!important;}}
      [data-testid="stTabs"] [data-baseweb="tab-highlight"]{{background:var(--cm-accent)!important;}}

      .stTextInput input,.stNumberInput input,.stTextArea textarea,
      .stSelectbox div[data-baseweb="select"]>div,.stMultiSelect div[data-baseweb="select"]>div{{
        background:var(--cm-panel2)!important;color:var(--cm-text)!important;border:1px solid var(--cm-line-strong)!important;
        border-radius:var(--cm-control-radius)!important;box-shadow:none!important;
      }}
      .stTextInput input::placeholder,.stTextArea textarea::placeholder{{color:var(--cm-muted)!important;opacity:1!important;}}
      .stTextInput input:focus,.stNumberInput input:focus,.stTextArea textarea:focus{{
        border-color:var(--cm-accent)!important;box-shadow:0 0 0 3px var(--cm-accent-soft)!important;
      }}
      .stButton>button,.stFormSubmitButton>button,.stDownloadButton>button,[data-testid="stLinkButton"] a{{
        min-height:var(--cm-control-height);border-radius:var(--cm-control-radius)!important;font-weight:700!important;
        background:var(--cm-panel)!important;color:var(--cm-text)!important;border:1px solid var(--cm-line-strong)!important;
        box-shadow:none!important;transition:background .15s ease,border-color .15s ease,transform .15s ease!important;
      }}
      .stButton>button *,.stFormSubmitButton>button *,.stDownloadButton>button *,[data-testid="stLinkButton"] a *{{
        color:inherit!important;
      }}
      .stButton>button:not(:disabled):hover,.stFormSubmitButton>button:not(:disabled):hover,
      .stDownloadButton>button:not(:disabled):hover,[data-testid="stLinkButton"] a:hover{{
        background:var(--cm-panel2)!important;border-color:var(--cm-accent)!important;transform:translateY(-1px)!important;
        box-shadow:0 6px 18px rgba(35,48,78,.09)!important;
      }}
      .stButton>button[kind="primary"],.stFormSubmitButton>button[kind="primary"],
      button[data-testid="baseButton-primary"]{{
        background:var(--cm-accent)!important;color:#fff!important;border-color:var(--cm-accent)!important;
      }}
      .stButton>button[kind="primary"]:not(:disabled):hover,.stFormSubmitButton>button[kind="primary"]:not(:disabled):hover{{
        background:var(--cm-accent-hover)!important;border-color:var(--cm-accent-hover)!important;
      }}
      .stButton>button:disabled,.stFormSubmitButton>button:disabled,.stDownloadButton>button:disabled,
      button[disabled],[aria-disabled="true"]{{
        opacity:1!important;background:var(--cm-disabled-bg)!important;color:var(--cm-disabled-text)!important;
        border-color:var(--cm-line)!important;box-shadow:none!important;transform:none!important;cursor:not-allowed!important;
      }}
      .stButton>button:disabled *,.stFormSubmitButton>button:disabled *,.stDownloadButton>button:disabled *{{
        color:var(--cm-disabled-text)!important;opacity:1!important;
      }}
      div[data-testid="stAlert"]{{border:1px solid var(--cm-line)!important;border-radius:13px!important;color:var(--cm-text)!important;}}
      div[data-testid="stAlert"] *{{color:var(--cm-text)!important;}}
      div[data-testid="stAlert"][data-baseweb="notification"]{{background:var(--cm-alert-info)!important;}}
      .gpt-error,.mail-warn,.source-note{{background:var(--cm-alert-warning)!important;color:var(--cm-warning)!important;border-color:var(--cm-line)!important;}}
      .gpt-ok,.mail-ready,.notice-success,.salary-advice{{background:var(--cm-alert-success)!important;color:var(--cm-success)!important;border-color:var(--cm-line)!important;}}
      .quota-num,.score{{color:var(--cm-accent)!important;}}
      .cm-sidebar-stats{{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin:12px 0;}}
      .cm-sidebar-stat{{padding:10px 11px;border:1px solid var(--cm-line);border-radius:12px;background:var(--cm-panel2);}}
      .cm-sidebar-stat span{{display:block;color:var(--cm-muted)!important;font-size:12px!important;line-height:1.25;}}
      .cm-sidebar-stat strong{{display:block;color:var(--cm-text)!important;font-size:20px!important;line-height:1.25;margin-top:2px;}}
      .cm-network-note{{margin:10px 0;padding:9px 11px;border-radius:11px;background:var(--cm-panel2);
        border:1px solid var(--cm-line);color:var(--cm-muted)!important;font-size:12px!important;line-height:1.4!important;}}
      .cm-appearance-kicker{{font-size:12px!important;letter-spacing:.08em;text-transform:uppercase;
        color:var(--cm-accent2)!important;font-weight:850;margin-bottom:2px;}}
      .cm-appearance-help{{font-size:13px!important;color:var(--cm-muted)!important;margin:-3px 0 8px;}}

      .cm-cyber-head{{background:var(--cm-panel)!important;border:1px solid var(--cm-line)!important;
        box-shadow:var(--cm-shadow)!important;color:var(--cm-text)!important;border-radius:20px!important;}}
      .cm-cyber-head:before{{display:none!important;}}
      .cm-cyber-head h1,.cm-cyber-head p{{color:var(--cm-text)!important;}}
      .cm-cyber-head p{{color:var(--cm-muted)!important;}}
      .cm-eyebrow{{color:var(--cm-accent2)!important;}}
      .cm-signal-grid{{background:var(--cm-panel2)!important;border-color:var(--cm-line)!important;}}
      .cm-signal-grid i{{background:var(--cm-accent)!important;box-shadow:none!important;}}
      .cm-flow-step{{background:var(--cm-panel)!important;border-color:var(--cm-line)!important;box-shadow:none!important;}}
      .cm-flow-step b{{color:var(--cm-accent)!important;}}.cm-flow-step strong{{color:var(--cm-text)!important;}}
      .cm-flow-step span{{color:var(--cm-muted)!important;}}.cm-flow-step:not(:last-child):after{{color:var(--cm-line-strong)!important;text-shadow:none!important;}}

      .cm-public-auth{{display:grid;grid-template-columns:84px 1fr;gap:22px;align-items:center;padding:28px;
        border:1px solid var(--cm-line);border-radius:28px;background:var(--cm-panel);box-shadow:var(--cm-shadow);margin:8px 0 22px;}}
      .cm-public-auth h1{{margin:3px 0 5px;font-size:clamp(1.75rem,4vw,3rem);letter-spacing:-.04em;}}
      .cm-public-auth p{{margin:0;color:var(--cm-muted);max-width:720px;}}
      .cm-auth-mark{{width:76px;height:76px;border-radius:24px;display:grid;place-items:center;font-weight:950;font-size:1.35rem;
        color:#fff;background:linear-gradient(140deg,var(--cm-accent),var(--cm-accent2));box-shadow:0 14px 32px color-mix(in srgb,var(--cm-accent) 36%,transparent);}}
      .cm-kicker{{font-size:.72rem;letter-spacing:.15em;font-weight:900;color:var(--cm-accent2);}}
      .cm-setup-row{{display:flex;justify-content:space-between;gap:12px;padding:8px 0;border-bottom:1px solid var(--cm-line);font-size:.86rem;}}
      .cm-required{{color:var(--cm-accent2);font-weight:800;}}
      .cm-optional{{color:var(--cm-muted);}}
      .cm-font-preview{{font-size:calc(1rem * var(--preview-scale,1));padding:18px;border:1px dashed var(--cm-line);
        background:var(--cm-panel2);color:var(--cm-text)!important;border-radius:16px;margin:8px 0 14px;line-height:1.55;}}
      .cm-font-preview.cm-font-scale-85{{font-size:.85rem!important;}}
      .cm-font-preview.cm-font-scale-90{{font-size:.90rem!important;}}
      .cm-font-preview.cm-font-scale-95{{font-size:.95rem!important;}}
      .cm-font-preview.cm-font-scale-100{{font-size:1rem!important;}}
      .cm-font-preview.cm-font-scale-105{{font-size:1.05rem!important;}}
      .cm-font-preview.cm-font-scale-110{{font-size:1.10rem!important;}}
      .cm-font-preview.cm-font-scale-115{{font-size:1.15rem!important;}}
      .cm-font-preview.cm-font-scale-120{{font-size:1.20rem!important;}}
      .cm-font-preview.cm-font-scale-125{{font-size:1.25rem!important;}}
      .cm-status-dot{{width:9px;height:9px;border-radius:50%;display:inline-block;margin-right:7px;background:var(--cm-accent2);
        animation:cmStatusPulse 1.8s ease-in-out infinite;}}
      @keyframes cmStatusPulse{{50%{{transform:scale(1.55);box-shadow:0 0 18px var(--cm-accent2);}}}}
      @media(max-width:760px){{
        :root{{--cm-body-size:clamp(15px,calc(16px * var(--cm-font-scale)),18px);}}
        [data-testid="stHeader"]{{height:2.75rem!important;}}
        [data-testid="stSidebar"]{{width:min(88vw,340px)!important;max-width:340px!important;box-shadow:14px 0 34px rgba(20,35,58,.16)!important;}}
        [data-testid="stSidebarContent"],[data-testid="stSidebarUserContent"]{{width:100%!important;}}
        [data-testid="stSidebarContent"]{{padding:.45rem .65rem 1.1rem!important;}}
        [data-testid="stSidebar"] .stRadio label{{min-height:39px;padding:6px 8px!important;}}
        .block-container{{padding:calc(2.75rem + .75rem) min(var(--cm-page-pad-x),.85rem) 4rem!important;}}
        .stApp h1{{font-size:1.8rem!important;}}.stApp h2{{font-size:1.4rem!important;}}.stApp h3{{font-size:1.12rem!important;}}
        .cm-public-auth{{grid-template-columns:48px 1fr;padding:15px;gap:12px;border-radius:16px;}}
        .cm-auth-mark{{width:52px;height:52px;border-radius:16px;font-size:1rem;}}
        .stButton>button,.stFormSubmitButton>button{{min-height:max(46px,var(--cm-control-height));width:100%;}}
        [data-testid="column"]{{min-width:100%!important;}}
        .cm-cyber-head{{padding:18px!important;border-radius:16px!important;gap:10px!important;margin-bottom:10px!important;}}
        .cm-cyber-head h1{{font-size:1.65rem!important;margin:.35rem 0!important;}}
        .cm-cyber-head p{{font-size:15px!important;line-height:1.5!important;}}
        .cm-signal-grid{{display:none!important;}}
        .cm-flow{{display:flex!important;overflow-x:auto!important;scroll-snap-type:x mandatory;gap:8px!important;
          margin:8px -.75rem 12px!important;padding:0 .75rem 8px!important;}}
        .cm-flow-step{{flex:0 0 168px;min-height:94px!important;padding:11px!important;border-radius:13px!important;scroll-snap-align:start;}}
        .cm-flow-step:not(:last-child):after{{display:none!important;}}
        div[data-testid="stExpander"]{{border-radius:min(var(--cm-card-radius),16px)!important;}}
        [data-testid="stTabs"] button[role="tab"]{{padding:.55rem .5rem!important;font-size:14px!important;}}
        .cm-sidebar-stats{{margin:9px 0;}}
      }}
      @media(prefers-reduced-motion:reduce){{
        *{{scroll-behavior:auto!important;}}.cm-status-dot,.cm-signal-grid i{{animation:none!important;}}
      }}
    </style>
    """


def inject_theme(st: Any, query: Callable[..., Any], user_id: int) -> None:
    theme = read_setting(query, user_id, "ui_theme", "system-light")
    if theme not in THEMES:
        theme = "system-light"
    layout_density = read_setting(query, user_id, "layout_density", "auto")
    if layout_density not in LAYOUT_MODES:
        layout_density = "auto"
    try:
        scale = int(read_setting(query, user_id, "font_scale", "100"))
    except ValueError:
        scale = 100
    st.markdown(theme_css(theme, scale, layout_density), unsafe_allow_html=True)


def _appearance_values(query: Callable[..., Any], user_id: int) -> tuple[str, int, str]:
    theme = read_setting(query, user_id, "ui_theme", "system-light")
    if theme not in THEMES:
        theme = "system-light"
    try:
        scale = max(85, min(int(read_setting(query, user_id, "font_scale", "100")), 125))
    except ValueError:
        scale = 100
    scale = max(85, min(5 * round(scale / 5), 125))
    density = read_setting(query, user_id, "layout_density", "auto")
    if density not in LAYOUT_MODES:
        density = "auto"
    return theme, scale, density


def render_quick_appearance_bar(
    st: Any,
    query: Callable[..., Any],
    execute: Callable[..., Any],
) -> None:
    """Compact, persistent appearance controls for the two entry screens."""
    user_id = int(st.session_state["uid"])
    current_theme, current_scale, current_density = _appearance_values(query, user_id)
    summary = (
        f"Оформление · {THEMES[current_theme]} · {current_scale}% · "
        f"{LAYOUT_MODES[current_density]}"
    )
    with st.expander(summary, expanded=False):
        st.markdown(
            "<div class='cm-appearance-kicker'>Быстрая настройка интерфейса</div>"
            "<div class='cm-appearance-help'>Изменения применятся ко всему кабинету и сохранятся для этого аккаунта.</div>",
            unsafe_allow_html=True,
        )
        theme_keys = list(THEMES)
        density_keys = list(LAYOUT_MODES)
        col_theme, col_scale, col_density = st.columns([1.25, 1, 1])
        with col_theme:
            theme = st.selectbox(
                "Тема",
                theme_keys,
                index=theme_keys.index(current_theme),
                format_func=lambda key: THEMES[key],
                key="quick_ui_theme",
            )
        with col_scale:
            scale = st.select_slider(
                "Размер текста",
                options=list(range(85, 126, 5)),
                value=current_scale,
                format_func=lambda value: f"{value}%",
                key="quick_font_scale",
            )
        with col_density:
            density = st.selectbox(
                "Компоновка",
                density_keys,
                index=density_keys.index(current_density),
                format_func=lambda key: LAYOUT_MODES[key],
                key="quick_layout_density",
                help="Авто подходит большинству экранов; компактно показывает больше данных; просторно увеличивает поля и отступы.",
            )
        if st.button(
            "Применить оформление",
            type="primary",
            use_container_width=True,
            key="quick_apply_appearance",
        ):
            put_setting(query, execute, user_id, "ui_theme", theme)
            put_setting(query, execute, user_id, "font_scale", scale)
            put_setting(query, execute, user_id, "layout_density", density)
            st.rerun()


def setup_state(query: Callable[..., Any], user_id: int) -> dict[str, bool]:
    candidates = query("SELECT id FROM candidates WHERE user_id=? LIMIT 1", (user_id,))
    resumes = query(
        "SELECT id FROM resumes WHERE user_id=? AND length(trim(COALESCE(content,'')))>80 LIMIT 1",
        (user_id,),
    )
    search = (
        bool(read_setting(query, user_id, "search_base_country", ""))
        and read_setting(query, user_id, "onboarding_completed", "0") == "1"
    )
    return {
        "profile": not candidates.empty,
        "resume": not resumes.empty,
        "search": bool(search),
        "optional_2fa": bool(
            int(query("SELECT COALESCE(totp_enabled,0) enabled FROM users WHERE id=?", (user_id,)).iloc[0]["enabled"] or 0)
        ),
    }


def backfill_existing_account(
    query: Callable[..., Any], execute: Callable[..., Any], user_id: int,
) -> None:
    """Mark already configured pre-v12 accounts without replaying onboarding."""
    # Previous beta screens could store service credentials in the settings
    # table. Public v12 accepts these values only from deployment secrets.
    execute(
        """
        DELETE FROM settings
        WHERE user_id=? AND key IN ('smtp_password','openai_api_key','gmail_app_password','telegram_bot_token')
        """,
        (user_id,),
    )
    if not query("SELECT value FROM settings WHERE user_id=? AND key='onboarding_completed'", (user_id,)).empty:
        return
    has_profile = not query("SELECT id FROM candidates WHERE user_id=? LIMIT 1", (user_id,)).empty
    has_resume = not query(
        "SELECT id FROM resumes WHERE user_id=? AND length(trim(COALESCE(content,'')))>80 LIMIT 1",
        (user_id,),
    ).empty
    put_setting(
        query, execute, user_id, "onboarding_completed",
        "1" if has_profile and has_resume else "0",
    )


def render_setup_sidebar(st: Any, query: Callable[..., Any], user_id: int) -> None:
    state = setup_state(query, user_id)
    required_done = sum(int(state[key]) for key in ("profile", "resume", "search"))
    with st.expander(f"Настройка · {required_done}/3", expanded=False):
        labels = (
            ("profile", "Профиль кандидата", True),
            ("resume", "Резюме", True),
            ("search", "Фильтры поиска", True),
            ("optional_2fa", "Двухфакторная защита", False),
        )
        for key, label, required in labels:
            icon = "✓" if state[key] else "○"
            kind = "обязательно" if required else "необязательно"
            css = "cm-required" if required else "cm-optional"
            st.markdown(
                f"<div class='cm-setup-row'><span>{icon} {label}</span><span class='{css}'>{kind}</span></div>",
                unsafe_allow_html=True,
            )


def render_onboarding(
    st: Any,
    query: Callable[..., Any],
    execute: Callable[..., Any],
) -> None:
    user_id = int(st.session_state["uid"])
    state = setup_state(query, user_id)
    done = sum(int(state[key]) for key in ("profile", "resume", "search"))
    st.title("Быстрый старт")
    st.caption("Три обязательных шага. Сертификаты, портфолио, соцсети и 2FA можно добавить позже.")
    st.progress(done / 3, text=f"Обязательная настройка: {done} из 3")
    if st.session_state.pop("just_registered", None):
        st.success("Аккаунт создан и вход выполнен. Чужие данные в этот кабинет не копировались.")

    with st.expander("1 · Профиль кандидата · обязательно", expanded=not state["profile"]):
        if state["profile"]:
            st.success("Профиль создан.")
        else:
            with st.form("onboarding_profile"):
                name = st.text_input("Имя и фамилия", placeholder="Как указать в резюме")
                role = st.text_input("Целевая роль", "QA Engineer", placeholder="Например, Middle QA Engineer")
                english = st.selectbox("Английский", ["A2", "B1", "B2", "C1"])
                salary = st.number_input("Минимальная зарплата, USD/месяц", 500, 20000, 1300, 100)
                geography = st.text_input(
                    "География", "Vietnam; Da Nang; Remote/Worldwide",
                    help="Работа из Вьетнама, Дананг, remote international или гибрид/офис во Вьетнаме.",
                )
                submitted = st.form_submit_button("Сохранить профиль", type="primary")
            if submitted and name.strip():
                execute(
                    """
                    INSERT INTO candidates(
                      user_id,name,emoji,target_title,age,citizenship,native_languages,
                      english_level,desired_countries,salary_min,notes,private_hints,hard_exclude,hard_require
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        user_id, name.strip(), "👤", role.strip(), 0, "", "", english,
                        geography.strip(), int(salary), "Public onboarding",
                        "Работа из Вьетнама; Дананг; remote international; готовность к интервью сейчас.",
                        "Sber; Sberbank; Сбер; Сбербанк; Moscow office; Saint Petersburg office",
                        "remote; Vietnam hybrid/office; official source",
                    ),
                )
                st.rerun()
    with st.expander("2 · Резюме · обязательно", expanded=state["profile"] and not state["resume"]):
        if not state["profile"]:
            st.info("Сначала создайте профиль.")
        elif state["resume"]:
            st.success("Хотя бы одно резюме заполнено.")
        else:
            profiles = query("SELECT id,name FROM candidates WHERE user_id=? ORDER BY id", (user_id,))
            profile_ids = [int(value) for value in profiles["id"].tolist()]
            with st.form("onboarding_resume"):
                candidate_id = st.selectbox(
                    "Кандидат", profile_ids,
                    format_func=lambda value: str(profiles.loc[profiles.id.eq(value), "name"].iloc[0]),
                )
                language = st.radio("Язык резюме", ["RU", "EN"], horizontal=True)
                content = st.text_area(
                    "Вставьте текст резюме", height=260,
                    placeholder="Опыт, навыки, проекты и контакты. Текст можно отредактировать позже.",
                )
                resume_submitted = st.form_submit_button("Сохранить резюме", type="primary")
            if resume_submitted and len(content.strip()) >= 80:
                title = f"{profiles.loc[profiles.id.eq(candidate_id), 'name'].iloc[0]} CV {language}"
                execute(
                    "INSERT INTO resumes(user_id,candidate_id,language,title,content) VALUES(?,?,?,?,?)",
                    (user_id, candidate_id, language, title, content.strip()),
                )
                st.rerun()
            elif resume_submitted:
                st.error("Добавьте минимум несколько строк об опыте и навыках.")
    with st.expander("3 · Настройки поиска · обязательно", expanded=state["profile"] and state["resume"]):
        with st.form("onboarding_search"):
            base = st.selectbox("Откуда вы работаете", ["Vietnam", "Worldwide", "Kazakhstan", "Other"])
            remote = st.checkbox("Remote international", value=True)
            hybrid = st.checkbox("Разрешить гибрид/офис только во Вьетнаме", value=True)
            max_age = st.slider("Не показывать вакансии старше, дней", 3, 30, 14)
            stop_companies = st.text_input("Стоп-компании", "Sber; Sberbank; Сбер; Сбербанк")
            search_submitted = st.form_submit_button("Сохранить фильтры", type="primary")
        if search_submitted:
            for key, value in {
                "search_base_country": base,
                "search_remote": int(remote),
                "search_vietnam_hybrid": int(hybrid),
                "search_serbia_hybrid": 0,
                "search_max_age_days": max_age,
                "search_stop_companies": stop_companies,
                "onboarding_completed": 1,
            }.items():
                put_setting(query, execute, user_id, key, value)
            st.success("Основная настройка завершена.")
            st.session_state["_requested_nav"] = "Today"
            st.rerun()

    if all(state[key] for key in ("profile", "resume", "search")):
        st.success("Всё обязательное готово. Можно запускать первую подборку.")
        if st.button("Перейти к подборке", type="primary"):
            st.session_state["_requested_nav"] = "Today"
            st.rerun()
    st.info("Необязательно сейчас: сертификаты, портфолио, дополнительные соцсети и 2FA. Они не влияют на первый поиск.")


def migrate_owner_to_vietnam(
    query: Callable[..., Any], execute: Callable[..., Any], user_id: int,
) -> None:
    owner = query("SELECT email FROM users WHERE id=?", (user_id,))
    if owner.empty or str(owner.iloc[0]["email"] or "").strip().lower() != OWNER_EMAIL:
        return
    if read_setting(query, user_id, "vietnam_release_migration_v13", "") == "1":
        return
    profiles = query(
        "SELECT id,name,desired_countries,salary_min,hard_exclude,private_hints FROM candidates WHERE user_id=?",
        (user_id,),
    )
    for _, row in profiles.iterrows():
        name = str(row["name"] or "")
        countries = str(row.get("desired_countries") or "")
        for old in ("Serbia", "Belgrade", "Novi Sad", "Сербия", "Белград", "Нови-Сад"):
            countries = countries.replace(old, "Vietnam")
        if "Vietnam" not in countries and "Вьетнам" not in countries:
            countries = "Vietnam, Da Nang, Remote/Worldwide, Southeast Asia"
        minimum = max(int(row.get("salary_min") or 0), 1300 if "natal" in name.lower() else 1200)
        blocked = str(row.get("hard_exclude") or "")
        additions = ["Sber", "Sberbank", "Сбер", "Сбербанк", "Moscow office", "Saint Petersburg office"]
        for item in additions:
            if item.lower() not in blocked.lower():
                blocked = (blocked.rstrip("; ") + "; " + item).strip("; ")
        hints = str(row.get("private_hints") or "")
        vietnam_hint = (
            " Работать из Вьетнама; Дананг; hybrid/office только во Вьетнаме; "
            "готовность к интервью сейчас; remote international; уточнять work permit, "
            "визу, технику, график и формат оформления до отклика."
        )
        if "Работать из Вьетнама" not in hints:
            hints += vietnam_hint
        execute(
            """
            UPDATE candidates SET desired_countries=?,salary_min=?,hard_exclude=?,private_hints=?
            WHERE user_id=? AND id=?
            """,
            (countries, minimum, blocked, hints.strip(), user_id, int(row["id"])),
        )
    resumes = query("SELECT id,content FROM resumes WHERE user_id=?", (user_id,))
    replacements = {
        "Serbia / remote international; ready to relocate to Belgrade": "Vietnam / remote international; ready to work from Da Nang",
        "Serbia-friendly": "Vietnam-friendly",
        "релокация в Белград / работа из Сербии": "релокация в Дананг / работа из Вьетнама",
        "Сербия": "Вьетнам",
        "Serbia": "Vietnam",
        "Белград": "Дананг",
        "Belgrade": "Da Nang",
    }
    for _, row in resumes.iterrows():
        content = str(row.get("content") or "")
        updated = content
        for old, new in replacements.items():
            updated = updated.replace(old, new)
        if updated != content:
            execute("UPDATE resumes SET content=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (updated, int(row["id"])))
    _safe_execute(
        execute,
        "UPDATE daily_tasks SET title=? WHERE user_id=? AND (title LIKE ? OR title LIKE ?)",
        ("Вьетнам: документы / бюджет / легализация", user_id, "%Серб%", "%Serbia%"),
    )
    for key, value in {
        "search_base_country": "Vietnam",
        "search_remote": "1",
        "search_vietnam_hybrid": "1",
        "search_serbia_hybrid": "0",
        "search_salary_min": "1300",
        "search_max_age_days": "30",
        "search_stop_companies": "Sber; Sberbank; Сбер; Сбербанк",
        "live_jobs_schedule_hours": "6",
        "onboarding_completed": "1",
        "vietnam_release_migration_v13": "1",
    }.items():
        put_setting(query, execute, user_id, key, value)


def unified_cover_letter(job: dict[str, Any], candidate: str, matched: str = "") -> str:
    language = detect_language(" ".join(str(job.get(key) or "") for key in ("position", "company", "source_snapshot")))
    company = str(job.get("company") or "Hiring Team")
    position = str(job.get("position") or "QA Engineer")
    skills = matched.strip() or "API, backend, mobile and regression testing"
    if language == "RU":
        return (
            f"Здравствуйте, команда {company}!\n\n"
            f"Хочу откликнуться на позицию {position}. Мой текущий опыт включает {skills}, "
            "анализ требований, тестовую документацию, расследование дефектов и релизные проверки.\n\n"
            "Я готова к интервью уже сейчас, рассматриваю remote international, а также hybrid/office во Вьетнаме. "
            "Перед откликом готова отдельно подтвердить формат оформления, work permit, визу, технику и график.\n\n"
            f"С уважением,\n{candidate}"
        )
    return (
        f"Dear {company} Team,\n\n"
        f"I am applying for the {position} position. My current QA experience includes {skills}, "
        "requirements analysis, test documentation, defect investigation and release validation.\n\n"
        "I am available to interview now and open to remote international work or Vietnam-based hybrid/office roles. "
        "Before applying, I can confirm the employment format, work permit, visa, equipment and schedule details.\n\n"
        f"Best regards,\n{candidate}"
    )


def choose_resume(resumes: Any, vacancy_text: str) -> Any | None:
    if resumes is None or resumes.empty:
        return None
    language = detect_language(vacancy_text)
    exact = resumes[resumes["language"].astype(str).str.upper().eq(language)]
    return (exact if not exact.empty else resumes).iloc[0]


def _source_allowed(source: Any, link: Any) -> bool:
    normalized = str(source or "").strip().lower()
    if normalized in ALLOWED_APPLICATION_SOURCES:
        return True
    if normalized == "openai_gpt_bridge":
        return valid_source_url(link)
    return valid_source_url(link) and normalized not in {"", "unknown", "demo"}


def render_batch_center(
    st: Any,
    query: Callable[..., Any],
    execute: Callable[..., Any],
    resume_pdf: Callable[[str], bytes],
    build_eml: Callable[..., bytes],
    safe_filename: Callable[..., str],
    from_email: str,
    from_name: str,
) -> None:
    user_id = int(st.session_state["uid"])
    st.subheader("Пакет из 10 золотых вакансий")
    st.caption(
        "Одна кнопка готовит выбранные черновики с правильным языком резюме. "
        "Ничего не отправляется без вашей финальной проверки."
    )
    vacancies = query(
        """
        SELECT v.*,c.name candidate
        FROM vacancies v
        JOIN candidates c ON c.id=v.candidate_id AND c.user_id=v.user_id
        WHERE v.user_id=? AND v.score>=80 AND v.status IN ('found','approved','ready')
        ORDER BY v.score DESC,v.fetched_at DESC
        LIMIT 10
        """,
        (user_id,),
    )
    if vacancies.empty:
        st.info("Пакет появится после того, как строгий фильтр найдёт вакансии с рейтингом 80%+.")
        return
    st.markdown(
        "<span class='cm-status-dot'></span><b>Безопасный режим:</b> автоотправка выключена; "
        "для 95%+ тоже создаётся только проверяемый черновик.",
        unsafe_allow_html=True,
    )
    selected_ids: list[int] = []
    for _, row in vacancies.iterrows():
        vacancy_id = int(row["id"])
        cols = st.columns([.45, 4.4, 1.15])
        checked = cols[0].checkbox("Выбрать", value=int(row.get("score") or 0) >= 90, key=f"batch_pick_{vacancy_id}", label_visibility="collapsed")
        if checked:
            selected_ids.append(vacancy_id)
        source_ok = _source_allowed(row.get("source"), row.get("link"))
        email_ok = valid_email(row.get("employer_email"))
        link_ok = valid_source_url(row.get("link"))
        contact = "email подтверждён" if email_ok else "форма/Telegram" if link_ok else "контакт не подтверждён"
        cols[1].markdown(
            f"**{int(row.get('score') or 0)}% · {row.get('company')} — {row.get('position')}**  \n"
            f"{row.get('candidate')} · {row.get('source')} · {contact}"
        )
        cols[2].markdown("✅ источник" if source_ok and link_ok else "⚠️ проверить")
    prepare = st.button(
        f"Подготовить выбранные ({len(selected_ids)})", type="primary",
        disabled=not selected_ids, use_container_width=True,
    )
    if prepare:
        batch_key = uuid.uuid4().hex
        execute(
            "INSERT INTO application_batches(batch_key,user_id,status,created_at) VALUES(?,?,?,?)",
            (batch_key, user_id, "preparing", now_iso()),
        )
        archive = io.BytesIO()
        statuses: list[dict[str, Any]] = []
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            for vacancy_id in selected_ids:
                row = vacancies[vacancies.id.eq(vacancy_id)].iloc[0]
                duplicate = query(
                    "SELECT id FROM applications WHERE user_id=? AND vacancy_id=? LIMIT 1",
                    (user_id, vacancy_id),
                )
                status, error, resume_id = "ready", "", None
                if not duplicate.empty:
                    status, error = "duplicate", "Отклик уже есть в истории."
                elif not _source_allowed(row.get("source"), row.get("link")):
                    status, error = "error", "Источник не прошёл проверку."
                elif not valid_source_url(row.get("link")):
                    status, error = "error", "Нет валидной ссылки на оригинал вакансии."
                resumes = query(
                    "SELECT id,title,language,content FROM resumes WHERE user_id=? AND candidate_id=? ORDER BY id",
                    (user_id, int(row["candidate_id"])),
                )
                vacancy_text = " ".join(str(row.get(key) or "") for key in ("position", "company", "source_snapshot", "strengths"))
                resume = choose_resume(resumes, vacancy_text)
                if resume is None:
                    status, error = "error", "Для кандидата нет резюме."
                else:
                    resume_id = int(resume["id"])
                if status == "ready" and resume is not None:
                    body = str(row.get("cover_letter") or "").strip() or unified_cover_letter(
                        row.to_dict(), str(row.get("candidate") or "Candidate"), str(row.get("strengths") or ""),
                    )
                    pdf = resume_pdf(str(resume.get("content") or ""))
                    resume_name = safe_filename(
                        f"{row.get('candidate')}_{resume.get('language')}_CV.pdf",
                        "candidate_resume.pdf",
                    )
                    if valid_email(row.get("employer_email")):
                        subject = f"Application for {row.get('position')} — {row.get('candidate')}"
                        eml = build_eml(
                            to=str(row.get("employer_email")), subject=subject, body=body,
                            resume_pdf=pdf, resume_filename=resume_name,
                            from_email=from_email, from_name=from_name,
                        )
                        bundle.writestr(
                            safe_filename(f"{row.get('company')}_{row.get('position')}.eml", f"{vacancy_id}.eml"),
                            eml,
                        )
                    else:
                        instruction = (
                            f"{row.get('company')} — {row.get('position')}\n"
                            f"Source: {row.get('link')}\nContact: {row.get('employer_contact') or 'application form'}\n\n"
                            f"{body}\n"
                        )
                        bundle.writestr(
                            safe_filename(f"{row.get('company')}_{row.get('position')}.txt", f"{vacancy_id}.txt"),
                            instruction.encode("utf-8"),
                        )
                    bundle.writestr(resume_name, pdf)
                    execute("UPDATE vacancies SET status='ready',cover_letter=? WHERE user_id=? AND id=?", (body, user_id, vacancy_id))
                execute(
                    """
                    INSERT INTO application_batch_items(
                      item_key,batch_key,user_id,vacancy_id,resume_id,status,error,created_at
                    ) VALUES(?,?,?,?,?,?,?,?)
                    """,
                    (uuid.uuid4().hex, batch_key, user_id, vacancy_id, resume_id, status, error, now_iso()),
                )
                statuses.append({
                    "Вакансия": f"{row.get('company')} — {row.get('position')}",
                    "Кандидат": row.get("candidate"),
                    "Резюме": "" if resume is None else f"{resume.get('language')} · {resume.get('title')}",
                    "Статус": status,
                    "Пояснение": error or "Черновик готов к личной проверке",
                })
        execute("UPDATE application_batches SET status=? WHERE batch_key=? AND user_id=?", ("ready", batch_key, user_id))
        archive.seek(0)
        st.session_state["_batch_archive"] = archive.getvalue()
        st.session_state["_batch_key"] = batch_key
        st.session_state["_batch_statuses"] = statuses

    statuses = st.session_state.get("_batch_statuses", [])
    if statuses:
        st.dataframe(statuses, use_container_width=True, hide_index=True)
        st.download_button(
            "Скачать пакет черновиков и резюме",
            st.session_state["_batch_archive"],
            file_name="careermove_gold_drafts.zip",
            mime="application/zip",
            use_container_width=True,
        )
        confirmed = st.checkbox("Я лично отправила все готовые отклики из этого пакета")
        if st.button("Отметить готовые как отправленные", disabled=not confirmed, use_container_width=True):
            batch_key = st.session_state.get("_batch_key")
            items = query(
                """
                SELECT i.*,v.candidate_id,v.company,v.position,v.source,v.link
                FROM application_batch_items i
                JOIN vacancies v ON v.id=i.vacancy_id AND v.user_id=i.user_id
                WHERE i.user_id=? AND i.batch_key=? AND i.status='ready'
                """,
                (user_id, batch_key),
            )
            for _, item in items.iterrows():
                duplicate = query(
                    "SELECT id FROM applications WHERE user_id=? AND vacancy_id=? LIMIT 1",
                    (user_id, int(item["vacancy_id"])),
                )
                if duplicate.empty:
                    execute(
                        """
                        INSERT INTO applications(
                          user_id,candidate_id,vacancy_id,company,position,source,link,method,
                          resume_id,cover_style,status,notes
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            user_id, int(item["candidate_id"]), int(item["vacancy_id"]),
                            item["company"], item["position"], item["source"], item["link"],
                            "manual/batch", int(item["resume_id"] or 0), "reviewed",
                            "applied", f"Manually confirmed batch {batch_key}",
                        ),
                    )
                execute("UPDATE vacancies SET status='applied' WHERE user_id=? AND id=?", (user_id, int(item["vacancy_id"])))
                execute("UPDATE application_batch_items SET status='sent' WHERE item_key=? AND user_id=?", (item["item_key"], user_id))
            execute("UPDATE application_batches SET status='sent' WHERE batch_key=? AND user_id=?", (batch_key, user_id))
            st.session_state.pop("_batch_archive", None)
            st.session_state.pop("_batch_statuses", None)
            st.success("Статусы обновлены: отправлено вручную.")
            st.rerun()


def render_preferences(
    st: Any,
    query: Callable[..., Any],
    execute: Callable[..., Any],
) -> None:
    user_id = int(st.session_state["uid"])
    st.title("Настройки")
    current_user = query("SELECT email FROM users WHERE id=?", (user_id,))
    current_email = "" if current_user.empty else str(current_user.iloc[0]["email"] or "").strip().lower()
    admin_emails = {
        value.strip().lower()
        for value in os.getenv("ADMIN_EMAILS", OWNER_EMAIL).split(",")
        if value.strip()
    }
    is_admin = current_email in admin_emails
    tab_names = ["Внешний вид", "Поиск", "Безопасность", "Почта", "Уведомления", "Данные"]
    if is_admin:
        tab_names.append("Админ")
    tabs = st.tabs(tab_names)
    with tabs[0]:
        st.subheader("Тема, текст и компоновка")
        current_theme, current_scale, current_density = _appearance_values(query, user_id)
        theme_keys = list(THEMES)
        selected = st.radio(
            "Тема", theme_keys, index=theme_keys.index(current_theme) if current_theme in theme_keys else 0,
            format_func=lambda key: THEMES[key], horizontal=True,
        )
        scale = st.slider("Размер шрифта", 85, 125, current_scale, 5, format="%d%%")
        density_keys = list(LAYOUT_MODES)
        density = st.radio(
            "Компоновка",
            density_keys,
            index=density_keys.index(current_density),
            format_func=lambda key: LAYOUT_MODES[key],
            horizontal=True,
            help="Авто адаптирует интерфейс к экрану. Компактно показывает больше данных. Просторно делает поля и отступы крупнее.",
        )
        st.markdown(
            f"<div class='cm-font-preview cm-font-scale-{scale}'>"
            "Плейсхолдер размера: так будут выглядеть подсказки, карточки вакансий и формы ввода."
            "</div>",
            unsafe_allow_html=True,
        )
        if st.button("Применить оформление", type="primary"):
            put_setting(query, execute, user_id, "ui_theme", selected)
            put_setting(query, execute, user_id, "font_scale", scale)
            put_setting(query, execute, user_id, "layout_density", density)
            st.rerun()
    with tabs[1]:
        st.subheader("Строгий фильтр")
        stop_companies = st.text_area(
            "Стоп-компании",
            read_setting(query, user_id, "search_stop_companies", "Sber; Sberbank; Сбер; Сбербанк"),
            help="Разделяйте компании точкой с запятой.",
        )
        stop_countries = st.text_area(
            "Стоп-страны и географические ограничения",
            read_setting(query, user_id, "search_stop_countries", "Russia-only; Moscow office; Saint Petersburg office"),
        )
        salary = st.number_input(
            "Минимальная зарплата, USD/месяц", 500, 20000,
            int(read_setting(query, user_id, "search_salary_min", "1300") or 1300), 100,
        )
        age = st.slider(
            "Максимальный возраст вакансии, дней", 3, 30,
            int(read_setting(query, user_id, "search_max_age_days", "14") or 14),
        )
        hybrid = st.checkbox(
            "Разрешить гибрид/офис только во Вьетнаме",
            value=read_setting(query, user_id, "search_vietnam_hybrid", "1") == "1",
        )
        schedule = st.select_slider(
            "Как часто проверять подборку при активном сервисе",
            options=[3, 6, 12, 24],
            value=int(read_setting(query, user_id, "live_jobs_schedule_hours", "6") or 6),
            format_func=lambda value: f"каждые {value} ч.",
        )
        if st.button("Сохранить фильтры", type="primary"):
            for key, value in {
                "search_stop_companies": stop_companies,
                "search_stop_countries": stop_countries,
                "search_salary_min": salary,
                "search_max_age_days": age,
                "search_vietnam_hybrid": int(hybrid),
                "search_serbia_hybrid": 0,
                "live_jobs_schedule_hours": schedule,
            }.items():
                put_setting(query, execute, user_id, key, value)
            st.success("Фильтры сохранены.")
    with tabs[2]:
        st.subheader("Двухфакторная защита TOTP")
        user = query("SELECT email,totp_secret,totp_enabled FROM users WHERE id=?", (user_id,)).iloc[0]
        if bool(user.get("totp_enabled") or 0):
            st.success("2FA включена.")
            disable_code = st.text_input("Код для отключения", max_chars=6)
            if st.button("Отключить 2FA"):
                if verify_totp(str(user.get("totp_secret") or ""), disable_code):
                    execute("UPDATE users SET totp_enabled=0,totp_secret='' WHERE id=?", (user_id,))
                    st.rerun()
                else:
                    st.error("Неверный код.")
        else:
            secret = st.session_state.setdefault("_totp_setup_secret", make_totp_secret())
            account = str(user.get("email") or "CareerMove")
            uri = f"otpauth://totp/CareerMove:{account}?secret={secret}&issuer=CareerMove&digits=6&period=30"
            st.info("Добавьте секрет вручную в Google Authenticator, Microsoft Authenticator, 1Password или другом TOTP-приложении.")
            st.code(secret)
            with st.expander("Ссылка для совместимого аутентификатора"):
                st.code(uri)
            enable_code = st.text_input("Введите первый код", max_chars=6)
            if st.button("Включить 2FA", type="primary"):
                if verify_totp(secret, enable_code):
                    execute("UPDATE users SET totp_secret=?,totp_enabled=1 WHERE id=?", (secret, user_id))
                    st.session_state.pop("_totp_setup_secret", None)
                    st.success("2FA включена.")
                    st.rerun()
                else:
                    st.error("Код не совпал. Проверьте время на телефоне и попробуйте новый код.")
        st.caption("Refresh-сессия хранит только случайный токен; в базе находится его SHA-256 отпечаток. Срок — 30 дней, выход отзывает токен.")
    with tabs[3]:
        email = read_setting(query, user_id, "application_from_email", "")
        name = read_setting(query, user_id, "application_from_name", "")
        new_name = st.text_input("Имя отправителя", name)
        new_email = st.text_input("Email для откликов", email)
        if st.button("Сохранить подпись"):
            if not valid_email(new_email):
                st.error("Проверьте формат email.")
            else:
                put_setting(query, execute, user_id, "application_from_name", new_name.strip())
                put_setting(query, execute, user_id, "application_from_email", new_email.strip().lower())
                st.success("Подпись сохранена.")
        st.info("Пароли почты и API-ключи не сохраняются в форме. Они задаются только через защищённые Environment Variables сервиса.")
    with tabs[4]:
        st.subheader("Сводка золотых вакансий")
        email_enabled = st.checkbox(
            "Присылать сводку по email",
            value=read_setting(query, user_id, "notify_email_enabled", "0") == "1",
        )
        notify_email = st.text_input(
            "Email для уведомлений",
            read_setting(query, user_id, "notify_email", read_setting(query, user_id, "application_from_email", "")),
        )
        telegram_enabled = st.checkbox(
            "Присылать сводку в Telegram",
            value=read_setting(query, user_id, "notify_telegram_enabled", "0") == "1",
        )
        telegram_chat = st.text_input(
            "Telegram chat ID",
            read_setting(query, user_id, "notify_telegram_chat_id", ""),
            help="Chat ID можно получить после первого сообщения вашему Telegram-боту.",
        )
        email_ready = bool(
            os.getenv("RESEND_API_KEY")
            or (os.getenv("SMTP_HOST") and os.getenv("SMTP_USER") and os.getenv("SMTP_PASSWORD"))
        )
        telegram_ready = bool(os.getenv("TELEGRAM_BOT_TOKEN"))
        st.caption(
            f"Сервис email: {'готов' if email_ready else 'нужен RESEND_API_KEY или SMTP_* в настройках сервера'} · "
            f"Telegram: {'готов' if telegram_ready else 'нужен TELEGRAM_BOT_TOKEN в настройках сервера'}"
        )
        if st.button("Сохранить уведомления"):
            if email_enabled and not valid_email(notify_email):
                st.error("Проверьте email для уведомлений.")
            elif telegram_enabled and not telegram_chat.strip():
                st.error("Укажите Telegram chat ID.")
            else:
                for key, value in {
                    "notify_email_enabled": int(email_enabled),
                    "notify_email": notify_email.strip().lower(),
                    "notify_telegram_enabled": int(telegram_enabled),
                    "notify_telegram_chat_id": telegram_chat.strip(),
                }.items():
                    put_setting(query, execute, user_id, key, value)
                st.success("Настройки уведомлений сохранены.")
        st.info("Уведомление содержит только краткую сводку и ссылки. Резюме, пароль и тексты профиля в него не включаются.")
    with tabs[5]:
        st.subheader("Только данные текущего аккаунта")
        counts = {}
        for table in ("candidates", "resumes", "vacancies", "applications"):
            frame = query(f"SELECT COUNT(*) count FROM {table} WHERE user_id=?", (user_id,))
            counts[table] = int(frame.iloc[0]["count"] or 0)
        st.json(counts)
        confirm = st.text_input("Чтобы удалить вакансии и отклики этого аккаунта, введите RESET")
        if st.button("Удалить мои вакансии и отклики", disabled=confirm != "RESET"):
            execute("DELETE FROM applications WHERE user_id=?", (user_id,))
            execute("DELETE FROM vacancies WHERE user_id=?", (user_id,))
            st.success("Данные поиска текущего аккаунта удалены.")
    if is_admin:
        with tabs[6]:
            st.subheader("Публичный релиз")
            st.caption("Здесь видны только очевидные тестовые аккаунты. Реальные пользователи не попадают под правило очистки.")
            test_users = query(
                """
                SELECT id,email,created_at FROM users
                WHERE id<>? AND (
                  lower(email) LIKE '%@example.com'
                  OR lower(email) LIKE 'test%'
                  OR lower(email) LIKE 'demo%'
                  OR lower(email) LIKE '%+test@%'
                )
                ORDER BY id
                """,
                (user_id,),
            )
            st.dataframe(test_users, use_container_width=True, hide_index=True)
            cleanup = st.text_input("Для удаления показанных тестовых аккаунтов введите DELETE TEST USERS")
            if st.button(
                "Удалить тестовые аккаунты",
                disabled=test_users.empty or cleanup != "DELETE TEST USERS",
                type="primary",
            ):
                ids = [int(value) for value in test_users["id"].tolist()]
                child_tables = (
                    "application_batch_items", "application_batches", "notification_events",
                    "auth_sessions", "security_events", "applications", "vacancies",
                    "live_job_index", "live_source_cache", "resumes", "skills", "social_links",
                    "portfolio_items", "planned_skills", "daily_tasks", "assistant_messages",
                    "company_rules", "company_ratings", "job_sources", "user_emails",
                    "settings", "candidates",
                )
                for test_id in ids:
                    for table in child_tables:
                        _safe_execute(execute, f"DELETE FROM {table} WHERE user_id=?", (test_id,))
                    execute("DELETE FROM users WHERE id=?", (test_id,))
                st.success(f"Удалено тестовых аккаунтов: {len(ids)}.")
                st.rerun()


def render_funnel(st: Any, query: Callable[..., Any], user_id: int) -> None:
    found = int(query("SELECT COUNT(*) count FROM vacancies WHERE user_id=?", (user_id,)).iloc[0]["count"] or 0)
    matching = int(query("SELECT COUNT(*) count FROM vacancies WHERE user_id=? AND score>=80", (user_id,)).iloc[0]["count"] or 0)
    sent = int(query("SELECT COUNT(*) count FROM applications WHERE user_id=? AND status='applied'", (user_id,)).iloc[0]["count"] or 0)
    responses = int(query(
        "SELECT COUNT(*) count FROM applications WHERE user_id=? AND status IN ('waiting','interview','offer')",
        (user_id,),
    ).iloc[0]["count"] or 0)
    cols = st.columns(4)
    for col, label, value in zip(cols, ("Найдено", "Подходит 80%+", "Отправлено", "Ответов"), (found, matching, sent, responses)):
        col.metric(label, value)
    sources = query(
        """
        SELECT COALESCE(NULLIF(source,''),'Не указан') source,COUNT(*) count
        FROM vacancies WHERE user_id=?
        GROUP BY COALESCE(NULLIF(source,''),'Не указан')
        ORDER BY count DESC LIMIT 5
        """,
        (user_id,),
    )
    if not sources.empty:
        st.caption("Топ источников: " + " · ".join(f"{row.source}: {int(row['count'])}" for _, row in sources.iterrows()))


def _digest_text(jobs: list[dict[str, Any]], app_url: str) -> str:
    golden = [job for job in jobs if int(job.get("score") or 0) >= 80]
    lines = [f"CareerMove: {len(golden)} золотых вакансий готовы к проверке.", ""]
    seen: set[tuple[str, str]] = set()
    for job in golden:
        key = (str(job.get("company") or ""), str(job.get("title") or job.get("position") or ""))
        if key in seen:
            continue
        seen.add(key)
        link = str(job.get("url") or job.get("link") or "")
        lines.append(f"• {int(job.get('score') or 0)}% · {key[0]} — {key[1]}")
        if valid_source_url(link):
            lines.append(f"  {link}")
        if len(seen) >= 5:
            break
    if app_url:
        lines.extend(("", f"Открыть CareerMove: {app_url.rstrip('/')}"))
    return "\n".join(lines)


def _send_digest_email(to_email: str, subject: str, body: str) -> tuple[bool, str]:
    resend_key = os.getenv("RESEND_API_KEY", "").strip()
    resend_from = (
        os.getenv("PASSWORD_RESET_FROM_EMAIL", "").strip()
        or os.getenv("NOTIFICATION_FROM_EMAIL", "").strip()
        or os.getenv("RESEND_FROM_EMAIL", "").strip()
        or os.getenv("APPLICATION_FROM_EMAIL", "").strip()
    )
    if resend_key and resend_from:
        request = Request(
            "https://api.resend.com/emails",
            data=json.dumps({
                "from": resend_from,
                "to": [to_email],
                "subject": subject,
                "text": body,
            }).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {resend_key}",
                "Content-Type": "application/json",
                "User-Agent": "CareerMoveAI/12.0",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=20) as response:
                status = int(getattr(response, "status", 0) or 0)
            return 200 <= status < 300, "Email отправлен" if 200 <= status < 300 else f"Email API: HTTP {status}"
        except Exception as error:
            return False, f"Email API: {str(error)[:170]}"
    host = os.getenv("SMTP_HOST", "").strip()
    user = os.getenv("SMTP_USER", "").strip()
    password = os.getenv("SMTP_PASSWORD", "").strip()
    from_email = (
        os.getenv("PASSWORD_RESET_FROM_EMAIL", "").strip()
        or os.getenv("SMTP_FROM", "").strip()
        or os.getenv("SMTP_FROM_EMAIL", "").strip()
        or os.getenv("APPLICATION_FROM_EMAIL", "").strip()
        or user
    )
    try:
        port = int(os.getenv("SMTP_PORT", "587") or 587)
    except ValueError:
        port = 587
    if not (host and user and password and from_email):
        return False, "SMTP не настроен"
    message = EmailMessage()
    message["From"] = f"CareerMove <{from_email}>"
    message["To"] = to_email
    message["Subject"] = subject
    message.set_content(body)
    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(), timeout=20) as client:
                client.login(user, password)
                client.send_message(message)
        else:
            with smtplib.SMTP(host, port, timeout=20) as client:
                if os.getenv("SMTP_TLS", "1").strip() != "0":
                    client.starttls(context=ssl.create_default_context())
                client.login(user, password)
                client.send_message(message)
        return True, "Email отправлен"
    except Exception as error:
        return False, f"Email: {str(error)[:180]}"


def send_password_reset_email(to_email: str, reset_url: str) -> tuple[bool, str]:
    body = "\n".join(
        (
            "Здравствуйте!",
            "",
            "Вы запросили новый пароль для CareerMove.",
            f"Откройте одноразовую ссылку в течение {PASSWORD_RESET_MINUTES} минут:",
            reset_url,
            "",
            "Если это были не вы, ничего делать не нужно. Пароль не изменится.",
            "",
            "CareerMove",
        )
    )
    return _send_digest_email(to_email, "Восстановление пароля CareerMove", body)


def _send_digest_telegram(chat_id: str, body: str) -> tuple[bool, str]:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        return False, "Telegram bot не настроен"
    payload = urlencode({"chat_id": chat_id, "text": body, "disable_web_page_preview": "true"}).encode("utf-8")
    request = Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=20) as response:
            data = json.loads(response.read().decode("utf-8"))
        return bool(data.get("ok")), "Telegram отправлен" if data.get("ok") else "Telegram отклонил сообщение"
    except Exception as error:
        return False, f"Telegram: {str(error)[:180]}"


def deliver_digest_notifications(
    query: Callable[..., Any],
    execute: Callable[..., Any],
    user_id: int,
    jobs: list[dict[str, Any]],
    *,
    app_url: str = "",
) -> list[dict[str, str]]:
    golden = [job for job in jobs if int(job.get("score") or 0) >= 80]
    if not golden:
        return []
    current = datetime.now(UTC)
    slot = f"{current.date().isoformat()}-{current.hour // 6}"
    body = _digest_text(golden, app_url)
    subject = f"CareerMove · {len(golden)} золотых вакансий"
    channels = []
    if read_setting(query, user_id, "notify_email_enabled", "0") == "1":
        channels.append(("email", read_setting(query, user_id, "notify_email", "")))
    if read_setting(query, user_id, "notify_telegram_enabled", "0") == "1":
        channels.append(("telegram", read_setting(query, user_id, "notify_telegram_chat_id", "")))
    statuses: list[dict[str, str]] = []
    for channel, destination in channels:
        event_key = f"digest:{user_id}:{slot}:{channel}"
        previous = query(
            "SELECT status,detail FROM notification_events WHERE event_key=? AND user_id=?",
            (event_key, user_id),
        )
        if not previous.empty and str(previous.iloc[0]["status"]) == "sent":
            statuses.append({"channel": channel, "status": "already_sent", "detail": "Сводка этого интервала уже отправлена"})
            continue
        if channel == "email" and valid_email(destination):
            ok, detail = _send_digest_email(destination, subject, body)
        elif channel == "telegram" and destination:
            ok, detail = _send_digest_telegram(destination, body)
        else:
            ok, detail = False, "Адрес уведомления не заполнен"
        if previous.empty:
            execute(
                "INSERT INTO notification_events(event_key,user_id,channel,status,detail,created_at) VALUES(?,?,?,?,?,?)",
                (event_key, user_id, channel, "sent" if ok else "error", detail, now_iso()),
            )
        else:
            execute(
                "UPDATE notification_events SET status=?,detail=?,created_at=? WHERE event_key=? AND user_id=?",
                ("sent" if ok else "error", detail, now_iso(), event_key, user_id),
            )
        statuses.append({"channel": channel, "status": "sent" if ok else "error", "detail": detail})
    return statuses
