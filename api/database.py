from __future__ import annotations

import os
import shutil
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any

try:
    import psycopg2
except ImportError:  # pragma: no cover - optional for local SQLite development
    psycopg2 = None

BASE_DIR = Path(__file__).resolve().parents[1]


class Database:
    """Small database adapter shared by the API and scheduled worker."""

    def __init__(self) -> None:
        configured_url = os.getenv("DATABASE_URL", "").strip()
        # Secrets exported by the Vercel CLI are intentionally redacted.  A
        # literal ``[SENSITIVE]`` is not a database URL and used to make every
        # serverless request fail before SQLite could start.
        self.database_url = "" if configured_url == "[SENSITIVE]" else configured_url
        self.is_postgres = bool(self.database_url)
        self.blob_token = os.getenv("BLOB_READ_WRITE_TOKEN", "").strip()
        self.blob_enabled = not self.is_postgres and bool(self.blob_token)
        self.blob_path = os.getenv("CAREERMOVE_DB_BLOB_PATH", "careermove/private.sqlite3")
        # Vercel mounts the deployed source tree read-only.  When Blob is the
        # authoritative store, keep the short-lived SQLite working copy in
        # the writable serverless temporary directory instead.
        default_data_dir = "/tmp/careermove" if self.blob_enabled or os.getenv("VERCEL") else str(BASE_DIR / "data")
        data_dir = Path(os.getenv("DATA_DIR", default_data_dir)).expanduser()
        if not self.is_postgres:
            # /tmp is writable on serverless functions; the authoritative copy
            # is stored in a private Vercel Blob, never exposed to the browser.
            data_dir.mkdir(parents=True, exist_ok=True)
        self.sqlite_path = data_dir / "careermove_v92.sqlite3"
        self.seed_path = BASE_DIR / "api" / "seed" / "careermove_v92.sqlite3"
        if self.is_postgres and psycopg2 is None:
            raise RuntimeError("DATABASE_URL is set but psycopg2 is not installed")
        if self.blob_enabled:
            self._blob_client()

    def _blob_client(self):
        try:
            from vercel.blob import BlobNotFoundError, get as blob_get, put as blob_put
        except ImportError as exc:  # pragma: no cover - Blob is only required on the hosted API
            raise RuntimeError("BLOB_READ_WRITE_TOKEN is set but the Vercel Blob SDK is not installed") from exc
        return BlobNotFoundError, blob_get, blob_put

    def _sql(self, sql: str) -> str:
        return sql.replace("?", "%s") if self.is_postgres else sql

    def _sync_from_blob(self) -> None:
        """Fetch the latest private database snapshot before serving a request."""
        if not self.blob_enabled:
            return
        BlobNotFoundError, blob_get, _ = self._blob_client()
        try:
            result = blob_get(self.blob_path, access="private", token=self.blob_token, use_cache=False)
        except BlobNotFoundError:
            # The first schema creation owns the initial empty snapshot.
            return
        except Exception as exc:
            # A stale or disconnected Vercel Blob token must not take down the
            # whole public app.  Fall back to the packaged seed and keep the API
            # alive while storage credentials are repaired in Vercel.
            print(f"CareerMove Blob unavailable; using local SQLite fallback: {type(exc).__name__}")
            self.blob_enabled = False
            self._restore_seed_if_needed()
            return
        if result is None or not result.content:
            return
        temporary_path = self.sqlite_path.with_suffix(".download")
        temporary_path.write_bytes(result.content)
        temporary_path.replace(self.sqlite_path)

    def _restore_seed_if_needed(self) -> None:
        if self.is_postgres or self.sqlite_path.exists() and self.sqlite_path.stat().st_size > 0:
            return
        if self.seed_path.exists() and self.seed_path.stat().st_size > 0:
            self.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(self.seed_path, self.sqlite_path)

    def _sync_to_blob(self) -> None:
        """Persist a committed SQLite snapshot in private object storage."""
        if not self.blob_enabled:
            return
        _, _, blob_put = self._blob_client()
        try:
            blob_put(
                self.blob_path,
                self.sqlite_path.read_bytes(),
                access="private",
                content_type="application/vnd.sqlite3",
                add_random_suffix=False,
                overwrite=True,
                token=self.blob_token,
            )
        except Exception as exc:
            raise RuntimeError("Could not save the private database snapshot") from exc

    def _connect(self, *, refresh: bool = False):
        if self.is_postgres:
            return psycopg2.connect(self.database_url, sslmode="require")
        if refresh:
            self._sync_from_blob()
        self._restore_seed_if_needed()
        connection = sqlite3.connect(self.sqlite_path, timeout=60, check_same_thread=False)
        connection.execute("PRAGMA busy_timeout=60000")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def query(self, sql: str, params: tuple[Any, ...] = ()):
        import pandas as pd

        connection = self._connect(refresh=True)
        try:
            return pd.read_sql_query(self._sql(sql), connection, params=list(params))
        finally:
            connection.close()

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        connection = self._connect(refresh=True)
        try:
            cursor = connection.cursor()
            cursor.execute(self._sql(sql), tuple(params))
            connection.commit()
        finally:
            connection.close()
        self._sync_to_blob()

    @contextmanager
    def transaction(self):
        """Reuse one database connection for a complete API operation."""
        connection = self._connect(refresh=True)
        mutated = False

        def query(sql: str, params: tuple[Any, ...] = ()):
            import pandas as pd

            return pd.read_sql_query(self._sql(sql), connection, params=list(params))

        def execute(sql: str, params: tuple[Any, ...] = ()) -> None:
            nonlocal mutated
            cursor = connection.cursor()
            cursor.execute(self._sql(sql), tuple(params))
            mutated = True

        try:
            yield query, execute
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        # A read-only dashboard used to upload the complete SQLite snapshot
        # on every page refresh.  That made the UI appear to hang and created
        # avoidable write races with an active search.  Blob remains the
        # authority for every mutation, but reads now download once and return.
        if mutated:
            self._sync_to_blob()

    def ensure_schema(self) -> None:
        primary_key = "BIGSERIAL PRIMARY KEY" if self.is_postgres else "INTEGER PRIMARY KEY AUTOINCREMENT"
        statements = [
            f"""
            CREATE TABLE IF NOT EXISTS users(
              id {primary_key}, email TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL,
              password_version TEXT DEFAULT 'pbkdf2_sha256', totp_secret TEXT DEFAULT '',
              totp_enabled INTEGER DEFAULT 0, last_login_at TEXT DEFAULT '',
              created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """,
            f"""
            CREATE TABLE IF NOT EXISTS user_emails(
              id {primary_key}, user_id INTEGER NOT NULL, email TEXT NOT NULL,
              label TEXT DEFAULT 'primary', enabled INTEGER DEFAULT 1
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS settings(
              user_id INTEGER NOT NULL, key TEXT NOT NULL, value TEXT DEFAULT '',
              PRIMARY KEY(user_id,key)
            )
            """,
            f"""
            CREATE TABLE IF NOT EXISTS candidates(
              id {primary_key}, user_id INTEGER NOT NULL, name TEXT NOT NULL,
              emoji TEXT DEFAULT '👤', target_title TEXT DEFAULT '', age INTEGER DEFAULT 0,
              citizenship TEXT DEFAULT '', native_languages TEXT DEFAULT '',
              english_level TEXT DEFAULT '', desired_countries TEXT DEFAULT '',
              salary_min INTEGER DEFAULT 0, notes TEXT DEFAULT '', private_hints TEXT DEFAULT '',
              hard_exclude TEXT DEFAULT '', hard_require TEXT DEFAULT '',
              created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """,
            f"""
            CREATE TABLE IF NOT EXISTS resumes(
              id {primary_key}, user_id INTEGER NOT NULL, candidate_id INTEGER NOT NULL,
              language TEXT DEFAULT 'EN', title TEXT DEFAULT '', content TEXT DEFAULT '',
              locked INTEGER DEFAULT 0, photo_path TEXT DEFAULT '', photo_data TEXT DEFAULT '',
              updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """,
            f"""
            CREATE TABLE IF NOT EXISTS skills(
              id {primary_key}, user_id INTEGER NOT NULL, candidate_id INTEGER NOT NULL,
              skill TEXT NOT NULL
            )
            """,
            f"""
            CREATE TABLE IF NOT EXISTS social_links(
              id {primary_key}, user_id INTEGER NOT NULL, candidate_id INTEGER NOT NULL,
              platform TEXT DEFAULT '', url TEXT DEFAULT '',
              show_global INTEGER DEFAULT 1, show_foreign INTEGER DEFAULT 1,
              show_ru INTEGER DEFAULT 1, notes TEXT DEFAULT ''
            )
            """,
            f"""
            CREATE TABLE IF NOT EXISTS company_ratings(
              id {primary_key}, user_id INTEGER NOT NULL, company TEXT NOT NULL,
              country TEXT DEFAULT '', industry_tag TEXT DEFAULT '', rating INTEGER DEFAULT 0,
              stability INTEGER DEFAULT 0, remote_friendly INTEGER DEFAULT 0,
              b1_friendly INTEGER DEFAULT 0, official_score INTEGER DEFAULT 0,
              notes TEXT DEFAULT '', updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """,
            f"""
            CREATE TABLE IF NOT EXISTS daily_tasks(
              id {primary_key}, user_id INTEGER NOT NULL, title TEXT NOT NULL,
              kind TEXT DEFAULT '', scheduled_time TEXT DEFAULT '', done INTEGER DEFAULT 0,
              due_date TEXT DEFAULT '', reschedule_until TEXT DEFAULT '',
              duration_days INTEGER DEFAULT 0, priority TEXT DEFAULT 'normal',
              created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """,
            f"""
            CREATE TABLE IF NOT EXISTS job_sources(
              id {primary_key}, user_id INTEGER NOT NULL, service TEXT NOT NULL,
              source_type TEXT DEFAULT '', region TEXT DEFAULT '', url TEXT DEFAULT '',
              enabled INTEGER DEFAULT 1, notes TEXT DEFAULT ''
            )
            """,
            f"""
            CREATE TABLE IF NOT EXISTS vacancies(
              id {primary_key}, user_id INTEGER NOT NULL, candidate_id INTEGER NOT NULL,
              fetched_at TEXT DEFAULT '', posted_at TEXT DEFAULT '', source TEXT DEFAULT '',
              service TEXT DEFAULT '', company TEXT DEFAULT '', company_country TEXT DEFAULT '',
              position TEXT DEFAULT '', industry_tag TEXT DEFAULT '', company_rating INTEGER DEFAULT 0,
              link TEXT DEFAULT '', language TEXT DEFAULT '', remote_location TEXT DEFAULT '',
              worker_country TEXT DEFAULT '', salary_text TEXT DEFAULT '', salary_min INTEGER DEFAULT 0,
              score INTEGER DEFAULT 0, category TEXT DEFAULT '', status TEXT DEFAULT 'found',
              strengths TEXT DEFAULT '', weaknesses TEXT DEFAULT '', positioning TEXT DEFAULT '',
              recommendation TEXT DEFAULT '', risk TEXT DEFAULT '', cover_letter TEXT DEFAULT '',
              feedback TEXT DEFAULT '', feedback_note TEXT DEFAULT '', blocked_reason TEXT DEFAULT '',
              employer_email TEXT DEFAULT '', employer_contact TEXT DEFAULT '',
              source_snapshot TEXT DEFAULT '', perk_match TEXT DEFAULT '', fit_type TEXT DEFAULT '',
              work_type TEXT DEFAULT '', final_salary_advice TEXT DEFAULT '',
              ai_analysis TEXT DEFAULT '', ai_review_status TEXT DEFAULT 'not_analyzed'
            )
            """,
            f"""
            CREATE TABLE IF NOT EXISTS applications(
              id {primary_key}, user_id INTEGER NOT NULL, candidate_id INTEGER,
              vacancy_id INTEGER, company TEXT DEFAULT '', position TEXT DEFAULT '',
              source TEXT DEFAULT '', link TEXT DEFAULT '', method TEXT DEFAULT '',
              resume_id INTEGER, cover_style TEXT DEFAULT '', status TEXT DEFAULT 'ready',
              notes TEXT DEFAULT '', created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """,
            f"""
            CREATE TABLE IF NOT EXISTS application_tracker(
              id {primary_key}, user_id INTEGER NOT NULL, vacancy_id INTEGER NOT NULL,
              candidate_id INTEGER NOT NULL, candidate TEXT DEFAULT '',
              applied_at TEXT DEFAULT '', response_at TEXT DEFAULT '',
              position TEXT DEFAULT '', company TEXT DEFAULT '', result TEXT DEFAULT '',
              comments TEXT DEFAULT '', salary_range TEXT DEFAULT '', language TEXT DEFAULT '',
              vacancy_link TEXT DEFAULT '', vacancy_source TEXT DEFAULT '',
              sync_status TEXT DEFAULT 'pending', sync_error TEXT DEFAULT '',
              synced_at TEXT DEFAULT '', updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
              item_type TEXT DEFAULT 'Вакансия', score INTEGER DEFAULT 0,
              priority TEXT DEFAULT '', favorite INTEGER DEFAULT 0,
              status TEXT DEFAULT 'В работу', cover_formal TEXT DEFAULT '',
              cover_friendly TEXT DEFAULT '', cover_detailed TEXT DEFAULT '',
              from_email TEXT DEFAULT '',
              UNIQUE(user_id,vacancy_id)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS search_runs_v2(
              run_id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, status TEXT NOT NULL,
              stage TEXT DEFAULT '', detail TEXT DEFAULT '', result_json TEXT DEFAULT '',
              error TEXT DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )
            """,
        ]
        from app import live_jobs, public_release

        # Keep startup schema checks on one connection. This materially reduces
        # cold-start time for serverless Postgres providers, where opening a new
        # TLS connection for every statement can take minutes.
        connection = self._connect(refresh=True)
        try:
            cursor = connection.cursor()
            savepoint_index = 0

            def schema_execute(sql_text: str, params: tuple[Any, ...] = ()) -> None:
                nonlocal savepoint_index
                savepoint_index += 1
                savepoint = f"careermove_schema_{savepoint_index}"
                cursor.execute(f"SAVEPOINT {savepoint}")
                try:
                    cursor.execute(self._sql(sql_text), tuple(params))
                except Exception:
                    cursor.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                    cursor.execute(f"RELEASE SAVEPOINT {savepoint}")
                    raise
                cursor.execute(f"RELEASE SAVEPOINT {savepoint}")

            for statement in statements:
                schema_execute(statement)
            # A small forward-only migration.  Photos are stored only with the
            # owner's resume record; no shared object storage or public URL is
            # used for this sensitive data.
            try:
                schema_execute("ALTER TABLE resumes ADD COLUMN photo_data TEXT DEFAULT ''")
            except Exception:
                pass
            try:
                schema_execute("ALTER TABLE vacancies ADD COLUMN favorite INTEGER DEFAULT 0")
            except Exception:
                pass
            # Profile-level search and application preferences were introduced
            # after the first public beta.  Keep these as small forward-only
            # migrations so existing private cabinets upgrade in place.
            for statement in (
                "ALTER TABLE candidates ADD COLUMN preferred_regions TEXT DEFAULT ''",
                "ALTER TABLE candidates ADD COLUMN preferred_cities TEXT DEFAULT ''",
                "ALTER TABLE candidates ADD COLUMN preferred_companies TEXT DEFAULT ''",
                "ALTER TABLE candidates ADD COLUMN priority_titles TEXT DEFAULT ''",
                "ALTER TABLE candidates ADD COLUMN contact_email TEXT DEFAULT ''",
                "ALTER TABLE candidates ADD COLUMN cover_tone TEXT DEFAULT ''",
                "ALTER TABLE candidates ADD COLUMN cover_length TEXT DEFAULT ''",
                "ALTER TABLE candidates ADD COLUMN manual_review INTEGER DEFAULT 1",
                "ALTER TABLE applications ADD COLUMN cover_letter TEXT DEFAULT ''",
                "ALTER TABLE applications ADD COLUMN recipient_email TEXT DEFAULT ''",
                "ALTER TABLE applications ADD COLUMN subject TEXT DEFAULT ''",
                "ALTER TABLE applications ADD COLUMN resume_language TEXT DEFAULT ''",
                "ALTER TABLE application_tracker ADD COLUMN item_type TEXT DEFAULT 'Вакансия'",
                "ALTER TABLE application_tracker ADD COLUMN score INTEGER DEFAULT 0",
                "ALTER TABLE application_tracker ADD COLUMN priority TEXT DEFAULT ''",
                "ALTER TABLE application_tracker ADD COLUMN favorite INTEGER DEFAULT 0",
                "ALTER TABLE application_tracker ADD COLUMN status TEXT DEFAULT 'В работу'",
                "ALTER TABLE application_tracker ADD COLUMN cover_formal TEXT DEFAULT ''",
                "ALTER TABLE application_tracker ADD COLUMN cover_friendly TEXT DEFAULT ''",
                "ALTER TABLE application_tracker ADD COLUMN cover_detailed TEXT DEFAULT ''",
                "ALTER TABLE application_tracker ADD COLUMN from_email TEXT DEFAULT ''",
            ):
                try:
                    schema_execute(statement)
                except Exception:
                    # The column already exists on upgraded installations.
                    pass
            schema_execute(
                f"""
                CREATE TABLE IF NOT EXISTS certificates(
                  id {primary_key}, user_id INTEGER NOT NULL, candidate_id INTEGER NOT NULL,
                  title TEXT NOT NULL, issuer TEXT DEFAULT '', credential_url TEXT DEFAULT '',
                  issued_at TEXT DEFAULT '', notes TEXT DEFAULT '',
                  include_in_resume INTEGER DEFAULT 1, created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            schema_execute(
                """
                CREATE TABLE IF NOT EXISTS search_schedules(
                  user_id INTEGER PRIMARY KEY, enabled INTEGER DEFAULT 0,
                  frequency TEXT DEFAULT 'once', updated_at TEXT DEFAULT '',
                  last_run_at TEXT DEFAULT '', last_run_status TEXT DEFAULT ''
                )
                """
            )
            schema_execute(
                f"""
                CREATE TABLE IF NOT EXISTS side_gigs(
                  id {primary_key}, user_id INTEGER NOT NULL, candidate_id INTEGER NOT NULL,
                  title TEXT NOT NULL, client TEXT DEFAULT '', source TEXT DEFAULT '', link TEXT DEFAULT '',
                  location TEXT DEFAULT '', category TEXT DEFAULT '', work_format TEXT DEFAULT '',
                  pay_text TEXT DEFAULT '', description TEXT DEFAULT '', contacts_json TEXT DEFAULT '',
                  score INTEGER DEFAULT 0, status TEXT DEFAULT 'found', favorite INTEGER DEFAULT 0,
                  posted_at TEXT DEFAULT '', active_checked_at TEXT DEFAULT '', is_active INTEGER DEFAULT 1,
                  safety_note TEXT DEFAULT '', requirements_note TEXT DEFAULT '', source_snapshot TEXT DEFAULT '',
                  created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            public_release.ensure_schema(schema_execute, postgres=self.is_postgres)
            live_jobs.ensure_schema(schema_execute)
            schema_execute(
                "CREATE INDEX IF NOT EXISTS idx_search_runs_user ON search_runs_v2(user_id,created_at)"
            )
            schema_execute(
                "CREATE INDEX IF NOT EXISTS idx_application_tracker_user ON application_tracker(user_id,applied_at)"
            )
            schema_execute(
                "CREATE INDEX IF NOT EXISTS idx_social_links_candidate ON social_links(user_id,candidate_id)"
            )
            connection.commit()
        finally:
            connection.close()
        self._sync_to_blob()
