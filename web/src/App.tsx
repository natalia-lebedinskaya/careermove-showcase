import { ChangeEvent, FormEvent, ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError, getToken, setToken } from "./api";
import type {
  AiServiceStatus,
  AiSettings,
  Appearance,
  Application,
  ApplicationPreferences,
  ApplicationTracker,
  Candidate,
  CandidateInput,
  Certificate,
  Dashboard,
  Gig,
  HigherEducationOption,
  Job,
  SearchRun,
  SpecialAttention,
  TelegramBotStatus,
  TrackerRow,
  User,
  View,
} from "./types";

type InstallPrompt = Event & {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed" }>;
};

const DEFAULT_APPEARANCE: Appearance = {
  theme: "system-light",
  font_scale: 100,
  density: "auto",
};

const APPEARANCE_STORAGE_KEY = "careermove.appearance";

function readStoredAppearance(): Appearance {
  try {
    const saved = JSON.parse(localStorage.getItem(APPEARANCE_STORAGE_KEY) || "null") as Partial<Appearance> | null;
    const themes: Appearance["theme"][] = ["system-light", "system-dark", "cyber-aurora"];
    const densities: Appearance["density"][] = ["auto", "compact", "comfortable"];
    const fontScales = [90, 100, 110, 120, 125];
    if (saved && themes.includes(saved.theme as Appearance["theme"])
      && densities.includes(saved.density as Appearance["density"])
      && fontScales.includes(Number(saved.font_scale))) {
      return saved as Appearance;
    }
  } catch {
    // A corrupt browser preference should never prevent the app from loading.
  }
  return DEFAULT_APPEARANCE;
}

function storeAppearance(appearance: Appearance) {
  try {
    localStorage.setItem(APPEARANCE_STORAGE_KEY, JSON.stringify(appearance));
  } catch {
    // Private browsing can reject storage; the in-memory preference still works.
  }
}

const MAIN_MATCH_SCORE = 60;

const NAV_ITEMS: Array<{ id: View; label: string; icon: IconName }> = [
  { id: "today", label: "Сегодня", icon: "home" },
  { id: "jobs", label: "Вакансии", icon: "briefcase" },
  { id: "special-attention", label: "Особое внимание", icon: "star" },
  { id: "gigs", label: "Подработки", icon: "signal" },
  { id: "internships", label: "Стажировка", icon: "shield" },
  { id: "combine", label: "Совмещение", icon: "clock" },
  { id: "higher-education", label: "Высшее обучение", icon: "book" },
  { id: "ai-chat", label: "AI-чат", icon: "shield" },
  { id: "education", label: "Обучение", icon: "shield" },
  { id: "work", label: "В работе", icon: "clock" },
  { id: "profiles", label: "Профили", icon: "users" },
  { id: "applications", label: "Отклики", icon: "send" },
  { id: "settings", label: "Настройки", icon: "settings" },
];

type IconName =
  | "home"
  | "briefcase"
  | "users"
  | "send"
  | "settings"
  | "refresh"
  | "download"
  | "external"
  | "check"
  | "clock"
  | "close"
  | "signal"
  | "shield"
  | "book"
  | "star"
  | "menu"
  | "eye"
  | "eyeOff"
  | "arrowLeft";

const ICON_PATHS: Record<IconName, ReactNode> = {
  home: <><path d="m3 11 9-8 9 8"/><path d="M5 10v10h14V10"/><path d="M9 20v-6h6v6"/></>,
  briefcase: <><rect x="3" y="7" width="18" height="13" rx="2"/><path d="M8 7V4h8v3M3 12h18M10 12v2h4v-2"/></>,
  users: <><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75"/></>,
  send: <><path d="m22 2-7 20-4-9-9-4Z"/><path d="M22 2 11 13"/></>,
  settings: <><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .34 1.88l.06.06-2.83 2.83-.06-.06A1.7 1.7 0 0 0 15 19.4a1.7 1.7 0 0 0-1 .6 1.7 1.7 0 0 0-.4 1.1V21h-4v-.09A1.7 1.7 0 0 0 8.6 19.4a1.7 1.7 0 0 0-1.88.34l-.06.06-2.83-2.83.06-.06A1.7 1.7 0 0 0 4.6 15a1.7 1.7 0 0 0-.6-1 1.7 1.7 0 0 0-1.1-.4H3v-4h.09A1.7 1.7 0 0 0 4.6 8.6a1.7 1.7 0 0 0-.34-1.88l-.06-.06 2.83-2.83.06.06A1.7 1.7 0 0 0 9 4.6a1.7 1.7 0 0 0 1-.6 1.7 1.7 0 0 0 .4-1.1V3h4v.09A1.7 1.7 0 0 0 15.4 4.6a1.7 1.7 0 0 0 1.88-.34l.06-.06 2.83 2.83-.06.06A1.7 1.7 0 0 0 19.4 9c.38.25.7.6.89 1H21v4h-.09c-.44.08-.84.3-1.1.6-.2.12-.34.26-.41.4Z"/></>,
  refresh: <><path d="M20 6v5h-5"/><path d="M4 18v-5h5"/><path d="M18.5 9A7 7 0 0 0 6.2 6.2L4 8M20 16l-2.2 1.8A7 7 0 0 1 5.5 15"/></>,
  download: <><path d="M12 3v12"/><path d="m7 10 5 5 5-5"/><path d="M5 21h14"/></>,
  external: <><path d="M15 3h6v6"/><path d="m10 14 11-11"/><path d="M18 13v7H4V6h7"/></>,
  check: <path d="m5 12 4 4L19 6"/>,
  clock: <><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></>,
  close: <><path d="m6 6 12 12M18 6 6 18"/></>,
  signal: <><path d="M5 20v-4M10 20v-8M15 20V8M20 20V4"/></>,
  shield: <><path d="M12 3 4 6v5c0 5 3.4 8.4 8 10 4.6-1.6 8-5 8-10V6Z"/><path d="m9 12 2 2 4-5"/></>,
  book: <><path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H11v16H6.5A2.5 2.5 0 0 0 4 21.5Z"/><path d="M20 5.5A2.5 2.5 0 0 0 17.5 3H13v16h4.5a2.5 2.5 0 0 1 2.5 2.5Z"/></>,
  star: <path d="m12 3 2.8 5.7 6.2.9-4.5 4.4 1.1 6.2-5.6-2.9-5.6 2.9 1.1-6.2L3 9.6l6.2-.9Z"/>,
  menu: <><path d="M4 7h16M4 12h16M4 17h16"/></>,
  eye: <><path d="M2.5 12s3.5-6 9.5-6 9.5 6 9.5 6-3.5 6-9.5 6-9.5-6-9.5-6Z"/><circle cx="12" cy="12" r="2.5"/></>,
  eyeOff: <><path d="m3 3 18 18"/><path d="M10.6 6.2A10 10 0 0 1 12 6c6 0 9.5 6 9.5 6a15 15 0 0 1-2.1 2.8M6.2 6.2C3.8 7.9 2.5 12 2.5 12s3.5 6 9.5 6a9.8 9.8 0 0 0 3.5-.6"/><path d="M10.2 10.2a2.5 2.5 0 0 0 3.6 3.6"/></>,
  arrowLeft: <><path d="m15 18-6-6 6-6"/><path d="M9 12h11"/></>,
};

function Icon({ name, size = 20 }: { name: IconName; size?: number }) {
  return (
    <svg
      aria-hidden="true"
      className="icon"
      fill="none"
      height={size}
      viewBox="0 0 24 24"
      width={size}
      stroke="currentColor"
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeWidth="1.8"
    >
      {ICON_PATHS[name]}
    </svg>
  );
}

function Logo() {
  return (
    <div className="brand">
      <div className="brand-mark" aria-hidden="true">CM</div>
      <div>
        <strong>CareerMove</strong>
        <span>точный поиск работы</span>
      </div>
    </div>
  );
}

function Button({
  children,
  className = "",
  icon,
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { icon?: IconName }) {
  return (
    <button className={`button ${className}`.trim()} {...props}>
      {icon && <Icon name={icon} size={18} />}
      <span>{children}</span>
    </button>
  );
}

function EmptyState({
  title,
  text,
  action,
}: {
  title: string;
  text: string;
  action?: ReactNode;
}) {
  return (
    <div className="empty-state">
      <div className="empty-icon"><Icon name="signal" size={24} /></div>
      <h3>{title}</h3>
      <p>{text}</p>
      {action}
    </div>
  );
}

type ServiceHealth = {
  api: "checking" | "online" | "offline" | "auth";
  ai?: AiServiceStatus;
  checkedAt?: string;
  message?: string;
};

function ServiceBadge({ health }: { health: ServiceHealth }) {
  const offline = health.api === "offline";
  const auth = health.api === "auth";
  const checking = health.api === "checking";
  const level = offline ? "error" : auth ? "warning" : health.ai?.level || (checking ? "muted" : "ok");
  const provider = health.ai?.provider && health.ai?.model ? `${health.ai.provider} · ${health.ai.model}` : health.ai?.title || "AI status";
  const label = auth ? "Нужно войти" : offline ? "API offline" : checking ? "Проверка API" : provider;
  const detail = auth ? (health.message || "Сессия не активна") : offline ? (health.message || "Нет ответа от сервера") : health.ai?.detail || "API отвечает";
  const time = health.checkedAt || health.ai?.checked_at || "";
  const timeLabel = time ? new Date(time).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" }) : "";
  return (
    <div className={`service-badge ${level}`} title={detail} aria-live="polite">
      <span className="service-dot" />
      <span className="service-main">{label}</span>
      {timeLabel ? <span className="service-time">{timeLabel}</span> : null}
    </div>
  );
}

type AuthMode = "login" | "register" | "forgot" | "reset";

function AuthScreen({
  onAuthenticated,
  onInstall,
}: {
  onAuthenticated: (user: User) => void;
  onInstall: () => void;
}) {
  const [resetToken] = useState(
    () => new URLSearchParams(window.location.search).get("reset")?.trim() || "",
  );
  const [mode, setMode] = useState<AuthMode>(() => resetToken ? "reset" : "login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [privacy, setPrivacy] = useState(false);
  const [challenge, setChallenge] = useState("");
  const [totp, setTotp] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [resetUrl, setResetUrl] = useState("");
  const [resetDelivery, setResetDelivery] = useState("");

  const [success, setSuccess] = useState("");

  useEffect(() => {
    if (!resetToken) return;
    window.history.replaceState({}, document.title, `${window.location.pathname}${window.location.hash}`);
  }, [resetToken]);

  function changeMode(next: AuthMode) {
    setMode(next);
    setChallenge("");
    setPassword("");
    setConfirmPassword("");
    setShowPassword(false);
    setError("");
    setSuccess("");
    setResetUrl("");
    setResetDelivery("");
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    setSuccess("");
    try {
      if (challenge) {
        const result = await api.totp(challenge, totp);
        setToken(result.token);
        onAuthenticated(result.user);
      } else if (mode === "forgot") {
        const result = await api.forgotPassword(email);
        setSuccess(result.message);
        setResetUrl(result.reset_url || "");
        setResetDelivery(result.delivery || "");
      } else if (mode === "reset") {
        if (password !== confirmPassword) throw new Error("Пароли не совпадают.");
        const result = await api.resetPassword(resetToken, password);
        setMode("login");
        setPassword("");
        setConfirmPassword("");
        setSuccess(result.message);
      } else if (mode === "register") {
        const result = await api.register(email, password, privacy);
        setToken(result.token);
        onAuthenticated(result.user);
      } else {
        const result = await api.login(email, password);
        if (result.requires_totp && result.challenge) {
          setChallenge(result.challenge);
          return;
        }
        if (!result.token || !result.user) throw new Error("Вход не завершён.");
        setToken(result.token);
        onAuthenticated(result.user);
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Не удалось войти.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="auth-layout">
      <section className="auth-story">
        <Logo />
        <div className="auth-copy">
          <span className="eyebrow">CAREERMOVE · НОВОЕ ПРИЛОЖЕНИЕ</span>
          <h1>Подходящие вакансии — без перезагрузок и лишнего шума</h1>
          <p>
            Один кабинет для поиска, проверки карточек и ручной отправки откликов.
            Устанавливается на телефон и компьютер.
          </p>
        </div>
        <div className="auth-points">
          <span><Icon name="shield" size={18} /> Данные пользователей изолированы</span>
          <span><Icon name="signal" size={18} /> Подборка обновляется на сервере</span>
          <span><Icon name="download" size={18} /> Работает как отдельное приложение</span>
        </div>
      </section>

      <section className="auth-panel" aria-label="Вход в CareerMove">
        <div className="auth-mobile-brand"><Logo /></div>
        {challenge ? (
          <>
            <span className="eyebrow">ДВУХФАКТОРНАЯ ЗАЩИТА</span>
            <h2>Подтвердите вход</h2>
            <p className="secondary">Введите шестизначный код из приложения-аутентификатора.</p>
          </>
        ) : mode === "forgot" ? (
          <>
            <button className="auth-back" onClick={() => changeMode("login")} type="button">
              <Icon name="arrowLeft" size={18} /> Вернуться ко входу
            </button>
            <span className="eyebrow">ВОССТАНОВЛЕНИЕ ДОСТУПА</span>
            <h2>Получить ссылку</h2>
            <p className="secondary">Отправим одноразовую ссылку на адрес вашего аккаунта.</p>
          </>
        ) : mode === "reset" ? (
          <>
            <span className="eyebrow">НОВЫЙ ПАРОЛЬ</span>
            <h2>Защитите аккаунт</h2>
            <p className="secondary">Придумайте новый пароль длиной не менее 8 символов.</p>
          </>
        ) : (
          <>
            <div className="auth-tabs" role="tablist" aria-label="Авторизация">
              <button
                aria-selected={mode === "login"}
                onClick={() => changeMode("login")}
                role="tab"
                type="button"
              >
                Войти
              </button>
              <button
                aria-selected={mode === "register"}
                onClick={() => changeMode("register")}
                role="tab"
                type="button"
              >
                Создать аккаунт
              </button>
            </div>
            <h2>{mode === "login" ? "С возвращением" : "Новый личный кабинет"}</h2>
            <p className="secondary">
              {mode === "login"
                ? "Ваши профили, вакансии и статусы останутся на месте."
                : "Новый пользователь не увидит чужие профили и историю."}
            </p>
          </>
        )}

        <form className="form-stack" onSubmit={submit}>
          {!challenge && (
            <>
              {mode !== "reset" && (
                <label>
                  <span>Email</span>
                  <input
                    autoComplete={mode === "register" || mode === "forgot" ? "email" : "username"}
                    inputMode="email"
                    onChange={(event) => setEmail(event.target.value)}
                    placeholder="name@example.com"
                    required
                    type="email"
                    value={email}
                  />
                </label>
              )}
              {mode !== "forgot" && (
                <div className="form-field">
                  <label htmlFor="auth-password">
                    {mode === "reset" ? "Новый пароль" : "Пароль"}
                  </label>
                  <div className="password-field">
                    <input
                      autoComplete={mode === "login" ? "current-password" : "new-password"}
                      id="auth-password"
                      minLength={8}
                      onChange={(event) => setPassword(event.target.value)}
                      placeholder="Минимум 8 символов"
                      required
                      type={showPassword ? "text" : "password"}
                      value={password}
                    />
                    <button
                      aria-label={showPassword ? "Скрыть пароль" : "Показать пароль"}
                      aria-pressed={showPassword}
                      className="password-toggle"
                      onClick={() => setShowPassword((visible) => !visible)}
                      title={showPassword ? "Скрыть пароль" : "Показать пароль"}
                      type="button"
                    >
                      <Icon name={showPassword ? "eyeOff" : "eye"} size={20} />
                    </button>
                  </div>
                </div>
              )}
              {mode === "reset" && (
                <label>
                  <span>Повторите пароль</span>
                  <input
                    autoComplete="new-password"
                    minLength={8}
                    onChange={(event) => setConfirmPassword(event.target.value)}
                    placeholder="Ещё раз новый пароль"
                    required
                    type={showPassword ? "text" : "password"}
                    value={confirmPassword}
                  />
                </label>
              )}
            </>
          )}
          {challenge && (
            <label>
              <span>Код подтверждения</span>
              <input
                autoComplete="one-time-code"
                inputMode="numeric"
                maxLength={6}
                onChange={(event) => setTotp(event.target.value.replace(/\D/g, ""))}
                placeholder="000000"
                required
                value={totp}
              />
            </label>
          )}
          {mode === "register" && !challenge && (
            <label className="check-row">
              <input
                checked={privacy}
                onChange={(event) => setPrivacy(event.target.checked)}
                type="checkbox"
              />
              <span>Принимаю Privacy Policy и Terms beta-версии</span>
            </label>
          )}
          {error && <div className="inline-error" role="alert">{error}</div>}
          {success && <div className="inline-success" role="status">{success}</div>}
          {resetUrl && resetDelivery === "manual_link" && (
            <a
              className="button secondary full"
              href={`mailto:${encodeURIComponent(email)}?subject=${encodeURIComponent("CareerMove: восстановление доступа")}&body=${encodeURIComponent(`Одноразовая ссылка CareerMove действует 30 минут:\n\n${resetUrl}\n\nЕсли вы не запрашивали сброс пароля, не открывайте её.`)}`}
            >
              <Icon name="send" size={18} /> Открыть готовое письмо в Outlook
            </a>
          )}
          {resetUrl && <a className="button ghost full" href={resetUrl}>Открыть ссылку восстановления</a>}
          <Button className="primary full" disabled={busy} type="submit">
            {busy
              ? "Подождите…"
              : challenge
                ? "Подтвердить"
                : mode === "login"
                  ? "Войти"
                  : mode === "register"
                    ? "Создать кабинет"
                    : mode === "forgot"
                      ? "Отправить ссылку"
                      : "Сохранить новый пароль"}
          </Button>
          {mode === "login" && !challenge && (
            <button className="auth-link" onClick={() => changeMode("forgot")} type="button">
              Не помню пароль
            </button>
          )}
          {mode === "reset" && (
            <button className="auth-link" onClick={() => changeMode("login")} type="button">
              Вернуться ко входу
            </button>
          )}
          {challenge && (
            <Button className="ghost full" onClick={() => setChallenge("")} type="button">
              Вернуться к входу
            </Button>
          )}
        </form>
        {!challenge && (
          <button className="auth-install" onClick={onInstall} type="button">
            <Icon name="download" size={18} />
            Установить на iPhone или компьютер
          </button>
        )}
      </section>
    </main>
  );
}

function MetricCard({
  label,
  value,
  note,
  tone,
}: {
  label: string;
  value: number;
  note: string;
  tone: "blue" | "violet" | "teal" | "neutral";
}) {
  return (
    <article className={`metric-card ${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{note}</small>
    </article>
  );
}

function SearchStatus({ run }: { run: SearchRun | null }) {
  if (!run) return null;
  const active = run.status === "queued" || run.status === "running";
  const failed = run.status === "failed";
  const result = !active && !failed ? run.result : undefined;
  const message = failed && run.error ? `${run.detail || "Поиск не завершён."} ${run.error}` : run.detail;
  const resultSummary = result
    ? ` Проверено карточек: ${result.raw_count || 0}; новых: ${result.new_count || 0}; обновлено: ${result.updated_count || 0}; повторно проверено: ${result.rechecked_count || 0}; снято: ${result.archived_count || 0}; в активной выдаче: ${result.active_count ?? result.saved_count ?? 0}; совпадений 60%+: ${result.golden_count || 0}.`
    : "";
  return (
    <section className={`search-status ${active ? "active" : ""} ${failed ? "failed" : ""}`} aria-live="polite">
      <div className="status-indicator">
        {active ? <span className="spinner" /> : <Icon name={failed ? "close" : "check"} size={20} />}
      </div>
      <div className="status-copy">
        <strong>
          {active ? "Подборка обновляется в фоне" : failed ? "Поиск не завершён" : "Подборка готова"}
        </strong>
        <span>{`${message || "Сервис готов к следующему запуску."}${resultSummary}`}</span>
      </div>
      <span className="status-badge">
        {run.stage === "sources" ? "Источники" : run.stage === "streaming" ? "Карточки" : run.stage === "matching" ? "Совпадения" : run.stage === "saving" ? "Сохранение" : active ? "В работе" : failed ? "Ошибка" : "Готово"}
      </span>
    </section>
  );
}

function jobFreshness(job: Job) {
  const raw = job.posted_at || job.verified_at || job.last_seen;
  if (!raw) return "Дата проверяется";
  const date = new Date(raw);
  if (Number.isNaN(date.getTime())) return "Активность проверяется";
  const label = date.toLocaleDateString("ru-RU", { day: "numeric", month: "short" });
  return job.posted_at ? `Опубликована ${label}` : `Активна в источнике · проверено ${label}`;
}

function jobTags(job: Job) {
  const rawTags = job.preview?.tags || [];
  const text = [job.remote_location, job.source, job.position, job.company, ...rawTags].join(" ").toLowerCase();
  const tags = new Set<string>();
  if (/\b(remote|удал|home office)\b/.test(text)) tags.add("Удалённо");
  if (/vietnam|вьетнам|da nang|дананг|danang|hanoi|ханой|ho chi minh|хошимин/.test(text)) tags.add("Вьетнам");
  if (/da nang|дананг|danang/.test(text)) tags.add("Дананг");
  if (/\b(usa|u\.s\.|сша|america)\b/.test(text)) tags.add("США");
  if (/\b(india|india|инди)\b/.test(text)) tags.add("Индия");
  if (/\b(eu|europe|европ)\b/.test(text)) tags.add("Европа");
  if (/hybrid|гибрид/.test(text)) tags.add("Гибрид");
  if (job.moonlight_compatible) tags.add("Можно совмещать");
  if (job.contacts?.telegram?.length || /telegram|телеграм|@\w{4,}/i.test([job.employer_contact, job.source, job.preview?.description].join(" "))) tags.add("Telegram");
  if (job.equipment?.length) tags.add("Техника");
  if (job.salary_text && !/не указана/i.test(job.salary_text)) tags.add("Зарплата указана");
  if (job.source) tags.add(job.source);
  rawTags.slice(0, 3).forEach((tag) => tags.add(tag));
  return [...tags].slice(0, 6);
}

function tagClass(tag: string) {
  if (/совмещ/i.test(tag)) return "job-tag moonlight";
  if (/telegram|телеграм/i.test(tag)) return "job-tag telegram";
  if (/техника/i.test(tag)) return "job-tag equipment";
  return "job-tag";
}

function uniqueOpportunityJobs(jobs: Job[]) {
  const grouped = new Map<string, Job>();
  for (const job of jobs) {
    const key = (job.link || `${job.company}|${job.position}|${job.source}`).toLowerCase();
    const current = grouped.get(key);
    if (!current) {
      grouped.set(key, { ...job });
      continue;
    }
    const candidates = new Set(
      [current.candidate, job.candidate]
        .flatMap((value) => String(value || "").split(/\s+\+\s+/))
        .filter(Boolean),
    );
    grouped.set(key, {
      ...current,
      score: Math.max(Number(current.score || 0), Number(job.score || 0)),
      candidate: [...candidates].join(" + "),
      favorite: Number(current.favorite || 0) || Number(job.favorite || 0),
      moonlight_compatible: Number(current.moonlight_compatible || 0) || Number(job.moonlight_compatible || 0),
      moonlight_reason: current.moonlight_reason || job.moonlight_reason,
    });
  }
  return [...grouped.values()].sort((a, b) => Number(b.score || 0) - Number(a.score || 0));
}

function JobCard({
  job,
  onStatus,
  inWork = false,
}: {
  job: Job;
  onStatus: (id: number, status?: string, favorite?: boolean) => Promise<void>;
  inWork?: boolean;
}) {
  const [busy, setBusy] = useState("");
  const [previewOpen, setPreviewOpen] = useState(false);
  const [preview, setPreview] = useState<Awaited<ReturnType<typeof api.jobPreview>> | null>(null);
  const [letterOpen, setLetterOpen] = useState(false);
  const [letterTone, setLetterTone] = useState<"formal" | "friendly">("formal");
  // Compensation guidance belongs in the private review, not in a first
  // contact.  A person can opt in for a particular vacancy when appropriate.
  const [includeSalary, setIncludeSalary] = useState(false);
  const [letter, setLetter] = useState<Awaited<ReturnType<typeof api.composeApplication>> | null>(null);
  const [letterMessage, setLetterMessage] = useState("");
  const golden = job.score >= MAIN_MATCH_SCORE;
  const links = job.links?.length
    ? job.links
    : (job.link ? [{ url: job.link, source: job.source || "Источник" }] : []);

  async function act(next?: string, favorite?: boolean) {
    setBusy(next || "favorite");
    try {
      await onStatus(job.id, next, favorite);
    } finally {
      setBusy("");
    }
  }
  async function openPreview() {
    setBusy("preview");
    try { const result = await api.jobPreview(job.id); setPreview(result); setPreviewOpen(true); }
    finally { setBusy(""); }
  }
  async function composeLetter() {
    setBusy("letter");
    setLetterMessage("");
    try {
      setLetter(await api.composeApplication(job.id, { tone: letterTone, include_salary: includeSalary }));
      setLetterOpen(true);
    } catch (error) {
      setLetterMessage(error instanceof Error ? error.message : "Не удалось подготовить сопроводительное письмо.");
    } finally { setBusy(""); }
  }
  async function copyLetter() {
    if (!letter) return;
    try { await navigator.clipboard.writeText(letter.cover_letter); setLetterMessage("Письмо скопировано. Проверьте его перед отправкой."); }
    catch { setLetterMessage("Не удалось скопировать автоматически — выделите текст вручную."); }
  }

  return (
    <article className={`job-card ${golden ? "golden" : ""}`}>
      <div className="job-card-head">
        <div className={`score-ring ${golden ? "golden" : ""}`} aria-label={`Совпадение ${job.score}%`}>
          <strong>{job.score}</strong>
          <span>%</span>
        </div>
        <div className="job-title">
          <div className="chip-row">
            <span className={`chip ${golden ? "success" : "info"}`}>
              {golden ? "60%+ совпадение" : "Проверить"}
            </span>
            {job.hot && <span className="chip warning">{job.priority_label || "Топ-приоритет"}</span>}
            {job.moonlight_compatible ? <span className="chip moonlight">Можно совмещать</span> : null}
            {job.contacts?.telegram?.length ? <span className="chip telegram">Telegram</span> : null}
            {job.equipment?.length ? <span className="chip equipment">Техника</span> : null}
            {job.relocation_priority ? <span className="chip success">Релокация / виза</span> : null}
            {Number(job.is_active ?? 1) === 0 && <span className="chip danger">Неактивна</span>}
            <span className="chip neutral">{job.candidate}</span>
          </div>
          <h3>{job.position || "Вакансия без названия"}</h3>
          <p>{job.company || "Компания не указана"} · {job.source || "Источник"}</p>
        </div>
      </div>

      <div className="job-facts">
        <span><small>Формат</small>{job.remote_location || "Географию проверить"}</span>
        <span><small>Вилка</small>{job.salary_text || "Не указана"}</span>
        <span><small>Рейтинг</small>{job.company_rating_verified ? `${job.company_rating}/100` : "Не проверен"}</span>
        <span><small>Дата</small>{jobFreshness(job)}</span>
      </div>
      <div className="job-tags" aria-label="Теги вакансии">
        {jobTags(job).map((tag) => <span className={tagClass(tag)} key={tag}>{tag}</span>)}
      </div>
      <div className="salary-guide">
        <strong>Ориентир по доходу</strong>
        <p>{job.final_salary_advice || (job.salary_text && !/не указана/i.test(job.salary_text)
          ? `В вакансии указано: ${job.salary_text}. Перед откликом уточните валюту и gross/net.`
          : "Вилка не указана. Перед откликом уточните валюту, gross/net, формат оформления и ожидаемый диапазон.")}</p>
      </div>
      <div className="job-benefits">
        <strong>Плюсы и условия компании</strong>
        {(job.benefits || job.preview?.benefits)?.length ? <div className="job-tags">{(job.benefits || job.preview?.benefits || []).map((benefit) => <span className="job-tag benefit" key={benefit}>{benefit}</span>)}</div> : <p>Льготы, релокация и компенсация переезда не указаны — уточните до отклика.</p>}
      </div>
      <div className="job-condition-grid">
        <div><strong>Техника</strong><p>{job.equipment?.length ? `Да: ${job.equipment.join("; ")}` : "Не указана в вакансии"}</p></div>
        <div><strong>График</strong><p>{job.schedule || "Не указан — уточните нормированный ли рабочий день"}</p></div>
      </div>
      {job.moonlight_compatible ? <div className="job-reason"><strong>Почему можно совмещать</strong><p>{job.moonlight_reason || "В вакансии прямо указан совместимый формат."}</p></div> : null}
      {!job.company_rating_verified && <p className="rating-missing">{job.company_rating_note || "Рейтинг компании не проверен — проверьте отзывы, юрлицо и условия самостоятельно."}</p>}
      {job.sector && <div className={`job-risk ${/букмек|gambl|betting/i.test(job.sector) ? "warning" : ""}`}><strong>Профиль компании</strong><p>{job.sector}{job.risk ? ` · ${job.risk}` : ""}</p></div>}

      {links.length > 0 && (
        <div className="source-links" aria-label="Ссылки на вакансию">
          <strong>{links.length > 1 ? `Найдена в ${links.length} источниках` : "Оригинал вакансии"}</strong>
          <div>
            {links.map((item, index) => (
              <a href={item.url} key={`${item.url}-${index}`} rel="noreferrer" target="_blank">
                <span>{item.source || `Источник ${index + 1}`}</span>
                <Icon name="external" size={16} />
              </a>
            ))}
          </div>
        </div>
      )}

      {(job.strengths || job.positioning) && (
        <div className="job-reason">
          <strong>Оценка совпадения · правила профиля</strong>
          <p>{job.strengths || job.positioning}</p>
        </div>
      )}
      <div className="settings-actions"><Button className="secondary" disabled={Boolean(busy)} onClick={() => void openPreview()} type="button">{busy === "preview" ? "Загружаем…" : "Предпросмотр вакансии"}</Button></div>
      {previewOpen && <details className="job-preview" open><summary>Структурированное превью вакансии</summary>
        <div className="preview-grid">
          <div><strong>Контакты в объявлении</strong>{preview?.contacts?.emails?.length ? <p><b>Email работодателя:</b> {preview.contacts.emails.map((email) => <a key={email} href={`mailto:${email}`}>{email}</a>)}</p> : null}{preview?.contacts?.phones?.length ? <p><b>Телефон:</b> {preview.contacts.phones.map((phone) => <a key={phone} href={`tel:${phone}`}>{phone}</a>)}</p> : null}{preview?.contacts?.telegram?.length ? <p><b>Telegram:</b> {preview.contacts.telegram.join(", ")}</p> : null}{!preview?.contacts?.emails?.length && !preview?.contacts?.phones?.length && !preview?.contacts?.telegram?.length ? <p>Публичные контакты не указаны.</p> : null}</div>
          <div><strong>Условия</strong><p>Техника: {preview?.equipment?.length ? preview.equipment.join("; ") : "не указана"}</p><p>График: {preview?.schedule || "не указан"}</p><p>Сектор: {preview?.sector || "не определён"}</p></div>
        </div>
        <strong>Оригинальный текст</strong><p>{preview?.preview?.description || "Источник не передал полный текст. Используйте ссылки выше и отметку проверки."}</p>{preview?.preview?.tags?.length ? <div className="chip-row">{preview.preview.tags.map((tag) => <span className="chip neutral" key={tag}>{tag}</span>)}</div> : null}</details>}
      <details className="cover-preview" open={letterOpen}>
        <summary>Сопроводительное письмо · ручная проверка</summary>
        <p>Быстрая настройка действует только на это письмо и не меняет профиль кандидата. Отправка всегда остаётся за вами.</p>
        <div className="profile-form-grid compact-grid">
          <label><span>Стиль для этой вакансии</span><select value={letterTone} onChange={(e) => setLetterTone(e.target.value as "formal" | "friendly")}><option value="formal">Деловой</option><option value="friendly">Дружелюбный</option></select></label>
          <label className="check-row"><input checked={includeSalary} onChange={(e) => setIncludeSalary(e.target.checked)} type="checkbox"/><span>Добавить ориентир по зарплате</span></label>
        </div>
        <div className="settings-actions"><Button className="secondary" disabled={Boolean(busy)} onClick={() => void composeLetter()} type="button">{busy === "letter" ? "Готовим…" : letter ? "Попробовать другой стиль" : "Подготовить письмо"}</Button></div>
        {letterMessage && <div className={letterMessage.includes("Не удалось") ? "inline-error" : "inline-success"}>{letterMessage}</div>}
        {letter && <div className="letter-inline-preview"><div className="preview-grid"><div><strong>Канал</strong><p>{letter.delivery_channel === "telegram" ? `Telegram: ${letter.contact_label || letter.recipient_contact}` : (letter.recipient_email || "Контакт не указан в объявлении")}</p></div><div><strong>Вложение</strong><p>{letter.delivery_channel === "telegram" ? "Не email: отправьте текст в чат" : (letter.resume?.title || "Резюме не найдено")}</p><small>{letter.resume_guidance || "Проверьте язык резюме перед отправкой."}</small></div></div><label><span>{letter.delivery_channel === "telegram" ? "Текст сообщения для Telegram" : "Текст письма"}</span><textarea aria-label="Предпросмотр сопроводительного письма" readOnly rows={12} value={letter.cover_letter}/></label><div className="settings-actions"><Button className="secondary" onClick={() => void copyLetter()} type="button">Скопировать</Button>{letter.delivery_channel === "telegram" && letter.recipient_contact ? <a className="button secondary" href={`https://t.me/${letter.recipient_contact.replace("@", "")}`} rel="noreferrer" target="_blank">Открыть Telegram</a> : letter.recipient_email ? <a className="button secondary" href={`mailto:${encodeURIComponent(letter.recipient_email)}?subject=${encodeURIComponent(letter.subject)}&body=${encodeURIComponent(letter.cover_letter)}`}>Открыть в почте</a> : <Button className="secondary" onClick={() => void openPreview()} type="button">Найти контакты в превью</Button>}<Button className="primary" disabled={Boolean(busy)} onClick={() => act("in_progress")} type="button">Взять в работу</Button></div></div>}
      </details>
      {job.weaknesses && (
        <details>
          <summary>Что проверить перед откликом</summary>
          <p>{job.weaknesses}</p>
        </details>
      )}

      <div className="job-actions">
        <Button className={job.favorite ? "secondary" : "ghost"} disabled={Boolean(busy)} onClick={() => act(undefined, !Boolean(job.favorite))}>{job.favorite ? "★ В избранном" : "☆ В избранное"}</Button>
        {!inWork && <Button className="secondary" disabled={Boolean(busy)} icon="clock" onClick={() => act("in_progress")}>{busy === "in_progress" ? "Сохраняем…" : "Взять в работу"}</Button>}
        {inWork && <Button className="primary" disabled={Boolean(busy)} icon="check" onClick={() => act("done")}>Готово</Button>}
        <Button
          className="primary"
          disabled={Boolean(busy) || job.status === "approved"}
          icon="check"
          onClick={() => act("approved")}
        >
          {job.status === "approved" ? "Одобрено" : busy === "approved" ? "Сохраняем…" : "Одобрить"}
        </Button>
        <Button className="secondary" disabled={Boolean(busy)} icon="clock" onClick={() => act("later")}>
          Позже
        </Button>
        <Button className="ghost" disabled={Boolean(busy)} onClick={() => act("skip")}>
          Скрыть
        </Button>
        <Button className="ghost" disabled={Boolean(busy) || job.status === "sent"} onClick={() => act("sent")}>
          {job.status === "sent" ? "Отмечено: отправлено" : "Я уже откликнулась"}
        </Button>
      </div>
    </article>
  );
}

function SpecialAttentionPage({
  data,
  loading,
  onSearch,
  onStatus,
}: {
  data: SpecialAttention | null;
  loading: boolean;
  onSearch: () => Promise<void>;
  onStatus: (id: number, status?: string, favorite?: boolean) => Promise<void>;
}) {
  const [source, setSource] = useState("all");
  const jobs = (data?.jobs || []).filter((job) => job.status !== "skip" && (source === "all" || job.source === source));
  return <div className="page-stack special-attention-page">
    <div className="section-heading page-title">
      <div>
        <span className="eyebrow">ТРИ РЕКОМЕНДОВАННЫХ ИСТОЧНИКА</span>
        <h1>Особое внимание</h1>
        <p>Только CareerSpace, SETTERS Media и Hirify. В выдаче остаются свежие международные удалённые роли без офиса и оформления в России.</p>
      </div>
      <Button className="primary" disabled={loading} icon="refresh" onClick={() => void onSearch()}>
        {loading ? "Проверяем…" : "Проверить 3 сервиса"}
      </Button>
    </div>
    <section className="special-source-grid" aria-label="Статус специальных источников">
      {(data?.sources || []).map((item) => <article className={`special-source ${item.status}`} key={item.name}>
        <div className="special-source-head"><strong>{item.name}</strong><span className={`status-dot ${item.status}`}/></div>
        <div className="special-source-metrics"><b>{item.matched}</b><span>подходящих</span></div>
        <small>Проверено: {item.checked}{item.target && item.checked < item.target ? ` из ${item.target}` : ""}</small>
        <p>{item.detail}</p>
        <a href={item.url} rel="noreferrer" target="_blank">Открыть источник <Icon name="external" size={15}/></a>
      </article>)}
    </section>
    {data?.summary ? <div className="inline-success">Проверено: {data.summary.checked}. Сохранено для проверки: {data.summary.saved}. Устаревших закрыто: {data.summary.archived}.</div> : null}
    <div className="special-toolbar">
      <label><span>Источник</span><select value={source} onChange={(event) => setSource(event.target.value)}><option value="all">Все три</option>{(data?.sources || []).map((item) => <option key={item.name} value={item.name}>{item.name}</option>)}</select></label>
      <span>{jobs.length} актуальных карточек</span>
    </div>
    {jobs.length ? <div className="jobs-grid">{jobs.map((job) => <JobCard job={job} key={`special-${job.id}`} onStatus={onStatus}/>)}</div> : <EmptyState title="Подходящих карточек пока нет" text="Это означает, что текущие объявления этих трёх площадок не прошли проверку географии, договора, свежести или соответствия профилю. Статусы источников выше показывают результат проверки."/>}
  </div>;
}

function GigCard({ gig, onStatus }: { gig: Gig; onStatus: (id: number, status?: string, favorite?: boolean) => Promise<void> }) {
  const [busy, setBusy] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [letterOpen, setLetterOpen] = useState(false);
  const [letterTone, setLetterTone] = useState<"formal" | "friendly">("formal");
  const [letter, setLetter] = useState<Awaited<ReturnType<typeof api.composeGigApplication>> | null>(null);
  const [letterMessage, setLetterMessage] = useState("");
  const links = gig.links?.length ? gig.links : [{ url: gig.link, source: gig.source }];
  const sectionLabel = gig.category === "Стажировка" ? "Стажировка" : "Подработка";
  async function act(status?: string, favorite?: boolean) {
    setBusy(true);
    try { await onStatus(gig.id, status, favorite); } finally { setBusy(false); }
  }
  async function composeLetter() {
    setBusy(true);
    setLetterMessage("");
    try {
      setLetter(await api.composeGigApplication(gig.id, { tone: letterTone }));
      setLetterOpen(true);
    } catch (error) {
      setLetterMessage(error instanceof Error ? error.message : "Не удалось подготовить сопроводительное письмо.");
    } finally { setBusy(false); }
  }
  async function copyLetter() {
    if (!letter) return;
    try {
      await navigator.clipboard.writeText(letter.cover_letter);
      setLetterMessage("Письмо скопировано. Проверьте его перед отправкой.");
    } catch {
      setLetterMessage("Не удалось скопировать автоматически — выделите текст вручную.");
    }
  }
  return <article className={`job-card gig-card ${gig.hot ? "golden" : ""}`}>
    <div className="job-card-head">
      <div className="score-ring"><strong>{gig.score}</strong><span>%</span></div>
      <div className="job-title"><div className="chip-row"><span className="chip info">{sectionLabel}</span>{gig.hot && <span className="chip warning">Новое · до 3 дней</span>}<span className="chip neutral">{gig.candidate}</span></div><h3>{gig.title}</h3><p>{gig.client || "Заказчик указан в объявлении"} · {gig.source}</p></div>
    </div>
    <div className="job-facts"><span><small>Направление</small>{gig.category}</span><span><small>Формат</small>{gig.work_format}</span><span><small>Город</small>{gig.location || "Уточнить"}</span><span><small>Оплата</small>{gig.pay_text || "Не указана"}</span></div>
    <div className="job-reason"><strong>Почему в подборке</strong><p>Соответствует отдельному профилю подработок и допускает проектный, гибкий или частичный формат. Перед согласием сверяйте объём и сроки.</p></div>
    <details className="job-preview" open={expanded} onToggle={(event) => setExpanded((event.target as HTMLDetailsElement).open)}><summary>Условия, контакты и проверка</summary><div className="preview-grid"><div><strong>Публичные контакты</strong>{gig.contacts?.emails?.length ? <p><b>Email:</b> {gig.contacts.emails.map((email) => <a key={email} href={`mailto:${email}`}>{email}</a>)}</p> : null}{gig.contacts?.phones?.length ? <p><b>Телефон:</b> {gig.contacts.phones.join(", ")}</p> : null}{gig.contacts?.telegram?.length ? <p><b>Telegram:</b> {gig.contacts.telegram.join(", ")}</p> : null}{!gig.contacts?.emails?.length && !gig.contacts?.phones?.length && !gig.contacts?.telegram?.length ? <p>Контакт в карточке не указан — откройте оригинал.</p> : null}</div><div><strong>Безопасность и оформление</strong><p>{gig.safety_note}</p><p>{gig.requirements_note}</p></div></div><strong>Описание объявления</strong><p>{gig.description}</p></details>
    <div className="source-links"><strong>{links.length > 1 ? `Ссылки: ${links.length}` : "Оригинал объявления"}</strong><div>{links.map((link, index) => <a href={link.url} key={`${link.url}-${index}`} rel="noreferrer" target="_blank"><span>{link.source || "Источник"}</span><Icon name="external" size={16}/></a>)}</div></div>
    <details className="cover-preview" open={letterOpen}>
      <summary>Сопроводительное письмо · ручная проверка</summary>
      <p>Для подработки письмо уточняет оплату, договор и возможность совмещения без эксклюзивности.</p>
      <div className="profile-form-grid compact-grid">
        <label><span>Стиль письма</span><select value={letterTone} onChange={(event) => setLetterTone(event.target.value as "formal" | "friendly")}><option value="formal">Деловой</option><option value="friendly">Дружелюбный</option></select></label>
      </div>
      <div className="settings-actions"><Button className="secondary" disabled={busy} onClick={() => void composeLetter()} type="button">{busy ? "Готовим…" : letter ? "Попробовать другой стиль" : "Подготовить письмо"}</Button></div>
      {letterMessage && <div className={letterMessage.includes("Не удалось") ? "inline-error" : "inline-success"}>{letterMessage}</div>}
      {letter && <div className="letter-inline-preview">
        <div className="preview-grid"><div><strong>Канал</strong><p>{letter.delivery_channel === "telegram" ? `Telegram: ${letter.contact_label || letter.recipient_contact}` : (letter.recipient_email || "Контакт не указан в объявлении")}</p></div><div><strong>Вложение</strong><p>{letter.resume?.title || "Резюме не найдено"}</p><small>{letter.resume_guidance}</small></div></div>
        <label><span>Текст отклика</span><textarea aria-label="Сопроводительное письмо для подработки" readOnly rows={12} value={letter.cover_letter}/></label>
        <div className="settings-actions"><Button className="secondary" onClick={() => void copyLetter()} type="button">Скопировать</Button>{letter.delivery_channel === "telegram" && letter.recipient_contact ? <a className="button secondary" href={`https://t.me/${letter.recipient_contact.replace("@", "")}`} rel="noreferrer" target="_blank">Открыть Telegram</a> : letter.recipient_email ? <a className="button secondary" href={`mailto:${encodeURIComponent(letter.recipient_email)}?subject=${encodeURIComponent(letter.subject)}&body=${encodeURIComponent(letter.cover_letter)}`}>Открыть в почте</a> : null}</div>
      </div>}
    </details>
    <div className="job-actions"><Button className={gig.favorite ? "secondary" : "ghost"} disabled={busy} onClick={() => void act(undefined, !Boolean(gig.favorite))}>{gig.favorite ? "★ Сохранено" : "☆ Сохранить"}</Button><Button className="secondary" disabled={busy || gig.status === "in_progress"} icon="clock" onClick={() => void act("in_progress")}>{gig.status === "in_progress" ? "В работе" : "Взять в работу"}</Button><Button className="secondary" disabled={busy} onClick={() => void act("later")}>Позже</Button><Button className="ghost" disabled={busy} onClick={() => void act("skip")}>Скрыть</Button></div>
  </article>;
}

function GigsPage({ candidates, gigs, onChanged }: { candidates: Candidate[]; gigs: Gig[]; onChanged: () => Promise<void> }) {
  const [candidateId, setCandidateId] = useState(0);
  const [category, setCategory] = useState("");
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const filtered = useMemo(() => gigs.filter((gig) => gig.status !== "skip" && (!candidateId || gig.candidate_id === candidateId) && (!category || gig.category === category) && (!query || `${gig.title} ${gig.client} ${gig.location} ${gig.category}`.toLowerCase().includes(query.toLowerCase()))), [gigs, candidateId, category, query]);
  const categories = [...new Set(gigs.map((gig) => gig.category).filter(Boolean))];
  async function refresh() { setBusy(true); setMessage(""); try { const result = await api.collectGigs(); await onChanged(); setMessage(result.message); } catch (error) { setMessage(error instanceof Error ? error.message : "Не удалось проверить подработки."); } finally { setBusy(false); } }
  async function status(id: number, next?: string, favorite?: boolean) { await api.gigStatus(id, next, favorite); await onChanged(); }
  return <div className="page-stack"><div className="section-heading page-title"><div><span className="eyebrow">ПРОЕКТЫ И СОВМЕЩЕНИЕ</span><h1>Подработки</h1><p>Отдельно от основного поиска: только проекты, неполная занятость и гибкие варианты для Вьетнама или удалённо из Вьетнама.</p></div><Button className="primary" icon="refresh" disabled={busy} onClick={() => void refresh()}>{busy ? "Проверяем…" : "Найти подработки"}</Button></div><section className="job-filter-panel"><div><span className="eyebrow">ЛИЧНЫЕ НАПРАВЛЕНИЯ</span><h2>Подбор без смешивания профилей</h2><p>QA-кандидат: визуализация, ландшафт, тексты, краткие QA-задачи. Support-кандидат: junior IT/QA, поддержка и безопасные бытовые задачи.</p></div><div className="job-filter-controls"><span className="chip success">Найдено: {filtered.length}</span><label><span>Кому</span><select value={candidateId} onChange={(event) => setCandidateId(Number(event.target.value))}><option value={0}>Оба кандидата</option>{candidates.map((candidate) => <option key={candidate.id} value={candidate.id}>{candidate.name}</option>)}</select></label><label><span>Направление</span><select value={category} onChange={(event) => setCategory(event.target.value)}><option value="">Все направления</option>{categories.map((item) => <option value={item} key={item}>{item}</option>)}</select></label><label><span>Поиск</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Визуализация, QA, Дананг"/></label></div></section>{message && <div className={message.includes("Не удалось") ? "inline-error" : "inline-success"}>{message}</div>}<ManualGigForm candidates={candidates} onAdded={onChanged}/>{filtered.length ? <div className="jobs-grid">{filtered.map((gig) => <GigCard gig={gig} key={gig.id} onStatus={status}/>)}</div> : <EmptyState title="Подработок пока нет" text="Нажмите «Найти подработки». Будут использованы только публичные свежие источники; неподходящие senior-вакансии не попадут в этот раздел." action={<Button className="primary" disabled={busy} onClick={() => void refresh()}>Проверить предложения</Button>}/>}</div>;
}

function InternshipsPage({ candidates, internships, onChanged }: { candidates: Candidate[]; internships: Gig[]; onChanged: () => Promise<void> }) {
  const [candidateId, setCandidateId] = useState(0);
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [messageError, setMessageError] = useState(false);
  const filtered = useMemo(() => internships.filter((item) => item.status !== "skip" && (!candidateId || item.candidate_id === candidateId) && (!query || `${item.title} ${item.client} ${item.location} ${item.pay_text}`.toLowerCase().includes(query.toLowerCase()))), [internships, candidateId, query]);
  async function refresh() { setBusy(true); setMessage(""); setMessageError(false); try { const result = await api.collectInternships(); await onChanged(); setMessage(result.message); } catch (error) { setMessageError(true); setMessage(error instanceof Error ? error.message : "Не удалось проверить стажировки."); } finally { setBusy(false); } }
  async function status(id: number, next?: string, favorite?: boolean) { await api.gigStatus(id, next, favorite); await onChanged(); }
  return <div className="page-stack">
    <div className="section-heading page-title">
      <div><span className="eyebrow">СТАРТ БЕЗ ОПЫТА</span><h1>Стажировка</h1><p>Удалённые trainee/junior-варианты, куда можно откликаться без опыта: ищем обучение, наставника, понятные условия и технику от работодателя.</p></div>
      <Button className="primary" icon="refresh" disabled={busy} onClick={() => void refresh()}>{busy ? "Проверяем…" : "Найти стажировку"}</Button>
    </div>
    <section className="job-filter-panel">
      <div><span className="eyebrow">ЖЁСТКИЕ УСЛОВИЯ</span><h2>Только проверяемые объявления</h2><p>Нужны русскоязычная коммуникация, удалёнка, конкретная ссылка и свежий источник. Если оплаты нет, сохраняем только явный старт без опыта с обучением или техникой.</p></div>
      <div className="job-filter-controls">
        <span className="chip success">Найдено: {filtered.length}</span>
        <span className="chip moonlight">Remote/worldwide</span>
        <span className="chip equipment">Без опыта / техника</span>
        <label><span>Кому</span><select value={candidateId} onChange={(event) => setCandidateId(Number(event.target.value))}><option value={0}>Оба кандидата</option>{candidates.map((candidate) => <option key={candidate.id} value={candidate.id}>{candidate.name}</option>)}</select></label>
        <label><span>Поиск</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="QA, support, Java, Swift"/></label>
      </div>
    </section>
    {message && <div className={messageError ? "inline-error" : "inline-success"} role={messageError ? "alert" : "status"}>{message}</div>}
    {filtered.length ? <div className="jobs-grid">{filtered.map((item) => <GigCard gig={item} key={item.id} onStatus={status}/>)}</div> : <EmptyState title="Стажировок пока нет" text="Запустите поиск. Сервис покажет русскоязычные удалённые trainee-варианты с проверяемым источником, обучением, техникой или понятной оплатой." action={<Button className="primary" disabled={busy} onClick={() => void refresh()}>Проверить стажировку</Button>}/>}
  </div>;
}

type AiChatMessage = {
  role: "assistant" | "user";
  text: string;
  meta?: string;
};

function AiChatPage({ health, dashboard }: { health: ServiceHealth; dashboard: Dashboard | null }) {
  const localNotice = "Онлайн-поиск активен. Генеративные модели не подключены, чат ищет по публичным источникам CareerMove.";
  const providerText = health.ai?.providers?.length
    ? health.ai.providers.map((item) => `${item.provider} · ${item.model}`).join(", ")
    : "CareerMove Search · public-sources";
  const [messages, setMessages] = useState<AiChatMessage[]>([
    {
      role: "assistant",
      text: "Я CareerMove AI. Можно спросить про стратегию поиска, резюме, отклик, источники вакансий или конкретную карточку.",
      meta: health.ai?.title || localNotice,
    },
  ]);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: FormEvent) {
    event.preventDefault();
    const text = message.trim();
    if (!text || busy) return;
    setMessages((items) => [...items, { role: "user", text }]);
    setMessage("");
    setBusy(true);
    setError("");
    try {
      const response = await api.aiChat(text);
      const meta = response.provider
        ? `${response.provider}${response.model ? ` · ${response.model}` : ""}`
        : "CareerMove Search · public-sources";
      setMessages((items) => [...items, { role: "assistant", text: response.answer, meta }]);
    } catch (caught) {
      const fallback = caught instanceof Error ? caught.message : "AI-чат временно недоступен.";
      setError(fallback);
      setMessages((items) => [...items, { role: "assistant", text: localNotice, meta: "Fallback" }]);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="page-stack">
      <div className="section-heading page-title ai-chat-title">
        <div>
          <span className="eyebrow">ALICE AI · CAREERMOVE ASSISTANT</span>
          <h1>AI-чат для вопросов по поиску</h1>
          <p>{health.ai?.detail || localNotice}</p>
        </div>
        <span className={`chip ${health.ai?.level === "ok" ? "success" : "info"}`}>{providerText}</span>
      </div>
      <section className="settings-card ai-chat-panel">
        <div className="ai-chat-metrics">
          <span>Вакансий: {dashboard?.metrics.found || 0}</span>
          <span>Золотых: {dashboard?.metrics.golden || 0}</span>
          <span>Стажировка: {dashboard?.metrics.internships || 0}</span>
          <span>Совмещение: {dashboard?.metrics.combine || 0}</span>
        </div>
        <div className="ai-chat-log" aria-live="polite">
          {messages.map((item, index) => (
            <article className={`ai-chat-message ${item.role}`} key={`${item.role}-${index}`}>
              <strong>{item.role === "assistant" ? "CareerMove AI" : "Вы"}</strong>
              <p>{item.text}</p>
              {item.meta ? <small>{item.meta}</small> : null}
            </article>
          ))}
        </div>
        {error && <div className="inline-error small">{error}</div>}
        <form className="ai-chat-form" onSubmit={submit}>
          <textarea
            aria-label="Вопрос AI-чату"
            disabled={busy}
            onChange={(event) => setMessage(event.target.value)}
            placeholder="Например: какие вакансии открыть первыми для Junior QA remote?"
            rows={3}
            value={message}
          />
          <Button className="primary" disabled={busy || !message.trim()} icon="send" type="submit">
            {busy ? "Думаю…" : "Спросить"}
          </Button>
        </form>
      </section>
    </div>
  );
}

function EducationPage({ items = [] }: { items?: NonNullable<Dashboard["education_recommendations"]> }) {
  const groups = useMemo(() => {
    const next = new Map<string, NonNullable<Dashboard["education_recommendations"]>>();
    for (const item of items) next.set(item.track, [...(next.get(item.track) || []), item]);
    return [...next.entries()];
  }, [items]);
  return <div className="page-stack">
    <div className="section-heading page-title">
      <div>
        <span className="eyebrow">ОБРАЗОВАНИЕ И СЕРТИФИКАЦИЯ</span>
        <h1>План входа в IT</h1>
        <p>Маршруты подобраны под переезд во Вьетнам, удалёнку по миру, A1 English на старте и риск, что дипломы придётся подтверждать заново.</p>
      </div>
    </div>
    <section className="job-filter-panel">
      <div>
        <span className="eyebrow">ПРИОРИТЕТ</span>
        <h2>Сначала база, потом специализация</h2>
        <p>Для QA-кандидата: QA → ISTQB → Java/Swift как расширение. Для Support-кандидата: IT Support → help desk практика → Java/Swift или Windows/macOS development после основ.</p>
      </div>
      <div className="job-filter-controls">
        <span className="chip success">Документы: апостиль и перевод</span>
        <span className="chip info">Английский: сначала A2-B1</span>
        <span className="chip neutral">Фокус: junior/trainee</span>
      </div>
    </section>
    {groups.map(([track, cards]) => <section className="settings-card" key={track}>
      <div className="section-heading compact"><div><span className="eyebrow">{track}</span><h2>{track}</h2></div></div>
      <div className="jobs-grid">
        {cards.map((item) => <article className="job-card" key={`${item.track}-${item.title}`}>
          <div className="chip-row"><span className="chip info">{item.priority}</span><span className="chip neutral">{item.format}</span></div>
          <h3>{item.title}</h3>
          <div className="job-reason"><strong>Зачем</strong><p>{item.fit}</p></div>
          <div className="job-reason"><strong>Поступление / вход</strong><p>{item.eligibility}</p></div>
          <div className="source-links"><strong>Источник</strong><div><a href={item.url} rel="noreferrer" target="_blank"><span>Открыть программу</span><Icon name="external" size={16}/></a></div></div>
        </article>)}
      </div>
    </section>)}
  </div>;
}

function ChanceMeter({ name, chance }: { name: string; chance: HigherEducationOption["qa_candidate"] }) {
  const tone = chance.score >= 70 ? "strong" : chance.score >= 45 ? "possible" : "stretch";
  return <div className={`education-chance ${tone}`}>
    <div><strong>{name}</strong><span>{chance.label}</span></div>
    <div className="chance-track" aria-label={`${name}: ориентир ${chance.score}%`}><i style={{ width: `${chance.score}%` }} /></div>
    <p>{chance.note}</p>
  </div>;
}

function HigherEducationPage({
  options = [],
  guide = [],
  applicantResources = [],
  relocationResources = [],
}: {
  options?: NonNullable<Dashboard["higher_education_options"]>;
  guide?: NonNullable<Dashboard["education_application_guide"]>;
  applicantResources?: NonNullable<Dashboard["applicant_resources"]>;
  relocationResources?: NonNullable<Dashboard["relocation_resources"]>;
}) {
  const [kind, setKind] = useState<"all" | "university" | "college">("all");
  const [budgetOnly, setBudgetOnly] = useState(false);
  const visible = useMemo(
    () => [...options]
      .filter((item) => (kind === "all" || item.kind === kind) && (!budgetOnly || item.budget_score >= 55))
      .sort((a, b) => a.rank - b.rank || b.ease_score - a.ease_score),
    [options, kind, budgetOnly],
  );

  return <div className="page-stack higher-education-page">
    <div className="section-heading page-title">
      <div>
        <span className="eyebrow">МЕЖДУНАРОДНЫЙ ДИПЛОМ · ВЬЕТНАМ И ОНЛАЙН</span>
        <h1>Высшее обучение</h1>
        <p>Варианты для QA-кандидата и Support-кандидата с English A1, ограниченным бюджетом и планом жить в Дананге. Список отсортирован по реальности поступления, а не по рекламе учебных заведений.</p>
      </div>
    </div>

    <section className="education-reality">
      <Icon name="book" size={22} />
      <div>
        <strong>Важная граница поиска</strong>
        <p>Признанного международного IT-диплома одновременно на русском, бесплатно и дистанционно сейчас нет. Реальный маршрут: подготовить английский до A2-B1 и параллельно отправить запросы в доступные онлайн-программы и кампусы Дананга. Проценты ниже — ориентир по вашему текущему профилю, не гарантия приёмной комиссии.</p>
      </div>
    </section>

    <section className="education-controls" aria-label="Фильтры учебных заведений">
      <div className="segmented education-segmented">
        {([['all', 'Все'], ['university', 'Университеты'], ['college', 'Колледжи']] as const).map(([value, label]) => (
          <label className={kind === value ? "selected" : ""} key={value}>
            <input checked={kind === value} name="education-kind" onChange={() => setKind(value)} type="radio" />
            <span>{label}</span>
          </label>
        ))}
      </div>
      <label className="check-row education-budget-filter">
        <input checked={budgetOnly} onChange={(event) => setBudgetOnly(event.target.checked)} type="checkbox" />
        <span>Только варианты с заметным шансом полной поддержки</span>
      </label>
      <span className="chip info">Показано: {visible.length}</span>
    </section>

    <div className="education-options">
      {visible.map((item) => <article className="education-option" key={`${item.institution}-${item.program}`}>
        <div className="education-option-head">
          <span className="education-rank">{item.rank}</span>
          <div>
            <div className="chip-row">
              <span className={`chip ${item.kind === "university" ? "info" : "success"}`}>{item.kind === "university" ? "Университет" : "Колледж / диплом"}</span>
              <span className="chip neutral">Лёгкость входа {item.ease_score}/100</span>
              <span className="chip neutral">{item.mode}</span>
            </div>
            <h2>{item.program}</h2>
            <p>{item.institution} · {item.location}</p>
          </div>
          <a className="icon-link" href={item.url} rel="noreferrer" target="_blank" title="Официальная программа"><Icon name="external" size={18}/></a>
        </div>

        <dl className="education-facts">
          <div><dt>Диплом</dt><dd>{item.credential}</dd></div>
          <div><dt>Язык</dt><dd>{item.language}</dd></div>
          <div><dt>Стоимость</dt><dd>{item.cost}</dd></div>
          <div><dt>Стипендия</dt><dd>{item.funding}</dd></div>
        </dl>

        <div className="education-recognition"><strong>Признание</strong><p>{item.recognition}</p></div>
        <div className="education-chances">
          <ChanceMeter chance={item.qa_candidate} name="QA-кандидат" />
          <ChanceMeter chance={item.support_candidate} name="Support-кандидат" />
        </div>

        <details className="education-details">
          <summary>Подача, требования и что подготовить</summary>
          <dl>
            <div><dt>Поступление</dt><dd>{item.admission}</dd></div>
            <div><dt>Математика</dt><dd>{item.math}</dd></div>
            <div><dt>Срок</dt><dd>{item.deadline}</dd></div>
            <div><dt>Контакт</dt><dd>{item.contact}</dd></div>
          </dl>
          <strong>Подтянуть за 2-3 месяца</strong>
          <ul>{item.preparation.map((step) => <li key={step}>{step}</li>)}</ul>
          <p className="education-caveat"><strong>Ограничение:</strong> {item.caveat}</p>
          <div className="education-links">
            <a href={item.apply_url} rel="noreferrer" target="_blank">Подать заявку <Icon name="external" size={14}/></a>
            {item.scholarship_url && <a href={item.scholarship_url} rel="noreferrer" target="_blank">Стипендии <Icon name="external" size={14}/></a>}
            {item.community_url && <a href={item.community_url} rel="noreferrer" target="_blank">Абитуриенты / сообщество <Icon name="external" size={14}/></a>}
          </div>
        </details>
      </article>)}
    </div>

    {!visible.length && <EmptyState title="Нет вариантов по этому фильтру" text="Верните режим «Все»: часть самых доступных программ не обещает полное финансирование, но допускает рассрочку или отдельный запрос на помощь." />}

    <section className="education-application-band">
      <div className="section-heading compact"><div><span className="eyebrow">ПАКЕТНАЯ ПОДАЧА</span><h2>Как отправить запросы сразу вдвоём</h2><p>Для каждого учебного заведения создавайте две отдельные заявки и два отдельных письма. Финансовую помощь просите одновременно с рассмотрением поступления.</p></div></div>
      <ol className="application-steps">{guide.map((item) => <li key={item.step}><b>{item.step}</b><div><strong>{item.title}</strong><p>{item.detail}</p></div></li>)}</ol>
      <div className="education-email-template">
        <strong>Тема письма</strong>
        <p>International applicant from Russia in Vietnam · IT programme · full scholarship / fee waiver enquiry</p>
        <strong>Что спросить одним письмом</strong>
        <p>Принимают ли российский аттестат; нужна ли легализация; можно ли начать с A1 через English pathway; доступна ли 100% scholarship или fee waiver; распространяется ли помощь на обоих независимых заявителей; кто выдаёт диплом и можно ли проверить его аккредитацию.</p>
      </div>
    </section>

    <section className="education-community-band">
      <div><span className="eyebrow">АБИТУРИЕНТЫ</span><h2>Где задавать вопросы до подачи</h2></div>
      <div className="education-resource-list">{applicantResources.map((item) => <a href={item.url} key={item.url} rel="noreferrer" target="_blank"><span><strong>{item.title}</strong><small>{item.kind} · {item.detail}</small></span><Icon name="external" size={16}/></a>)}</div>
    </section>

    <section className="education-community-band relocation">
      <div><span className="eyebrow">ДАНАНГ И ПЕРЕЕЗД</span><h2>Чаты релокантов</h2><p>Для сообщения «летим из Краснодарского края такого-то числа, кто тоже летит?» лучше подходят городские чаты и встречи, а не каналы с односторонними публикациями.</p></div>
      <div className="education-resource-list">{relocationResources.map((item) => <a href={item.url} key={item.url} rel="noreferrer" target="_blank"><span><strong>{item.title}</strong><small>{item.detail}{item.safety ? ` · ${item.safety}` : ""}</small></span><Icon name="external" size={16}/></a>)}</div>
    </section>
  </div>;
}

function ManualGigForm({ candidates, onAdded }: { candidates: Candidate[]; onAdded: () => Promise<void> }) {
  const [candidateId, setCandidateId] = useState(candidates[0]?.id || 0);
  const [form, setForm] = useState({ title: "", client: "", link: "", category: "Проектная работа", work_format: "Проект / гибкий график", location: "Дананг / удалённо из Вьетнама", pay_text: "", posted_at: new Date().toISOString().slice(0, 10), description: "" });
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  async function save(event: FormEvent) {
    event.preventDefault();
    setBusy(true); setMessage("");
    try {
      const result = await api.addManualGig({ candidate_id: candidateId, ...form });
      await onAdded();
      setForm((current) => ({ ...current, title: "", client: "", link: "", pay_text: "", description: "" }));
      setMessage(`Подработка добавлена и прошла первичную проверку: ${result.score}% совпадения.`);
    } catch (error) { setMessage(error instanceof Error ? error.message : "Не удалось добавить подработку."); }
    finally { setBusy(false); }
  }
  if (!candidates.length) return null;
  return <details className="settings-card"><summary>Добавить подработку вручную</summary><p>Добавляйте только свежие публичные объявления с исходной ссылкой. Сервис проверит дату, уровень роли и базовую совместимость с профилем; договор, налоги и право работать во Вьетнаме нужно подтвердить до старта.</p><form className="profile-form-grid" onSubmit={save}><label><span>Кандидат</span><select value={candidateId} onChange={(event) => setCandidateId(Number(event.target.value))}>{candidates.map((candidate) => <option key={candidate.id} value={candidate.id}>{candidate.name}</option>)}</select></label><label><span>Заказчик</span><input required value={form.client} onChange={(event) => setForm({ ...form, client: event.target.value })} placeholder="Компания или имя заказчика"/></label><label><span>Задача / роль</span><input required value={form.title} onChange={(event) => setForm({ ...form, title: event.target.value })} placeholder="3D-визуализация двора, краткий QA…"/></label><label><span>Категория</span><input required value={form.category} onChange={(event) => setForm({ ...form, category: event.target.value })} placeholder="Визуализация, QA, IT support…"/></label><label><span>Формат</span><input value={form.work_format} onChange={(event) => setForm({ ...form, work_format: event.target.value })} placeholder="Проект, гибкий график, part-time"/></label><label><span>Город / доступность</span><input value={form.location} onChange={(event) => setForm({ ...form, location: event.target.value })} placeholder="Дананг / удалённо из Вьетнама"/></label><label><span>Оплата</span><input value={form.pay_text} onChange={(event) => setForm({ ...form, pay_text: event.target.value })} placeholder="Например: $150 за проект"/></label><label><span>Дата публикации</span><input required type="date" value={form.posted_at} onChange={(event) => setForm({ ...form, posted_at: event.target.value })}/></label><label className="wide"><span>Оригинал объявления</span><input required type="url" value={form.link} onChange={(event) => setForm({ ...form, link: event.target.value })} placeholder="https://…"/></label><label className="wide"><span>Текст / публичные контакты <small>(желательно)</small></span><textarea rows={3} value={form.description} onChange={(event) => setForm({ ...form, description: event.target.value })} placeholder="Вставьте описание: сервис выделит публичный e-mail, телефон и Telegram, если они есть."/></label><div className="settings-actions wide"><Button className="primary" disabled={busy} type="submit">{busy ? "Проверяем…" : "Добавить и проверить"}</Button></div></form>{message && <div className={message.includes("добавлена") ? "inline-success" : "inline-error"}>{message}</div>}</details>;
}

function CandidateCard({ candidate, onEdit }: { candidate: Candidate; onEdit: () => void }) {
  const ready = candidate.resume_count > 0;
  return (
    <article className="candidate-card">
      <div className="candidate-avatar">{candidate.name?.slice(0, 1).toUpperCase() || "?"}</div>
      <div className="candidate-main">
        <div className="candidate-title">
          <div>
            <h3>{candidate.name}</h3>
            <p>{candidate.target_title || "Целевая роль не указана"}</p>
          </div>
          <span className={`chip ${ready ? "success" : "warning"}`}>{ready ? "Готов" : "Нужно резюме"}</span>
        </div>
        <dl className="candidate-facts">
          <div><dt>Английский</dt><dd>{candidate.english_level || "—"}</dd></div>
          <div><dt>Минимум</dt><dd>${candidate.salary_min || 0}</dd></div>
          <div><dt>Резюме</dt><dd>{candidate.resume_count}</dd></div>
        </dl>
        <p className="candidate-geo">{candidate.desired_countries || "География не указана"}</p>
        <Button className="ghost candidate-edit" onClick={onEdit} type="button">Изменить настройки кандидата</Button>
      </div>
    </article>
  );
}

function ManualVacancyForm({ candidates, onAdded }: { candidates: Candidate[]; onAdded: () => Promise<void> }) {
  const [candidateId, setCandidateId] = useState(candidates[0]?.id || 0);
  const [form, setForm] = useState({ company: "", position: "", link: "", location: "", source: "Добавлено вручную", posted_at: new Date().toISOString().slice(0, 10), salary_text: "", description: "" });
  const [busy, setBusy] = useState(false); const [message, setMessage] = useState("");
  async function save(event: FormEvent) { event.preventDefault(); setBusy(true); setMessage(""); try { const result = await api.addManualVacancy({ candidate_id: candidateId, ...form }); await onAdded(); setForm({ ...form, company: "", position: "", link: "", description: "" }); setMessage(`Вакансия добавлена и оценена: ${result.score}% совпадения.`); } catch (e) { setMessage(e instanceof Error ? e.message : "Не удалось добавить вакансию."); } finally { setBusy(false); } }
  if (!candidates.length) return null;
  return <details className="settings-card"><summary>Добавить вакансию вручную</summary><p>Используйте для Telegram, соцсетей и личных рекомендаций. Ссылка и свежая дата обязательны: архивные объявления не попадут в подборку.</p><form className="profile-form-grid" onSubmit={save}><label><span>Кандидат</span><select value={candidateId} onChange={(e) => setCandidateId(Number(e.target.value))}>{candidates.map((candidate) => <option value={candidate.id} key={candidate.id}>{candidate.name}</option>)}</select></label><label><span>Компания</span><input required value={form.company} onChange={(e) => setForm({ ...form, company: e.target.value })}/></label><label><span>Позиция</span><input required value={form.position} onChange={(e) => setForm({ ...form, position: e.target.value })}/></label><label><span>Город / формат</span><input value={form.location} onChange={(e) => setForm({ ...form, location: e.target.value })} placeholder="Дананг / Вьетнам / удалённо"/></label><label className="wide"><span>Ссылка на вакансию</span><input required type="url" value={form.link} onChange={(e) => setForm({ ...form, link: e.target.value })} placeholder="https://…"/></label><label><span>Источник</span><input value={form.source} onChange={(e) => setForm({ ...form, source: e.target.value })}/></label><label><span>Дата публикации</span><input required type="date" value={form.posted_at} onChange={(e) => setForm({ ...form, posted_at: e.target.value })}/></label><label className="wide"><span>Текст вакансии <small>(необязательно, но точнее)</small></span><textarea rows={3} value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })}/></label><div className="settings-actions wide"><Button className="primary" type="submit" disabled={busy}>{busy ? "Проверяем…" : "Добавить и проверить"}</Button></div></form>{message && <div className={message.includes("добавлена") ? "inline-success" : "inline-error"}>{message}</div>}</details>;
}

function VacancyCleanup({ onChanged }: { onChanged: () => Promise<void> }) {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  async function archive(mode: "inactive" | "ignored" | "all" | "reset") {
    setBusy(true); setMessage("");
    try {
      const result = await api.cleanupVacancies(mode);
      await onChanged();
      setMessage(result.message);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Не удалось очистить список.");
    } finally { setBusy(false); }
  }
  return <details className="settings-card"><summary>Очистка старых карточек</summary><p>Карточки не удаляются: они уходят из активного списка в архив. Одобренные и отправленные отклики не затрагиваются.</p><div className="settings-actions"><Button className="secondary" disabled={busy} onClick={() => void archive("inactive")}>Убрать неактивные</Button><Button className="secondary" disabled={busy} onClick={() => void archive("ignored")}>Убрать «Позже» и скрытые старше 21 дня</Button><Button className="ghost" disabled={busy} onClick={() => void archive("all")}>Очистить устаревшие</Button><Button className="secondary" disabled={busy} onClick={() => void archive("reset")}>Очистить текущую подборку</Button></div>{message && <div className={message.includes("Не удалось") ? "inline-error" : "inline-success"}>{message}</div>}</details>;
}

function CandidateEditor({
  candidate,
  onSaved,
}: {
  candidate?: Candidate;
  onSaved: (saved: Candidate) => void;
}) {
  const [form, setForm] = useState<CandidateInput>(() => ({
    name: candidate?.name || "",
    target_title: candidate?.target_title || "",
    english_level: candidate?.english_level || "",
    desired_countries: candidate?.desired_countries || "",
    salary_min: candidate?.salary_min || 0,
    notes: candidate?.notes || "",
    hard_exclude: candidate?.hard_exclude || "",
    hard_require: candidate?.hard_require || "",
    preferred_regions: candidate?.preferred_regions || "",
    preferred_cities: candidate?.preferred_cities || "",
    preferred_companies: candidate?.preferred_companies || "",
    priority_titles: candidate?.priority_titles || candidate?.target_title || "",
    contact_email: candidate?.contact_email || "",
    cover_tone: candidate?.cover_tone || "",
    cover_length: candidate?.cover_length || "",
    manual_review: candidate?.manual_review ?? 1,
    skills: candidate?.skills || [],
  }));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  function applyVietnamPreset(kind: "qa_candidate" | "support_candidate") {
    const qa_candidate = kind === "qa_candidate";
    setForm((current) => ({
      ...current,
      name: current.name || (qa_candidate ? "QA-кандидат" : "Support-кандидат"),
      target_title: qa_candidate ? "Manual QA Engineer" : "Junior IT Support / Help Desk / Manual QA",
      priority_titles: qa_candidate
        ? "Manual QA Engineer; QA Engineer; QA Specialist; Software Tester"
        : "Junior IT Support; Help Desk; Service Desk; Junior QA; Manual QA; Trainee QA; Support Specialist",
      english_level: "A1",
      salary_min: qa_candidate ? 1300 : 1100,
      desired_countries: "Вьетнам: Дананг; удалённо из Вьетнама; hybrid/office во Вьетнаме; международные вакансии, доступные из Вьетнама",
      preferred_regions: "Вьетнам; Юго-Восточная Азия; Казахстан; remote worldwide",
      preferred_cities: "Дананг; Da Nang; Ханой; Хошимин",
      hard_exclude: qa_candidate
        ? "Russia-only; офис или гибрид в любом городе РФ; оформление по ТК РФ; российское юрлицо; Sber; Senior; Lead; Head; Director; обязательный B2 English; букмекерская компания"
        : "Russia-only; офис или гибрид в любом городе РФ; оформление по ТК РФ; российское юрлицо; Sber; Senior; Lead; Head; Manager; Director; Architect; Principal; обязательный B2 English; букмекерская компания",
      hard_require: qa_candidate
        ? "Manual QA/API QA/Product Support QA; international remote, доступно из Вьетнама, или работа во Вьетнаме; русскоязычная коммуникация/onboarding; без ТК РФ и российского юрлица; прозрачный international legal/contractor contract; указаны формат, график, оплата и контакт"
        : "Junior IT Support/Help Desk/QA Trainee; international remote, доступно из Вьетнама, или работа во Вьетнаме; onboarding/mentorship; без ТК РФ и российского юрлица; прозрачный international legal/contractor contract; указаны формат, график, оплата и контакт",
      notes: qa_candidate
        ? "Переезжаю в Дананг 1 сентября, документы для легального оформления во Вьетнаме в процессе. Английский A1; рассматриваю remote, hybrid или office во Вьетнаме. Для русскоязычной вакансии желательно уточнить возможность onboarding/daily на русском."
        : "Переезжаю в Дананг 1 сентября, документы для легального оформления во Вьетнаме в процессе. Ищу первую IT-роль: junior support/help desk/manual QA/trainee; нужен onboarding. Английский A1; рассматриваю remote, hybrid или office во Вьетнаме. Для русскоязычной вакансии желательно уточнить возможность daily на русском.",
    }));
  }

  async function applySavedVietnamPreset(kind: "qa_candidate" | "support_candidate") {
    // A new, unsaved profile has no id yet, so populate its form first.  For
    // an existing profile the server persists the preset in one safe update.
    if (!candidate) {
      applyVietnamPreset(kind);
      return;
    }
    setBusy(true);
    setError("");
    try {
      const saved = await api.applyVietnamPreset(candidate.id, kind);
      onSaved(saved);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Не удалось применить настройку.");
    } finally {
      setBusy(false);
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!form.name.trim()) return;
    setBusy(true);
    setError("");
    try {
      const saved = candidate
        ? await api.updateCandidate(candidate.id, form)
        : await api.createCandidate(form);
      onSaved(saved);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Не удалось сохранить профиль.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="settings-card profile-editor" onSubmit={submit}>
      <div className="section-heading compact">
        <div><h2>{candidate ? "Изменить профиль" : "Добавить профиль"}</h2><p>Профиль нужен для точного сравнения вакансий и выбора резюме.</p></div>
      </div>
      <div className="profile-preset"><div><strong>Готовые ключевые теги для поиска во Вьетнаме</strong><p>Заполнят только поиск: роль, A1, города, минимальную зарплату, исключения и заметку о переезде. Резюме, навыки, почта, фото и сертификаты не изменятся.</p></div><div className="preset-buttons"><Button className="secondary" disabled={busy} onClick={() => void applySavedVietnamPreset("qa_candidate")} type="button">Заполнить QA-кандидата · QA</Button><Button className="secondary" disabled={busy} onClick={() => void applySavedVietnamPreset("support_candidate")} type="button">Заполнить Support-кандидата · старт IT</Button></div></div>
      <div className="profile-form-grid">
        <label><span>Имя кандидата</span><input autoComplete="name" required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="QA-кандидат" /></label>
        <label><span>Целевая роль</span><input autoComplete="organization-title" value={form.target_title} onChange={(e) => setForm({ ...form, target_title: e.target.value })} placeholder="Повар, бухгалтер, QA Engineer…" /></label>
        <label><span>Названия — в первую очередь</span><input value={form.priority_titles} onChange={(e) => setForm({ ...form, priority_titles: e.target.value })} placeholder="Manual QA Engineer; QA Engineer" /><small>Точные названия будут выше похожих ролей.</small></label>
        <label><span>Английский</span><select value={form.english_level} onChange={(e) => setForm({ ...form, english_level: e.target.value })}><option value="">Не важно</option><option value="A1">A1 — базовый</option><option value="A2">A2</option><option value="B1">B1</option><option value="B1+">B1+</option><option value="B2">B2</option><option value="C1">C1</option><option value="C2">C2</option></select><button className="field-link" onClick={() => setForm({ ...form, english_level: "A1" })} type="button">Установить A1 для этого профиля</button></label>
        <label><span>Минимальная зарплата, $</span><input inputMode="numeric" min={0} type="number" value={form.salary_min} onChange={(e) => setForm({ ...form, salary_min: Number(e.target.value) || 0 })} /></label>
        <label className="wide"><span>География и формат</span><input value={form.desired_countries} onChange={(e) => setForm({ ...form, desired_countries: e.target.value })} placeholder="Например: Вьетнам; удалённо; гибрид Дананг" /></label>
        <label><span>Приоритетный регион</span><input value={form.preferred_regions} onChange={(e) => setForm({ ...form, preferred_regions: e.target.value })} placeholder="Вьетнам, Юго-Восточная Азия, remote worldwide" /></label>
        <label><span>Приоритетный город</span><input value={form.preferred_cities} onChange={(e) => setForm({ ...form, preferred_cities: e.target.value })} placeholder="Дананг; Da Nang; Ханой" /></label>
        <label className="wide"><span>Приоритетные компании <small>(необязательно)</small></span><input value={form.preferred_companies} onChange={(e) => setForm({ ...form, preferred_companies: e.target.value })} placeholder="Например: IREV; EPAM; конкретные компании через запятую" /></label>
        <label className="wide"><span>Что исключать <small>(необязательно)</small></span><input value={form.hard_exclude} onChange={(e) => setForm({ ...form, hard_exclude: e.target.value })} placeholder="Sber; офис Москва" /></label>
        <label className="wide"><span>Что обязательно <small>(необязательно)</small></span><input value={form.hard_require} onChange={(e) => setForm({ ...form, hard_require: e.target.value })} placeholder="remote; official source" /></label>
        <label className="wide"><span>Ключевые навыки <small>(через запятую)</small></span><input value={form.skills.join(", ")} onChange={(e) => setForm({ ...form, skills: e.target.value.split(",").map((item) => item.trim()).filter(Boolean) })} placeholder="Поварское дело, меню, HACCP" /></label>
        <label><span>E-mail именно этого кандидата</span><input autoComplete="email" type="email" value={form.contact_email} onChange={(e) => setForm({ ...form, contact_email: e.target.value })} placeholder="name@example.com" /><small>Можно указать отдельную почту QA-кандидата и Support-кандидата: при подготовке отклика сервис подставит её вместо общей.</small></label>
        <label><span>Стиль письма этого кандидата</span><select value={form.cover_tone} onChange={(e) => setForm({ ...form, cover_tone: e.target.value as CandidateInput["cover_tone"] })}><option value="">Как в общих настройках</option><option value="formal">Формальный</option><option value="friendly">Дружелюбный</option></select></label>
        <label><span>Объём письма</span><select value={form.cover_length} onChange={(e) => setForm({ ...form, cover_length: e.target.value as CandidateInput["cover_length"] })}><option value="">Как в общих настройках</option><option value="compact">Компактно</option><option value="detailed">Подробно</option></select></label>
        <label className="check-row"><input checked={Boolean(form.manual_review)} onChange={(e) => setForm({ ...form, manual_review: e.target.checked ? 1 : 0 })} type="checkbox"/><span>Всегда раскрывать проверку перед отправкой</span></label>
        <label className="wide"><span>Заметка</span><textarea rows={2} value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} /></label>
      </div>
      {error && <div className="inline-error">{error}</div>}
      <div className="settings-actions"><Button className="primary" disabled={busy} type="submit">{busy ? "Сохраняем…" : "Сохранить профиль"}</Button></div>
    </form>
  );
}

function CareerAssistantSetup({ candidates, onChanged }: { candidates: Candidate[]; onChanged: () => Promise<void> }) {
  const [candidateId, setCandidateId] = useState(candidates[0]?.id || 0);
  const selected = candidates.find((candidate) => candidate.id === candidateId) || candidates[0];
  const [target, setTarget] = useState(selected?.target_title || "");
  const [mustHave, setMustHave] = useState(selected?.hard_require || "");
  const [priorityTitles, setPriorityTitles] = useState(selected?.priority_titles || selected?.target_title || "");
  const [avoid, setAvoid] = useState(selected?.hard_exclude || "");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (!candidates.some((candidate) => candidate.id === candidateId)) setCandidateId(candidates[0]?.id || 0); }, [candidateId, candidates]);
  useEffect(() => { const next = candidates.find((candidate) => candidate.id === candidateId) || candidates[0]; setTarget(next?.target_title || ""); setPriorityTitles(next?.priority_titles || next?.target_title || ""); setMustHave(next?.hard_require || ""); setAvoid(next?.hard_exclude || ""); }, [candidateId, candidates]);
  async function save() {
    if (!selected) return;
    setBusy(true); setMessage("");
    try { await api.updateCandidate(selected.id, { name: selected.name, target_title: target, english_level: selected.english_level, desired_countries: selected.desired_countries, salary_min: selected.salary_min, notes: selected.notes || "", hard_exclude: avoid, hard_require: mustHave, preferred_regions: selected.preferred_regions || "", preferred_cities: selected.preferred_cities || "", preferred_companies: selected.preferred_companies || "", priority_titles: priorityTitles, contact_email: selected.contact_email || "", cover_tone: selected.cover_tone || "", cover_length: selected.cover_length || "", manual_review: selected.manual_review ?? 1, skills: selected.skills || [] }); await onChanged(); setMessage("Помощник будет учитывать эти правила при следующем поиске."); }
    catch (error) { setMessage(error instanceof Error ? error.message : "Не удалось сохранить настройки."); }
    finally { setBusy(false); }
  }
  if (!selected) return null;
  return <section className="settings-card assistant-setup"><div className="section-heading compact"><div><span className="eyebrow">ВАШ ЛИЧНЫЙ ПОМОЩНИК</span><h2>Настройте подбор под себя</h2><p>Не нужен технический опыт: напишите обычными словами, что искать и чего избегать.</p></div><span className="assistant-orb">AI</span></div>
    <div className="profile-form-grid"><label><span>Для кого ищем</span><select value={candidateId} onChange={(e) => setCandidateId(Number(e.target.value))}>{candidates.map((candidate) => <option key={candidate.id} value={candidate.id}>{candidate.name}</option>)}</select></label><label><span>Какая работа нужна</span><input value={target} onChange={(e) => setTarget(e.target.value)} placeholder="Например: Junior QA, повар, бухгалтер" /></label><label className="wide"><span>Показывать первыми</span><input value={priorityTitles} onChange={(e) => setPriorityTitles(e.target.value)} placeholder="Например: Manual QA Engineer; QA Engineer" /><small>Точное название получит заметный приоритет, остальные подходящие роли останутся ниже.</small></label><label className="wide"><span>Обязательно должно быть</span><input value={mustHave} onChange={(e) => setMustHave(e.target.value)} placeholder="Например: удалённо из Вьетнама; Дананг; русскоязычная команда" /></label><label className="wide"><span>Точно не показывать</span><input value={avoid} onChange={(e) => setAvoid(e.target.value)} placeholder="Например: Senior; офис Москва; Sber; релокация в другую страну" /></label></div>
    {message && <div className={message.includes("Не удалось") ? "inline-error" : "inline-success"}>{message}</div>}
    <div className="settings-actions"><Button className="primary" disabled={busy} onClick={() => void save()}>{busy ? "Сохраняем…" : "Сохранить правила помощника"}</Button></div>
  </section>;
}

function WorkflowGuide({ onNavigate }: { onNavigate: (view: View) => void }) {
  const [step, setStep] = useState(0);
  const steps: Array<{ title: string; text: string; view: View; action: string }> = [
    { title: "1. Профиль", text: "Укажите опыт, желаемую работу и ограничения. Помощник не будет угадывать важные условия.", view: "profiles", action: "Настроить помощника" },
    { title: "2. Подборка", text: "Нажмите «Обновить» — сервис проверит свежие публичные вакансии и отсеет неподходящие уровни.", view: "jobs", action: "Посмотреть вакансии" },
    { title: "3. Черновики", text: "Выберите подходящие вакансии: сервис подготовит письмо и правильное резюме, но не отправит их сам.", view: "applications", action: "Подготовить отклики" },
    { title: "4. Отправка", text: "Проверьте адрес, текст и вложение. Затем отправьте письмо вручную — так вы полностью контролируете отклик.", view: "applications", action: "Открыть отклики" },
  ];
  const current = steps[step];
  return <section className="workflow-guide"><div className="workflow-progress">{steps.map((item, index) => <button aria-current={index === step ? "step" : undefined} className={index <= step ? "done" : ""} key={item.title} onClick={() => setStep(index)} type="button"><span>{index + 1}</span>{item.title.replace(/^\d\. /, "")}</button>)}</div><div className="workflow-copy"><div><span className="eyebrow">БЫСТРЫЙ ТУР</span><h2>{current.title}</h2><p>{current.text}</p></div><Button className="primary" onClick={() => onNavigate(current.view)}>{current.action}</Button></div></section>;
}

function downloadText(name: string, content: string, type = "text/plain;charset=utf-8") {
  const href = URL.createObjectURL(new Blob([content], { type }));
  const link = document.createElement("a"); link.href = href; link.download = name; link.click(); URL.revokeObjectURL(href);
}

function escapeHtml(value: string) {
  return value.replace(/[&<>"']/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }[character] || character));
}

function resumeHtml(candidate: Candidate, title: string, content: string) {
  const body = content.split(/\r?\n/).filter(Boolean).map((line) => `<p>${escapeHtml(line)}</p>`).join("");
  const photo = candidate.photo_data ? `<img class="photo" src="${candidate.photo_data}" alt="">` : "";
  return `<!doctype html><html><head><meta charset="utf-8"><title>${escapeHtml(title)}</title><style>body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;max-width:760px;margin:48px auto;color:#111827;line-height:1.55}.head{display:flex;gap:18px;align-items:center}.photo{width:82px;height:82px;object-fit:cover;border-radius:14px}h1{font-size:28px;margin:0 0 4px}small{color:#667085}p{margin:6px 0}@media print{body{margin:24px}}</style></head><body><div class="head">${photo}<div><h1>${escapeHtml(candidate.name)}</h1><small>${escapeHtml(candidate.target_title)} · ${escapeHtml(title)}</small></div></div><hr>${body}</body></html>`;
}

function downloadResume(candidate: Candidate, title: string, language: string, content: string, format: "txt" | "html" | "doc") {
  const safeName = `${(candidate.name || "resume").replace(/[^\p{L}\p{N}_-]+/gu, "-")}-${language}`;
  if (format === "txt") return downloadText(`${safeName}.txt`, content);
  const markup = resumeHtml(candidate, title, content);
  return downloadText(`${safeName}.${format}`, markup, format === "doc" ? "application/msword" : "text/html;charset=utf-8");
}

function printResume(candidate: Candidate, title: string, content: string) {
  const popup = window.open("", "_blank", "noopener,noreferrer");
  if (!popup) return;
  popup.document.open(); popup.document.write(resumeHtml(candidate, title, content)); popup.document.close();
  window.setTimeout(() => popup.print(), 250);
}

function ResumePreview({ candidate, content }: { candidate: Candidate; content: string }) {
  const lines = content.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  if (!lines.length) return <p className="resume-empty">Добавьте текст резюме — здесь появится его точное, читаемое превью.</p>;
  return <div className="resume-document"><div className="resume-document-head">{candidate.photo_data ? <img src={candidate.photo_data} alt="Фото кандидата"/> : null}<div><strong>{candidate.name}</strong><span>{candidate.target_title}</span></div></div>{lines.map((line, index) => {
    const heading = /^(профиль|навыки|опыт|образование|сертификаты|цель|profile|skills|experience|education|certificates|objective)$/i.test(line.replace(/[:.]/g, ""));
    const name = index === 0 && line.length < 120;
    return heading ? <h4 key={`${line}-${index}`}>{line}</h4> : name ? <h3 key={`${line}-${index}`}>{line}</h3> : <p key={`${line}-${index}`} className={line.startsWith("-") || line.startsWith("•") ? "resume-bullet" : ""}>{line}</p>;
  })}</div>;
}

function ResumeStudio({ candidate, onChanged }: { candidate: Candidate; onChanged: () => Promise<void> }) {
  const current = candidate.resumes?.find((item) => item.language === "EN") || candidate.resumes?.[0];
  const [title, setTitle] = useState(current?.title || "Основное резюме");
  const [content, setContent] = useState(current?.content || "");
  const [language, setLanguage] = useState<"RU" | "EN" | "SR" | "OTHER">((current?.language as "RU" | "EN" | "SR" | "OTHER") || "EN");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [autoSave, setAutoSave] = useState("Предпросмотр обновляется сразу");
  const autoSaveTimer = useRef<number | null>(null);
  const [certificate, setCertificate] = useState<Omit<Certificate, "id">>({ title: "", issuer: "", credential_url: "", issued_at: "", notes: "", include_in_resume: 1 });

  useEffect(() => {
    const next = candidate.resumes?.find((item) => item.language === language);
    const defaultTitle = language === "RU" ? "Основное резюме" : language === "SR" ? "Biografija" : language === "EN" ? "Resume" : "Resume";
    setTitle(next?.title || defaultTitle); setContent(next?.content || "");
  }, [candidate, language]);

  async function save() {
    if (content.trim().length < 20) { setMessage("Добавьте минимум 20 символов текста резюме."); return; }
    setBusy(true); setMessage("");
    try { await api.saveResume(candidate.id, { title, language, content }); await onChanged(); setMessage("Резюме сохранено приватно."); setAutoSave("Все изменения сохранены"); }
    catch (caught) { setMessage(caught instanceof Error ? caught.message : "Не удалось сохранить резюме."); }
    finally { setBusy(false); }
  }

  function changeContent(next: string) {
    setContent(next); setAutoSave("Черновик сохраняется…");
    if (autoSaveTimer.current) window.clearTimeout(autoSaveTimer.current);
    autoSaveTimer.current = window.setTimeout(() => {
      if (next.trim().length < 20) { setAutoSave("Добавьте ещё немного текста для автосохранения"); return; }
      api.saveResume(candidate.id, { title, language, content: next })
        .then(() => { setAutoSave("Все изменения сохранены"); return onChanged(); })
        .catch(() => setAutoSave("Не удалось автосохранить — используйте кнопку «Сохранить»"));
    }, 900);
  }

  async function transform() {
    if (content.trim().length < 20) { setMessage("Сначала вставьте текст резюме."); return; }
    setBusy(true); setMessage("Очередь → загрузка → извлечение позиций → структурирование…");
    try { const result = await api.transformResume({ candidate_id: candidate.id, content, language }); setContent(result.content); setMessage(result.message); }
    catch (caught) { setMessage(caught instanceof Error ? caught.message : "Преобразование не удалось."); }
    finally { setBusy(false); }
  }

  async function photo(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]; if (!file) return;
    setBusy(true); setMessage("Проверяем и прикрепляем фото приватно…");
    try { await api.uploadPhoto(candidate.id, file, language); await onChanged(); setMessage(`Фото прикреплено к версии ${language}.`); }
    catch (caught) { setMessage(caught instanceof Error ? caught.message : "Не удалось прикрепить фото."); }
    finally { setBusy(false); event.target.value = ""; }
  }

  async function addCertificate() {
    if (!certificate.title.trim()) { setMessage("Укажите название сертификата."); return; }
    setBusy(true);
    try { await api.addCertificate(candidate.id, certificate); setCertificate({ title: "", issuer: "", credential_url: "", issued_at: "", notes: "", include_in_resume: 1 }); await onChanged(); setMessage("Сертификат добавлен."); }
    catch (caught) { setMessage(caught instanceof Error ? caught.message : "Не удалось добавить сертификат."); }
    finally { setBusy(false); }
  }

  async function removeCertificate(id: number) {
    setBusy(true);
    try { await api.deleteCertificate(candidate.id, id); await onChanged(); }
    catch (caught) { setMessage(caught instanceof Error ? caught.message : "Не удалось удалить сертификат."); }
    finally { setBusy(false); }
  }

  return <section className="settings-card profile-editor resume-studio">
    <div className="section-heading compact"><div><h2>Резюме и фото</h2><p>Данные доступны только вам и используются для подбора. Никуда не публикуются.</p></div></div>
    <div className="resume-row">
      <div className="resume-photo">{candidate.photo_data ? <img src={candidate.photo_data} alt="Фото кандидата" /> : <span>Фото</span>}</div>
      <label className="secondary upload-label dashed-upload">Прикрепить фото<input accept="image/jpeg,image/png,image/webp" disabled={busy} onChange={photo} type="file" /></label>
    </div>
    <div className="resume-format-tabs" aria-label="Версии резюме">{(["EN", "RU", "SR"] as const).map((item) => <button className={language === item ? "active" : ""} key={item} onClick={() => setLanguage(item)} type="button"><strong>{item}</strong><span>{candidate.resumes?.some((resume) => resume.language === item) ? "сохранено" : "создать версию"}</span></button>)}</div>
    <label><span>Язык резюме</span><select value={language} onChange={(e) => setLanguage(e.target.value as typeof language)}><option value="EN">English</option><option value="RU">Русский</option><option value="SR">Srpski</option><option value="OTHER">Другой</option></select></label>
    <label><span>Название резюме</span><input value={title} onChange={(e) => { setTitle(e.target.value); setAutoSave("Название изменено — нажмите «Сохранить»"); }} /></label>
    <label><span>Текст резюме <small className="live-save">● {autoSave}</small></span><textarea rows={8} value={content} onChange={(e) => changeContent(e.target.value)} placeholder="Вставьте или отредактируйте текст. Предпросмотр меняется сразу, черновик сохраняется автоматически." /></label>
    {message && <div className={message.includes("не удалось") ? "inline-error" : "inline-success"}>{message}</div>}
    <div className="settings-actions export-actions"><Button className="secondary" disabled={busy} onClick={() => void transform()} type="button">{busy ? "Очередь → позиции → структура…" : "Адаптировать под поиск"}</Button><Button className="secondary" disabled={!content} onClick={() => downloadResume(candidate, title, language, content, "txt")} type="button">TXT</Button><Button className="secondary" disabled={!content} onClick={() => downloadResume(candidate, title, language, content, "doc")} type="button">Word</Button><Button className="secondary" disabled={!content} onClick={() => downloadResume(candidate, title, language, content, "html")} type="button">HTML</Button><Button className="secondary" disabled={!content} onClick={() => printResume(candidate, title, content)} type="button">Печать / PDF</Button><Button className="primary" disabled={busy} onClick={() => void save()} type="button">Сохранить</Button></div>
    <details className="resume-preview" open><summary>Предпросмотр резюме · {language}</summary><ResumePreview candidate={candidate} content={content} /></details>
    <section className="certificate-panel"><div className="section-heading compact"><div><h3>Сертификаты</h3><p>Добавляются к конкретному кандидату и включаются в письмо только по вашему выбору.</p></div></div>
      {(candidate.certificates || []).length ? (candidate.certificates || []).map((item) => <div className="table-row" key={item.id}><div><strong>{item.title}</strong><span>{[item.issuer, item.issued_at].filter(Boolean).join(" · ")}</span></div><Button className="ghost" disabled={busy} onClick={() => void removeCertificate(item.id)} type="button">Удалить</Button></div>) : <div className="certificate-empty">Сертификатов в этом личном кабинете пока нет. Они не переносятся из другого аккаунта автоматически — это защита ваших данных.</div>}
      <div className="profile-form-grid"><label><span>Название</span><input value={certificate.title} onChange={(e) => setCertificate({ ...certificate, title: e.target.value })} placeholder="ISTQB Foundation" /></label><label><span>Организация</span><input value={certificate.issuer} onChange={(e) => setCertificate({ ...certificate, issuer: e.target.value })} placeholder="ISTQB" /></label><label className="wide"><span>Ссылка на подтверждение <small>(необязательно)</small></span><input value={certificate.credential_url} onChange={(e) => setCertificate({ ...certificate, credential_url: e.target.value })} placeholder="https://…" /></label></div>
      <div className="settings-actions"><Button className="secondary" disabled={busy} onClick={() => void addCertificate()} type="button">Добавить сертификат</Button></div>
    </section>
  </section>;
}

function ApplicationsList({ applications }: { applications: Application[] }) {
  if (!applications.length) {
    return <EmptyState title="Откликов пока нет" text="После одобрения вакансии подготовьте письмо и подтвердите отправку вручную." />;
  }
  return (
    <div className="table-card">
      <div className="table-head">
        <span>Компания и позиция</span><span>Статус</span><span>Дата</span>
      </div>
      {applications.map((item) => (
        <div className="table-row" key={item.id}>
          <div>
            <strong>{item.company || "Компания"}</strong>
            <span>{item.position || "Вакансия"}</span>
          </div>
          <span className={`chip ${item.status === "sent" ? "success" : "info"}`}>{item.status}</span>
          <time>{item.created_at ? new Date(item.created_at).toLocaleDateString("ru-RU") : "—"}</time>
        </div>
      ))}
    </div>
  );
}

function ApplicationTrackerPanel() {
  const [data, setData] = useState<ApplicationTracker | null>(null);
  const [sheetUrl, setSheetUrl] = useState("");
  const [webhookUrl, setWebhookUrl] = useState("");
  const [secret, setSecret] = useState("");
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("");

  async function load() {
    try {
      const next = await api.applicationTracker();
      setData(next);
      setSheetUrl(next.google_sheets.spreadsheet_url || "");
      setWebhookUrl(next.google_sheets.webhook_url || "");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Не удалось загрузить реестр откликов.");
    }
  }

  useEffect(() => { void load(); }, []);

  function editRow(id: number, patch: Partial<TrackerRow>) {
    setData((current) => current ? { ...current, rows: current.rows.map((row) => row.id === id ? { ...row, ...patch } : row) } : current);
  }

  async function saveRow(row: TrackerRow, patch: Partial<Pick<TrackerRow, "response_at" | "result" | "comments" | "salary_range">>) {
    setBusy(`row-${row.id}`);
    try {
      const result = await api.updateTracker(row.id, patch);
      editRow(row.id, result.row);
      setMessage(result.sync.status === "synced" ? "Строка сохранена и синхронизирована." : "Строка сохранена в CareerMove; синхронизация ожидает подключения Google Sheets.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Не удалось сохранить строку.");
    } finally {
      setBusy("");
    }
  }

  async function saveSheetSettings() {
    let nextSecret = secret.trim();
    if (webhookUrl.trim() && !data?.google_sheets.secret_configured && !nextSecret) {
      nextSecret = crypto.randomUUID().replaceAll("-", "") + crypto.randomUUID().replaceAll("-", "");
      setSecret(nextSecret);
    }
    setBusy("settings");
    try {
      const google_sheets = await api.saveGoogleSheets({ spreadsheet_url: sheetUrl, webhook_url: webhookUrl, webhook_secret: nextSecret });
      setData((current) => current ? { ...current, google_sheets } : current);
      setMessage(google_sheets.connected ? "Google Sheets подключён. Запустите синхронизацию строк." : "Параметры таблицы сохранены.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Не удалось сохранить подключение.");
    } finally {
      setBusy("");
    }
  }

  async function syncRows() {
    setBusy("sync");
    try {
      const result = await api.syncApplicationTracker();
      await load();
      if (result.snapshot?.status === "error") throw new Error(result.snapshot.detail || "Google Sheets отклонил снимок очередей.");
      const queueRows = result.snapshot?.status === "synced" ? ` Очередей обновлено: ${result.snapshot.rows}.` : "";
      const pulledRows = result.pulled?.status === "synced" ? ` Из таблицы принято изменений: ${result.pulled.updated}.` : "";
      setMessage(`Синхронизировано откликов: ${result.synced}.${queueRows}${pulledRows}`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Не удалось синхронизировать таблицу.");
    } finally {
      setBusy("");
    }
  }

  return <div className="tracker-stack">
    <section className="settings-card tracker-connect">
      <div className="section-heading compact"><div><span className="eyebrow">GOOGLE SHEETS</span><h2>Таблица откликов</h2><p>Вакансия попадает в реестр при нажатии «Взять в работу». Ответ HR, этап и комментарий редактируются здесь.</p></div><span className={`chip ${data?.google_sheets.connected ? "success" : "neutral"}`}>{data?.google_sheets.connected ? "Подключено" : "Не подключено"}</span></div>
      <div className="tracker-connect-grid">
        <label><span>Ссылка на Google Таблицу</span><input value={sheetUrl} onChange={(event) => setSheetUrl(event.target.value)} placeholder="https://docs.google.com/spreadsheets/…"/></label>
        <label><span>Apps Script Web App</span><input value={webhookUrl} onChange={(event) => setWebhookUrl(event.target.value)} placeholder="https://script.google.com/macros/s/…/exec"/></label>
        <label><span>Секрет синхронизации</span><input autoComplete="new-password" type="password" value={secret} onChange={(event) => setSecret(event.target.value)} placeholder={data?.google_sheets.secret_configured ? "Секрет уже сохранён" : "Будет создан автоматически"}/></label>
      </div>
      <div className="settings-actions tracker-actions"><a className="button secondary" download href="/CareerMove-Job-Tracker.xlsx"><Icon name="download" size={17}/> Таблица очередей</a><a className="button secondary" download href="/careermove-google-sheets.gs"><Icon name="download" size={17}/> Apps Script</a>{data?.google_sheets.spreadsheet_url ? <a className="button secondary" href={data.google_sheets.spreadsheet_url} rel="noreferrer" target="_blank">Открыть таблицу <Icon name="external" size={16}/></a> : null}<Button className="secondary" disabled={Boolean(busy)} onClick={() => void saveSheetSettings()}>Сохранить подключение</Button><Button className="primary" disabled={Boolean(busy)} icon="refresh" onClick={() => void syncRows()}>{busy === "sync" ? "Синхронизация…" : "Синхронизировать всё"}</Button></div>
      {data?.google_sheets.last_sync_at ? <div className={data.google_sheets.last_sync_status === "error" ? "inline-error" : "inline-success"}>Последняя синхронизация: {new Date(data.google_sheets.last_sync_at).toLocaleString("ru-RU")} · {data.google_sheets.last_sync_detail || data.google_sheets.last_sync_status}</div> : null}
      {secret ? <div className="inline-warning">Сохраните этот секрет в константе <code>SHARED_SECRET</code> скачанного Apps Script: <code>{secret}</code></div> : null}
      {message ? <div className={/не удалось|ошиб/i.test(message) ? "inline-error" : "inline-success"}>{message}</div> : null}
    </section>
    <section className="tracker-table-section">
      <div className="section-heading compact"><div><span className="eyebrow">ВОРОНКА</span><h2>Реестр вакансий в работе</h2></div><span>{data?.rows.length || 0} строк</span></div>
      {data?.rows.length ? <div className="tracker-table-wrap"><table className="tracker-table"><thead><tr><th>№</th><th>Кандидат</th><th>Дата отклика</th><th>Дата ответа</th><th>Должность</th><th>Компания</th><th>Результат</th><th>Комментарии</th><th>Зарплатная вилка</th><th>Язык</th><th>Синхронизация</th></tr></thead><tbody>{data.rows.map((row) => <tr key={row.id}><td>{row.id}</td><td>{row.candidate}</td><td>{row.applied_at || "—"}</td><td><input aria-label={`Дата ответа ${row.company}`} type="date" value={row.response_at || ""} onChange={(event) => editRow(row.id, { response_at: event.target.value })} onBlur={(event) => void saveRow(row, { response_at: event.currentTarget.value })}/></td><td><a href={row.vacancy_link} rel="noreferrer" target="_blank">{row.position || "Вакансия"}</a><small>{row.vacancy_source}</small></td><td>{row.company}</td><td><select aria-label={`Результат ${row.company}`} value={row.result || ""} onChange={(event) => { const result = event.target.value; editRow(row.id, { result }); void saveRow({ ...row, result }, { result }); }}><option value="">Не указан</option>{data.results.map((item) => <option key={item} value={item}>{item}</option>)}</select></td><td><textarea aria-label={`Комментарии ${row.company}`} rows={2} value={row.comments || ""} onChange={(event) => editRow(row.id, { comments: event.target.value })} onBlur={(event) => void saveRow(row, { comments: event.currentTarget.value })}/></td><td><input aria-label={`Зарплатная вилка ${row.company}`} value={row.salary_range || ""} onChange={(event) => editRow(row.id, { salary_range: event.target.value })} onBlur={(event) => void saveRow(row, { salary_range: event.currentTarget.value })}/></td><td>{row.language}</td><td><span className={`chip ${row.sync_status === "synced" ? "success" : row.sync_status === "error" ? "warning" : "neutral"}`}>{busy === `row-${row.id}` ? "Сохраняем" : row.sync_status === "synced" ? "В таблице" : row.sync_status === "error" ? "Ошибка" : "Ожидает"}</span>{row.sync_error ? <small title={row.sync_error}>{row.sync_error}</small> : null}</td></tr>)}</tbody></table></div> : <EmptyState title="Реестр пока пуст" text="Нажмите «Взять в работу» в карточке вакансии — строка появится здесь автоматически."/>}
    </section>
  </div>;
}

function ApplicationStudio({ jobs, applications, onChanged }: { jobs: Job[]; applications: Application[]; onChanged: () => Promise<void> }) {
  const [prefs, setPrefs] = useState<ApplicationPreferences | null>(null);
  const [selected, setSelected] = useState<number[]>([]);
  const [reviewed, setReviewed] = useState<number[]>([]);
  const [preview, setPreview] = useState<{ vacancy_id: number; subject: string; cover_letter: string; recipient_email: string; from_email?: string; manual_review?: boolean; resume: { title: string; language: string; content: string } | null } | null>(null);
  const [code, setCode] = useState(""); const [message, setMessage] = useState(""); const [busy, setBusy] = useState(false);
  useEffect(() => { api.applicationPreferences().then(setPrefs).catch((e) => setMessage(e.message)); }, []);
  const readyJobs = jobs.filter((job) => ["approved", "ready"].includes(job.status) && !applications.some((app) => app.link === job.link));
  async function save() { if (!prefs) return; setBusy(true); try { setPrefs(await api.saveApplicationPreferences(prefs)); setMessage("Настройки сопроводительного сохранены."); } catch (e) { setMessage(e instanceof Error ? e.message : "Не удалось сохранить."); } finally { setBusy(false); } }
  async function compose(id: number) { setBusy(true); try { const draft = await api.composeApplication(id); setPreview(draft); setReviewed((items) => items.includes(id) ? items : [...items, id]); setMessage("Черновик открыт: проверьте адрес, резюме и текст, затем выберите карточку для очереди."); } catch (e) { setMessage(e instanceof Error ? e.message : "Не удалось подготовить письмо."); } finally { setBusy(false); } }
  async function queue() {
    if (!selected.length) { setMessage("Выберите хотя бы одну проверенную вакансию."); return; }
    const unreviewed = selected.filter((id) => !reviewed.includes(id));
    if (unreviewed.length) { setMessage("Сначала откройте «Проверить письмо» у каждой выбранной вакансии. Черновики без проверки не создаются."); return; }
    setBusy(true); try { const result = await api.prepareApplications(selected); setMessage(result.message); setSelected([]); await onChanged(); } catch (e) { setMessage(e instanceof Error ? e.message : "Не удалось подготовить черновики."); } finally { setBusy(false); }
  }
  async function activate() { setBusy(true); try { setPrefs(await api.activateProCode(code)); setCode(""); setMessage("Лимит Профи включён."); } catch (e) { setMessage(e instanceof Error ? e.message : "Код не подошёл."); } finally { setBusy(false); } }
  if (!prefs) return <section className="settings-card"><p>Загружаем настройки откликов…</p></section>;
  return <>
    <section className="settings-card"><div className="section-heading compact"><div><span className="eyebrow">СОПРОВОДИТЕЛЬНОЕ</span><h2>Письмо и вложение</h2><p>Генерируется внутри сервиса. До вашего подтверждения ничего не отправляется.</p></div></div>
      <div className="profile-form-grid"><label><span>Общий email для ответа</span><input autoComplete="email" value={prefs.from_email} onChange={(e) => setPrefs({ ...prefs, from_email: e.target.value })} /><small>Если у кандидата заполнен отдельный e-mail в «Профилях», будет использован именно он.</small></label><label><span>Тон</span><select value={prefs.tone} onChange={(e) => setPrefs({ ...prefs, tone: e.target.value as "formal" | "friendly" })}><option value="formal">Формальный</option><option value="friendly">Дружелюбный</option></select></label><label><span>Объём</span><select value={prefs.length} onChange={(e) => setPrefs({ ...prefs, length: e.target.value as "compact" | "detailed" })}><option value="compact">Компактно</option><option value="detailed">Подробно</option></select></label><label className="check-row"><input checked={prefs.include_certificates} onChange={(e) => setPrefs({ ...prefs, include_certificates: e.target.checked })} type="checkbox"/><span>Указывать сертификаты</span></label><label className="check-row"><input checked={prefs.include_achievements} onChange={(e) => setPrefs({ ...prefs, include_achievements: e.target.checked })} type="checkbox"/><span>Указывать достижения</span></label></div>
      <div className="letter-style-samples" aria-label="Пример выбранного стиля письма"><div><strong>Как будет звучать</strong><span>{prefs.tone === "formal" ? "Формально и по делу" : "Тепло и профессионально"} · {prefs.length === "compact" ? "3–4 коротких абзаца" : "вводный абзац, опыт и мотивация"}</span></div><p>{prefs.tone === "formal" ? "Здравствуйте! Меня заинтересовала позиция. Мой опыт соответствует ключевым задачам вакансии; буду рада обсудить детали." : "Здравствуйте! Мне очень откликнулась ваша позиция — особенно задачи команды. Буду рада коротко рассказать, чем могу быть полезна."}</p></div>
      <div className="settings-actions"><Button className="primary" disabled={busy} onClick={() => void save()}>Сохранить стиль письма</Button></div>
    </section>
    <section className="settings-card autoqueue-panel"><div className="section-heading compact"><div><span className="eyebrow">БЕЗОПАСНАЯ АВТООЧЕРЕДЬ</span><h2>До {prefs.daily_limit} персональных черновиков в день</h2><p>Подготовлено сегодня: {prefs.prepared_today}/{prefs.daily_limit}. Сервис готовит письмо и вложение, но не отправляет их сам.</p></div><span className="queue-counter">{readyJobs.length} к проверке</span></div>
      <div className="queue-steps" aria-label="Как работает автоочередь"><div><b>1</b><strong>Выбрать вакансию</strong><span>Только одобренные карточки</span></div><div><b>2</b><strong>Проверить черновик</strong><span>Адрес, резюме, тон и текст</span></div><div><b>3</b><strong>Подтвердить очередь</strong><span>Создаётся черновик, не отправка</span></div></div>
      {!prefs.pro_enabled && <div className="settings-actions"><input aria-label="Код Профи" value={code} onChange={(e) => setCode(e.target.value)} placeholder="Код Профи"/><Button className="secondary" disabled={busy || !code} onClick={() => void activate()}>Подключить +20</Button></div>}
      {readyJobs.length ? <div className="queue-list">{readyJobs.slice(0, Math.max(0, prefs.daily_limit - prefs.prepared_today)).map((job) => {
        const isReviewed = reviewed.includes(job.id);
        const isSelected = selected.includes(job.id);
        return <article className={`queue-card ${isSelected ? "selected" : ""}`} key={job.id}>
          <label className="queue-select"><input aria-label={`Добавить ${job.position} в очередь`} checked={isSelected} disabled={!isReviewed} onChange={(e) => setSelected(e.target.checked ? [...selected, job.id] : selected.filter((id) => id !== job.id))} type="checkbox"/><span>{isReviewed ? "Добавить в очередь" : "Сначала проверить"}</span></label>
          <div className="queue-card-copy"><strong>{job.position}</strong><span>{job.company} · {job.candidate}</span><div className="queue-meta"><em>{job.score}% совпадение</em><em>{job.remote_location || "локация уточняется"}</em>{isReviewed ? <em className="checked">Письмо проверено</em> : <em className="needs-review">Нужна проверка</em>}</div></div>
          <Button className="secondary" disabled={busy} onClick={() => void compose(job.id)} type="button">{isReviewed ? "Открыть письмо" : "Проверить письмо"}</Button>
        </article>;
      })}</div> : <div className="queue-empty"><strong>Очередь ждёт одобренные вакансии</strong><p>Вернитесь в «Вакансии», откройте подходящую карточку и нажмите «Одобрить». Затем здесь появится письмо для обязательной проверки.</p></div>}
      <div className="settings-actions"><Button className="primary" disabled={busy || !selected.length} onClick={() => void queue()}>Создать проверенные черновики ({selected.length})</Button></div>
      {message && <div className={message.includes("не ") || message.includes("Код") ? "inline-error" : "inline-success"}>{message}</div>}
    </section>
    {preview && <section className="settings-card"><div className="section-heading compact"><div><span className="eyebrow">ШАГ 2 ИЗ 3 · ПРОВЕРКА</span><h2>Предпросмотр письма</h2><p>От: {preview.from_email || prefs.from_email}<br/>Кому: {preview.recipient_email || "адрес не указан — найдите контакт в вакансии"}<br/>Тема: {preview.subject}<br/>Вложение: {preview.resume?.title || "резюме не найдено"}</p></div></div>{preview.manual_review !== false && <details className="job-preview" open><summary>Финальная проверка перед внешним действием</summary><p>Проверьте адрес получателя, выбранное резюме, имя кандидата и текст. После проверки выберите вакансию выше: сервис создаст только черновик, а письмо не отправит.</p></details>}<pre className="letter-preview">{preview.cover_letter}</pre><div className="settings-actions"><Button className="secondary" disabled={!preview.resume} onClick={() => preview.resume && downloadResume({ name: "resume", target_title: "" } as Candidate, preview.resume.title || "resume", preview.resume.language, preview.resume.content, "doc")}>Скачать Word</Button><Button className="secondary" onClick={() => downloadText("career-move-draft.eml", `From: ${preview.from_email || prefs.from_email}\nTo: ${preview.recipient_email}\nSubject: ${preview.subject}\n\n${preview.cover_letter}`)}>Скачать черновик .eml</Button>{preview.recipient_email && <a className="button secondary" href={`mailto:${encodeURIComponent(preview.recipient_email)}?subject=${encodeURIComponent(preview.subject)}&body=${encodeURIComponent(preview.cover_letter)}`}>Открыть в почте после проверки</a>}</div></section>}
  </>;
}

function AppearanceSettings({
  appearance,
  onSave,
}: {
  appearance: Appearance;
  onSave: (appearance: Appearance) => Promise<void>;
}) {
  const [draft, setDraft] = useState(appearance);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => setDraft(appearance), [appearance]);

  async function save() {
    setSaving(true);
    setSaved(false);
    try {
      await onSave(draft);
      setSaved(true);
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="settings-card">
      <div className="section-heading compact">
        <div>
          <span className="eyebrow">ИНТЕРФЕЙС</span>
          <h2>Внешний вид</h2>
          <p>Настройки применяются ко всему приложению и сохраняются в аккаунте.</p>
        </div>
      </div>
      <div className="settings-grid">
        <fieldset>
          <legend>Тема</legend>
          <div className="choice-grid themes">
            {([
              ["system-light", "Светлая", "Системная и спокойная"],
              ["system-dark", "Тёмная", "Контрастная для вечера"],
              ["cyber-aurora", "Cyber Aurora", "Фирменная, но читаемая"],
            ] as const).map(([value, label, note]) => (
              <label className={`choice-card ${draft.theme === value ? "selected" : ""}`} key={value}>
                <input
                  checked={draft.theme === value}
                  name="theme"
                  onChange={() => setDraft({ ...draft, theme: value })}
                  type="radio"
                />
                <span className={`theme-swatch ${value}`} />
                <strong>{label}</strong>
                <small>{note}</small>
              </label>
            ))}
          </div>
        </fieldset>

        <fieldset>
          <legend>Размер текста: {draft.font_scale}%</legend>
          <input
            aria-label="Размер текста"
            className="range"
            max="125"
            min="85"
            onChange={(event) => setDraft({ ...draft, font_scale: Number(event.target.value) })}
            step="5"
            type="range"
            value={draft.font_scale}
          />
          <div className="range-labels"><span>85%</span><span>100%</span><span>125%</span></div>
        </fieldset>

        <fieldset>
          <legend>Компоновка</legend>
          <div className="segmented">
            {([
              ["auto", "Авто"],
              ["compact", "Компактно"],
              ["comfortable", "Просторно"],
            ] as const).map(([value, label]) => (
              <label className={draft.density === value ? "selected" : ""} key={value}>
                <input
                  checked={draft.density === value}
                  name="density"
                  onChange={() => setDraft({ ...draft, density: value })}
                  type="radio"
                />
                <span>{label}</span>
              </label>
            ))}
          </div>
        </fieldset>
      </div>
      <div className="settings-actions">
        <Button className="primary" disabled={saving} onClick={save}>
          {saving ? "Сохраняем…" : "Сохранить оформление"}
        </Button>
        {saved && <span className="saved-note"><Icon name="check" size={17} /> Сохранено</span>}
      </div>
    </section>
  );
}

function pushApplicationKey(value: string) {
  const padding = "=".repeat((4 - value.length % 4) % 4);
  const base64 = (value + padding).replace(/-/g, "+").replace(/_/g, "/");
  const raw = window.atob(base64);
  return Uint8Array.from([...raw].map((character) => character.charCodeAt(0)));
}

function NotificationSettings() {
  const supported = (
    "serviceWorker" in navigator
    && "PushManager" in window
    && "Notification" in window
  );
  const [enabled, setEnabled] = useState(supported && Notification.permission === "granted");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState(
    supported
      ? "После ежедневного поиска уведомление откроет новую подборку прямо в приложении."
      : "Установите CareerMove как приложение: на этом устройстве браузер не даёт доступ к push-уведомлениям.",
  );

  useEffect(() => {
    if (!supported) return;
    navigator.serviceWorker.ready
      .then((registration) => registration.pushManager.getSubscription())
      .then((subscription) => setEnabled(Boolean(subscription)))
      .catch(() => undefined);
  }, [supported]);

  async function enable() {
    if (!supported || busy) return;
    setBusy(true);
    try {
      const config = await api.pushConfig();
      if (!config.enabled || !config.public_key) {
        throw new Error("Сервер уведомлений ещё не активирован.");
      }
      const permission = await Notification.requestPermission();
      if (permission !== "granted") {
        throw new Error("Разрешение не выдано. Его можно включить в настройках браузера или системы.");
      }
      const registration = await navigator.serviceWorker.ready;
      const existing = await registration.pushManager.getSubscription();
      const subscription = existing || await registration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: pushApplicationKey(config.public_key),
      });
      const data = subscription.toJSON();
      if (!data.endpoint || !data.keys?.p256dh || !data.keys.auth) {
        throw new Error("Браузер не вернул ключи push-подписки.");
      }
      await api.pushSubscribe({
        endpoint: data.endpoint,
        keys: { p256dh: data.keys.p256dh, auth: data.keys.auth },
      });
      setEnabled(true);
      setMessage("Уведомления включены. Они будут приходить на это устройство после автопоиска.");
    } catch (caught) {
      setMessage(caught instanceof Error ? caught.message : "Не удалось включить уведомления.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="settings-card notification-settings">
      <div>
        <span className="eyebrow">АВТОПОИСК · 10:00 / 18:00 МСК</span>
        <h2>Уведомления на это устройство</h2>
        <p>{message}</p>
      </div>
      <Button
        className={enabled ? "secondary" : "primary"}
        disabled={!supported || busy}
        icon={enabled ? "check" : "signal"}
        onClick={() => void enable()}
      >
        {busy ? "Подключаем…" : enabled ? "Уведомления включены" : "Включить уведомления"}
      </Button>
    </section>
  );
}

function CareerBotSettings() {
  const [status, setStatus] = useState<TelegramBotStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  const refresh = () => api.telegramBot().then(setStatus).catch((error) => {
    setMessage(error instanceof Error ? error.message : "Не удалось проверить CareerBot.");
  });

  useEffect(() => { void refresh(); }, []);

  async function sendTest() {
    setBusy(true);
    setMessage("");
    try {
      const result = await api.testTelegramBot();
      setMessage(result.detail || "Проверочная подборка отправлена.");
      await refresh();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "CareerBot не смог отправить подборку.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="settings-card notification-settings">
      <div>
        <span className="eyebrow">CAREERBOT · {status?.schedule || "10:00 / 18:00 МСК"}</span>
        <h2>{status?.username ? `@${status.username}` : "Telegram-подборки"}</h2>
        <p>{message || status?.detail || "Проверяем подключение…"}</p>
      </div>
      {status?.subscribed ? (
        <Button className="secondary" disabled={busy} icon="signal" onClick={() => void sendTest()}>
          {busy ? "Отправляем…" : "Проверить отправку"}
        </Button>
      ) : status?.start_url ? (
        <a className="button primary" href={status.start_url} rel="noreferrer" target="_blank">
          <Icon name="signal" size={18} /> Открыть CareerBot
        </a>
      ) : (
        <span className="status-pill pending">Подключение готовится</span>
      )}
    </section>
  );
}

function AiSearchSettings() {
  const [settings, setSettings] = useState<AiSettings | null>(null);
  const [keys, setKeys] = useState<Record<string, string>>({});
  const [models, setModels] = useState<Record<string, string>>({});
  const [clear, setClear] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  useEffect(() => {
    api.aiSettings()
      .then((next) => {
        setSettings(next);
        setModels(Object.fromEntries(next.providers.map((provider) => [provider.id, provider.model || provider.default_model])));
      })
      .catch((error) => setMessage(error instanceof Error ? error.message : "Не удалось загрузить настройки AI."));
  }, []);

  async function save() {
    if (!settings) return;
    setBusy(true);
    setMessage("");
    try {
      const saved = await api.saveAiSettings({
        enabled: settings.enabled,
        mode: settings.mode,
        max_providers: settings.max_providers,
        keys,
        models,
        clear,
      });
      setSettings(saved);
      setKeys({});
      setClear([]);
      setModels(Object.fromEntries(saved.providers.map((provider) => [provider.id, provider.model || provider.default_model])));
      setMessage("Онлайн AI-поиск сохранён. Следующий поиск использует подключённые модели.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Не удалось сохранить AI-поиск.");
    } finally {
      setBusy(false);
    }
  }

  if (!settings) {
    return <section className="settings-card"><p>Загружаем настройки онлайн AI-поиска…</p></section>;
  }
  const connected = settings.providers.filter((provider) => provider.configured).length;
  return (
    <section className="settings-card ai-settings-panel">
      <div className="section-heading compact">
        <div>
          <span className="eyebrow">ОНЛАЙН AI-ПОИСК</span>
          <h2>Несколько нейросетей для отбора вакансий</h2>
          <p>Сервис сначала собирает открытые вакансии, затем подключённые модели сверяют short-list с вашими резюме и отмечают сильные совпадения.</p>
        </div>
        <span className={`chip ${connected ? "success" : "info"}`}>{connected ? `Подключено: ${connected}` : "Ключи не добавлены"}</span>
      </div>
      <div className="profile-form-grid">
        <label className="check-row">
          <input checked={settings.enabled} onChange={(event) => setSettings({ ...settings, enabled: event.target.checked })} type="checkbox" />
          <span>Включить AI-проверку в онлайн-поиске</span>
        </label>
        <label>
          <span>Режим</span>
          <select value={settings.mode} onChange={(event) => setSettings({ ...settings, mode: event.target.value as AiSettings["mode"] })}>
            <option value="ensemble">Ensemble: несколько моделей</option>
            <option value="auto">Auto failover</option>
            <option value="vercel_gateway">Vercel AI Gateway</option>
            <option value="openai">Только OpenAI</option>
            <option value="gemini">Только Gemini</option>
            <option value="anthropic">Только Claude</option>
            <option value="groq">Только Groq</option>
            <option value="openrouter">Только OpenRouter</option>
          </select>
        </label>
        <label>
          <span>Моделей за запуск: {settings.max_providers}</span>
          <input
            className="range"
            max="5"
            min="1"
            onChange={(event) => setSettings({ ...settings, max_providers: Number(event.target.value) })}
            type="range"
            value={settings.max_providers}
          />
        </label>
      </div>
      <div className="ai-provider-grid">
        {settings.providers.map((provider) => (
          <article className={`ai-provider-card ${provider.configured ? "configured" : ""}`} key={provider.id}>
            <div>
              <strong>{provider.label}</strong>
              <span>{provider.configured ? provider.environment_configured && !provider.account_configured ? "Подключён через окружение" : "Ключ сохранён" : "Нужен API key"}</span>
            </div>
            {provider.id === "vercel_gateway" && provider.environment_configured ? (
              <span className="saved-note">Серверная авторизация Vercel активна; ключ не передаётся в браузер.</span>
            ) : (
              <label>
                <span>API key</span>
                <input
                  autoComplete="off"
                  onChange={(event) => setKeys({ ...keys, [provider.id]: event.target.value })}
                  placeholder={provider.configured ? "Оставьте пустым, чтобы не менять" : "Вставьте ключ провайдера"}
                  type="password"
                  value={keys[provider.id] || ""}
                />
              </label>
            )}
            <label>
              <span>Модель</span>
              <input
                onChange={(event) => setModels({ ...models, [provider.id]: event.target.value })}
                placeholder={provider.default_model}
                value={models[provider.id] || ""}
              />
            </label>
            {provider.account_configured && (
              <label className="check-row compact">
                <input
                  checked={clear.includes(provider.id)}
                  onChange={(event) => setClear(event.target.checked ? [...clear, provider.id] : clear.filter((id) => id !== provider.id))}
                  type="checkbox"
                />
                <span>Удалить сохранённый ключ</span>
              </label>
            )}
          </article>
        ))}
      </div>
      <div className="settings-actions">
        <Button className="primary" disabled={busy} onClick={() => void save()}>{busy ? "Сохраняем…" : "Сохранить AI-поиск"}</Button>
        {message && <span className={message.includes("Не удалось") ? "inline-error small" : "saved-note"}>{message}</span>}
      </div>
    </section>
  );
}

const SOURCE_STYLE: Record<string, { mark: string; tone: string; label: string }> = {
  "Talanto": { mark: "T", tone: "talanto", label: "Вакансии" },
  "Telegram Abroad": { mark: "✈", tone: "telegram", label: "Каналы" },
  "ITviec": { mark: "IT", tone: "habr", label: "Вьетнам · IT" },
  "VietnamWorks": { mark: "VW", tone: "remoteok", label: "Вьетнам" },
  "TopCV Vietnam": { mark: "TC", tone: "talanto", label: "Вьетнам" },
  "CareerViet": { mark: "CV", tone: "muse", label: "Вьетнам" },
  "JobsGO": { mark: "JG", tone: "jobicy", label: "Вьетнам" },
  "Glints Vietnam": { mark: "G", tone: "helloworld", label: "Вьетнам" },
  "JobStreet Vietnam": { mark: "JS", tone: "arbeitnow", label: "Вьетнам" },
  "CareerLink Vietnam": { mark: "CL", tone: "custom", label: "Вьетнам" },
  "Vieclam24h": { mark: "24", tone: "wwr", label: "Вьетнам" },
  "Timviecnhanh": { mark: "TV", tone: "telegram", label: "Вьетнам" },
  "Relocate.me": { mark: "R", tone: "relocate", label: "Релокация" },
  "Habr Career": { mark: "H", tone: "habr", label: "IT" },
  "Arbeitnow": { mark: "A", tone: "arbeitnow", label: "Европа" },
  "Remote OK": { mark: "R", tone: "remoteok", label: "Удалённо" },
  "We Work Remotely": { mark: "W", tone: "wwr", label: "Удалённо" },
  "Remotive": { mark: "M", tone: "remotive", label: "Удалённо" },
  "Jobicy": { mark: "J", tone: "jobicy", label: "Удалённо" },
  "The Muse": { mark: "M", tone: "muse", label: "Вакансии" },
  "HH.ru": { mark: "hh", tone: "hh", label: "Ссылка" },
  "LinkedIn Jobs · Vietnam": { mark: "in", tone: "habr", label: "Вьетнам" },
  "LinkedIn Jobs Vietnam": { mark: "in", tone: "habr", label: "Вьетнам" },
  "Remote.co": { mark: "R", tone: "remoteok", label: "Удалённо" },
  "Working Nomads": { mark: "WN", tone: "wwr", label: "Удалённо" },
  "Dynamite Jobs": { mark: "D", tone: "remotive", label: "Удалённо" },
  "Jobspresso": { mark: "J", tone: "jobicy", label: "Удалённо" },
  "Contra": { mark: "C", tone: "talanto", label: "Проекты" },
  "Guru": { mark: "G", tone: "custom", label: "Проекты" },
  "PeoplePerHour": { mark: "PPH", tone: "custom", label: "Проекты" },
  "Freelancer": { mark: "F", tone: "custom", label: "Проекты" },
  "Archinect": { mark: "A", tone: "arbeitnow", label: "Архитектура" },
  "Dezeen Jobs": { mark: "D", tone: "muse", label: "Дизайн" },
  "Europe Language Jobs": { mark: "EL", tone: "helloworld", label: "Европа" },
};

function SourceTile({ source }: { source: NonNullable<Dashboard["sources_list"]>[number] }) {
  const style = SOURCE_STYLE[source.name]
    || (source.name.startsWith("Telegram ·") ? { mark: "✈", tone: "telegram", label: "Публичный канал" }
      : { mark: source.name.slice(0, 1).toUpperCase(), tone: "custom", label: "Свой источник" });
  const checked = source.last_checked_at ? new Date(source.last_checked_at).toLocaleString("ru-RU", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }) : "ещё не проверен";
  const manual = /manual verified link/i.test(source.kind || "");
  const state = manual ? "Вручную" : !source.enabled ? "На паузе" : source.status === "error" ? "Ошибка" : source.status === "checked" ? "Проверен" : "Ожидает";
  const stateClass = (source.enabled && source.status !== "error") || manual ? "source-status active" : "source-status";
  return <article className="source-tile">
    <span className={`source-mark ${style.tone}`}>{style.mark}</span>
    <div className="source-copy"><div><strong>{source.name}</strong><span className={stateClass} title={source.detail || undefined}>{state}</span></div><p>{manual ? `${source.region || style.label} · проверенный каталог, добавляйте только конкретную вакансию` : source.enabled ? `${source.jobs_found || 0} вакансий · ${checked}` : "в поиск не включён"}</p></div>
    {source.url ? <a aria-label={`Открыть ${source.name}`} className="source-open" href={source.url} target="_blank" rel="noreferrer"><Icon name="external" size={16}/><span>Открыть</span></a> : null}
  </article>;
}

function SearchControlCenter({ dashboard, onChanged }: { dashboard: Dashboard | null; onChanged: () => Promise<void> }) {
  const [schedule, setSchedule] = useState<{ enabled: number; frequency: "once" | "twice"; updated_at?: string; last_run_at?: string; last_run_status?: string } | null>(null);
  const [name, setName] = useState(""); const [url, setUrl] = useState(""); const [region, setRegion] = useState(""); const [message, setMessage] = useState(""); const [busy, setBusy] = useState(false);
  useEffect(() => { api.schedule().then(setSchedule).catch(() => undefined); }, []);
  async function saveSchedule() { if (!schedule) return; setBusy(true); try { setSchedule(await api.saveSchedule({ enabled: Boolean(schedule.enabled), frequency: schedule.frequency })); setMessage("Расписание сохранено. Поиск запускается сервером, а результат появится в приложении."); } catch (e) { setMessage(e instanceof Error ? e.message : "Не удалось сохранить расписание."); } finally { setBusy(false); } }
  async function addSource() { if (!name.trim()) return; setBusy(true); try { await api.addSource({ name, url, region, kind: "manual public link" }); setName(""); setUrl(""); setRegion(""); await onChanged(); setMessage("Источник добавлен. Открытые ссылки можно проверять вручную уже сейчас; отдельный адаптер появится после проверки формата источника."); } catch (e) { setMessage(e instanceof Error ? e.message : "Не удалось добавить источник."); } finally { setBusy(false); } }
  return <>
    <section className="settings-card"><div className="section-heading compact"><div><span className="eyebrow">АВТОПОИСК</span><h2>Регулярная проверка вакансий</h2><p>Проверка выполняется в фоне по вашему профилю. Уведомления на устройство включаются отдельной кнопкой ниже.</p></div></div>
      {schedule && <div className="profile-form-grid"><label className="check-row"><input type="checkbox" checked={Boolean(schedule.enabled)} onChange={(e) => setSchedule({ ...schedule, enabled: e.target.checked ? 1 : 0 })}/><span>Включить автопоиск</span></label><label><span>Как часто</span><select value={schedule.frequency} onChange={(e) => setSchedule({ ...schedule, frequency: e.target.value as "once" | "twice" })}><option value="once">Раз в день</option><option value="twice">Дважды в день</option></select></label></div>}
      {schedule && <div className="schedule-status"><strong>{schedule.enabled ? "Автопоиск включён" : "Автопоиск на паузе"}</strong><span>{schedule.last_run_at ? `Последняя проверка: ${new Date(schedule.last_run_at).toLocaleString("ru-RU")} · ${schedule.last_run_status || "завершена"}` : "Первая серверная проверка будет показана здесь после запуска."}</span></div>}
      <div className="settings-actions"><Button className="primary" disabled={busy || !schedule} onClick={() => void saveSchedule()}>Сохранить расписание</Button></div>
    </section>
    <section className="settings-card"><div className="section-heading compact"><div><span className="eyebrow">ИСТОЧНИКИ И РЕЙТИНГИ</span><h2>Где сервис ищет вакансии</h2><p>В каталоге: {dashboard?.sources_list?.length || 0} источников. Автоматически проверяются {dashboard?.metrics.live_sources || 0} независимых публичных лент; остальные — проверенные каталоги для ручного добавления ссылок, без скрытого скрейпинга.</p></div></div>
      <div className="source-tiles">{(dashboard?.sources_list || []).map((source) => <SourceTile key={source.id} source={source}/>)}</div>
      <div className="profile-form-grid"><label><span>Новый источник</span><input value={name} onChange={(e) => setName(e.target.value)} placeholder="Название канала или сайта"/></label><label><span>Регион</span><input value={region} onChange={(e) => setRegion(e.target.value)} placeholder="Вьетнам / international"/></label><label className="wide"><span>Публичная ссылка</span><input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://…"/></label></div>
      <div className="settings-actions"><Button className="secondary" disabled={busy || !name.trim()} onClick={() => void addSource()}>Добавить источник</Button></div>
      <details><summary>Рейтинг компаний ({dashboard?.company_ratings?.length || 0})</summary><div className="table-card">{(dashboard?.company_ratings || []).map((rating) => <div className="table-row" key={rating.id}><div><strong>{rating.company}</strong><span>{rating.notes || rating.country || "Личная оценка"}</span></div><span className="chip info">{rating.rating}/100</span></div>) || null}</div></details>
    </section>
    <section className="settings-card hh-information"><div className="section-heading compact"><div><span className="eyebrow">HH.RU</span><h2>HH как проверяемый источник</h2><p>HH больше не предоставляет API-сценарий для личных кабинетов соискателей. Поэтому CareerMove не просит пароль HH и не имитирует вход.</p></div><span className="chip info">Безопасный режим</span></div><div className="hh-points"><span>✓ Переход к вакансии на HH</span><span>✓ Сохранение карточки и ссылки</span><span>✓ Отметка «я уже откликнулась»</span></div><p className="muted">Отклик на HH остаётся ручным на сайте HeadHunter — так ваша сессия и резюме остаются только у HH.</p></section>
    {message && <div className={message.includes("Не удалось") ? "inline-error" : "inline-success"}>{message}</div>}
  </>;
}

function AppearanceToolbar({
  appearance,
  onChange,
}: {
  appearance: Appearance;
  onChange: (appearance: Appearance) => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);

  async function apply(next: Appearance) {
    setBusy(true);
    try {
      await onChange(next);
    } catch {
      // Appearance is applied locally even when server synchronisation fails.
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="appearance-toolbar" aria-label="Быстрая настройка интерфейса">
      <div className="appearance-toolbar-copy">
        <Icon name="settings" size={18} />
        <div>
          <strong>Вид интерфейса</strong>
          <span>Настройте под свой экран</span>
        </div>
      </div>
      <label>
        <span>Тема</span>
        <select
          aria-label="Тема"
          disabled={busy}
          onChange={(event) => void apply({ ...appearance, theme: event.target.value as Appearance["theme"] })}
          value={appearance.theme}
        >
          <option value="system-light">Светлая</option>
          <option value="system-dark">Тёмная</option>
          <option value="cyber-aurora">Cyber Aurora</option>
        </select>
      </label>
      <label>
        <span>Текст</span>
        <select
          aria-label="Размер текста"
          disabled={busy}
          onChange={(event) => void apply({ ...appearance, font_scale: Number(event.target.value) })}
          value={appearance.font_scale}
        >
          <option value="90">90%</option>
          <option value="100">100%</option>
          <option value="110">110%</option>
          <option value="120">120%</option>
          <option value="125">125%</option>
        </select>
      </label>
      <label>
        <span>Отступы</span>
        <select
          aria-label="Плотность интерфейса"
          disabled={busy}
          onChange={(event) => void apply({ ...appearance, density: event.target.value as Appearance["density"] })}
          value={appearance.density}
        >
          <option value="auto">Авто</option>
          <option value="compact">Компактно</option>
          <option value="comfortable">Просторно</option>
        </select>
      </label>
    </section>
  );
}

function LoadingScreen() {
  return (
    <div className="loading-screen" aria-live="polite">
      <Logo />
      <span className="spinner large" />
      <p>Подключаем ваш кабинет…</p>
    </div>
  );
}

function InstallHelp({ onClose }: { onClose: () => void }) {
  return (
    <div className="modal-layer" role="presentation" onMouseDown={onClose}>
      <section className="install-modal" role="dialog" aria-modal="true" aria-labelledby="install-title" onMouseDown={(event) => event.stopPropagation()}>
        <button className="modal-close" onClick={onClose} aria-label="Закрыть">
          <Icon name="close" />
        </button>
        <div className="modal-icon"><Icon name="download" size={28} /></div>
        <h2 id="install-title">Установить CareerMove</h2>
        <div className="install-steps">
          <div><strong>iPhone / iPad</strong><span>Откройте эту страницу в Safari → нажмите «Поделиться» → «На экран Домой» → «Добавить».</span></div>
          <div><strong>macOS</strong><span>Safari → Файл → «Добавить в Dock».</span></div>
          <div><strong>Windows / Android</strong><span>Откройте меню браузера и выберите «Установить приложение».</span></div>
        </div>
        <Button className="primary full" onClick={onClose}>Понятно</Button>
      </section>
    </div>
  );
}

export default function App() {
  const query = new URLSearchParams(window.location.search);
  const requestedView = query.get("view") as View | null;
  const [view, setView] = useState<View>(NAV_ITEMS.some((item) => item.id === requestedView) ? requestedView! : "today");
  const [user, setUser] = useState<User | null>(null);
  const [localAppearance, setLocalAppearance] = useState<Appearance>(readStoredAppearance);
  const [dashboard, setDashboard] = useState<Dashboard | null>(null);
  const [loading, setLoading] = useState(Boolean(getToken()));
  const [error, setError] = useState("");
  const [toast, setToast] = useState("");
  const [serviceHealth, setServiceHealth] = useState<ServiceHealth>({ api: "checking" });
  const [mobileMenu, setMobileMenu] = useState(false);
  const [installPrompt, setInstallPrompt] = useState<InstallPrompt | null>(null);
  const [showInstallHelp, setShowInstallHelp] = useState(false);
  const [searching, setSearching] = useState(false);
  const [specialAttention, setSpecialAttention] = useState<SpecialAttention | null>(null);
  const [specialSearching, setSpecialSearching] = useState(false);
  const [addingProfile, setAddingProfile] = useState(false);
  const [editingCandidateId, setEditingCandidateId] = useState<number | null>(null);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => localStorage.getItem("careermove.sidebar.collapsed") === "1");
  const [jobQuery, setJobQuery] = useState("");
  const [jobTag, setJobTag] = useState("");
  const [jobEquipment, setJobEquipment] = useState("");
	  const [jobSchedule, setJobSchedule] = useState("");

  const appearance = user?.appearance || localAppearance;
  const goldenJobs = useMemo(
    () => (dashboard?.jobs || []).filter((job) => job.score >= MAIN_MATCH_SCORE && job.status !== "skip"),
    [dashboard],
  );
  const uniqueGoldenJobs = useMemo(() => uniqueOpportunityJobs(goldenJobs), [goldenJobs]);
  const allJobTags = useMemo(
    () => [...new Set((dashboard?.jobs || []).flatMap((job) => jobTags(job)))].sort((a, b) => a.localeCompare(b, "ru")),
    [dashboard],
  );
  const visibleJobs = useMemo(() => {
    const queryText = jobQuery.trim().toLowerCase();
    return (dashboard?.jobs || []).filter((job) => {
      if (job.status === "skip") return false;
      const searchable = [job.position, job.company, job.candidate, job.source, job.remote_location, job.salary_text].join(" ").toLowerCase();
      const hasEquipment = Boolean(job.equipment?.length);
      const hasSchedule = Boolean(job.schedule && !/не указан/i.test(job.schedule));
      return (!queryText || searchable.includes(queryText))
        && (!jobTag || jobTags(job).includes(jobTag))
        && (!jobEquipment || (jobEquipment === "provided" ? hasEquipment : !hasEquipment))
        && (!jobSchedule || (jobSchedule === "listed" ? hasSchedule : !hasSchedule));
    });
  }, [dashboard, jobQuery, jobTag, jobEquipment, jobSchedule]);
  const workJobs = useMemo(() => (dashboard?.jobs || []).filter((job) => ["in_progress", "later", "done"].includes(job.status) || Boolean(job.favorite)), [dashboard]);
  const combineJobs = useMemo(
    () => uniqueOpportunityJobs((dashboard?.jobs || []).filter((job) => job.status !== "skip" && Boolean(job.moonlight_compatible))),
    [dashboard],
  );

  useEffect(() => {
    if (!user || view !== "special-attention" || specialAttention) return;
    api.specialAttention().then(setSpecialAttention).catch((caught) => {
      if (!noteAuthWarning(caught, "Статус специальных источников временно не обновился.")) {
        setError(caught instanceof Error ? caught.message : "Не удалось загрузить специальные источники.");
      }
    });
  }, [user, view, specialAttention]);

  useEffect(() => {
    document.documentElement.dataset.theme = appearance.theme;
    document.documentElement.dataset.density = appearance.density;
    document.documentElement.style.setProperty("--font-scale", String(appearance.font_scale / 100));
    const themeColor = appearance.theme === "system-light" ? "#f7f9fc" : appearance.theme === "system-dark" ? "#0c111d" : "#07101a";
    document.querySelector('meta[name="theme-color"]')?.setAttribute("content", themeColor);
  }, [appearance]);

  useEffect(() => {
    const handler = (event: Event) => {
      event.preventDefault();
      setInstallPrompt(event as InstallPrompt);
    };
    window.addEventListener("beforeinstallprompt", handler);
    return () => window.removeEventListener("beforeinstallprompt", handler);
  }, []);

  useEffect(() => {
    if (!getToken()) {
      setLoading(false);
      setUser(null);
      setDashboard(null);
      setServiceHealth({ api: "auth", message: "Требуется вход" });
      return;
    }
    api.me()
      .then(async (nextUser) => {
        storeAppearance(nextUser.appearance);
        setLocalAppearance(nextUser.appearance);
        setUser(nextUser);
        setServiceHealth((current) => ({ ...current, api: "online", checkedAt: new Date().toISOString() }));
        try {
          const nextDashboard = await api.dashboard();
          setDashboard(nextDashboard);
          if (query.get("action") === "search") setTimeout(() => void startSearch(), 0);
        } catch (caught) {
          if (noteAuthWarning(caught, "Кабинет открыт, но данные не загрузились. Повторите обновление.")) return;
          setError(caught instanceof Error ? caught.message : "Не удалось открыть кабинет.");
        }
      })
      .catch((caught) => {
        if (handleAuthLoss(caught)) return;
        setServiceHealth({ api: "offline", message: caught instanceof Error ? caught.message : "API не отвечает" });
        setError(caught instanceof Error ? caught.message : "Не удалось открыть кабинет.");
      })
      .finally(() => setLoading(false));
    // The initial session restore must run once.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!user) return;
    let cancelled = false;
    async function refreshServiceHealth() {
      try {
        const status = await api.aiStatus();
        if (!cancelled) {
          setServiceHealth({ api: "online", ai: status, checkedAt: status.checked_at });
        }
      } catch (caught) {
        if (!cancelled) {
          if (noteAuthWarning(caught, "Проверка AI-статуса требует обновить запрос.")) return;
          setServiceHealth({
            api: "offline",
            checkedAt: new Date().toISOString(),
            message: caught instanceof Error ? caught.message : "API не отвечает",
          });
        }
      }
    }
    void refreshServiceHealth();
    const timer = window.setInterval(() => void refreshServiceHealth(), 15000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [user]);

  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(""), 2800);
    return () => window.clearTimeout(timer);
  }, [toast]);

  useEffect(() => { localStorage.setItem("careermove.sidebar.collapsed", sidebarCollapsed ? "1" : "0"); }, [sidebarCollapsed]);

  function noteAuthWarning(caught: unknown, message: string) {
    if (!(caught instanceof ApiError) || caught.status !== 401) return false;
    // Background status and dashboard polls may occasionally hit a stale
    // serverless snapshot. Keep the last confirmed service state; only the
    // initial session restore or a user action may end the visible session.
    setServiceHealth((current) => ({ ...current, message }));
    return true;
  }

  function handleAuthLoss(caught: unknown) {
    if (!(caught instanceof ApiError) || caught.status !== 401) return false;
    setToken("");
    setUser(null);
    setDashboard(null);
    setSearching(false);
    setServiceHealth({
      api: "auth",
      checkedAt: new Date().toISOString(),
      message: "Сессия завершена. Войдите снова.",
    });
    setError("Сессия завершена. Войдите снова.");
    return true;
  }

  async function loadDashboard() {
    const next = await api.dashboard();
    setDashboard(next);
  }

  async function startSearch() {
    if (searching) return;
    if (!getToken()) {
      handleAuthLoss(new ApiError("Сессия завершена. Войдите снова.", 401));
      return;
    }
    setSearching(true);
    setError("");
    try {
      let run = await api.startSearch(true);
      setDashboard((current) => current ? { ...current, search: run } : current);
      const maxSteps = Math.max(3, Number(run.result?.batch_count || 12) + 2);
      for (let attempt = 0; attempt < maxSteps && run.status === "running"; attempt += 1) {
        const continued = await api.continueSearch(run.run_id).catch(async (caught) => {
          noteAuthWarning(caught, "Один пакет источников временно не ответил; повторяю безопасно.");
          await new Promise((resolve) => window.setTimeout(resolve, 900));
          return null;
        });
        if (!continued) continue;
        run = continued;
        const streamed = await api.dashboard().catch((caught) => {
          noteAuthWarning(caught, "Карточки сохранены; обновление экрана повторится после следующего пакета.");
          return null;
        });
        setDashboard((current) => streamed ? { ...streamed, search: run } : current ? { ...current, search: run } : current);
      }
      if (run.status === "completed") {
        await api.syncApplicationTracker().catch(() => null);
        await loadDashboard();
        setToast("Подборка, подработки, стажировки и таблица обновлены");
        return;
      }
      if (run.status === "failed") {
        setError(run.detail || "Поиск остановился на одном из пакетов. Уже найденные карточки сохранены.");
        return;
      }
      const latest = await api.searchStatus(run.run_id).catch((caught) => {
        noteAuthWarning(caught, "Статус поиска временно требует повторить запрос.");
        return null;
      });
      if (latest && latest.status !== "completed" && latest.status !== "failed") {
        setDashboard((current) => current ? { ...current, search: latest } : current);
        setToast("Часть источников обновлена; повторное нажатие продолжит этот поиск");
      }
    } catch (caught) {
      if (handleAuthLoss(caught)) return;
      setError(caught instanceof Error ? caught.message : "Не удалось запустить поиск.");
    } finally {
      setSearching(false);
    }
  }

  async function searchSpecial() {
    if (specialSearching) return;
    setSpecialSearching(true);
    setError("");
    try {
      const result = await api.searchSpecialAttention();
      setSpecialAttention(result);
      await loadDashboard();
      setToast("Три специальных источника проверены");
    } catch (caught) {
      if (handleAuthLoss(caught)) return;
      setError(caught instanceof Error ? caught.message : "Не удалось проверить специальные источники.");
    } finally {
      setSpecialSearching(false);
    }
  }

  async function setJobStatus(id: number, status?: string, favorite?: boolean) {
    const previous = dashboard;
    const previousSpecial = specialAttention;
    setDashboard((current) => current ? {
      ...current,
      jobs: current.jobs.map((job) => job.id === id ? { ...job, ...(status ? { status } : {}), ...(favorite === undefined ? {} : { favorite: favorite ? 1 : 0 }) } : job),
    } : current);
    setSpecialAttention((current) => current ? {
      ...current,
      jobs: current.jobs.map((job) => job.id === id ? { ...job, ...(status ? { status } : {}), ...(favorite === undefined ? {} : { favorite: favorite ? 1 : 0 }) } : job),
    } : current);
    try {
      await api.jobStatus(id, status, favorite);
      setToast(favorite !== undefined ? (favorite ? "Добавлено в избранное" : "Убрано из избранного") : status === "in_progress" ? "Вакансия перенесена в работу и добавлена в реестр" : status === "done" ? "Вакансия отмечена выполненной" : status === "approved" ? "Вакансия одобрена" : status === "later" ? "Сохранено на потом" : "Вакансия скрыта");
    } catch (caught) {
      setDashboard(previous);
      setSpecialAttention(previousSpecial);
      throw caught;
    }
  }

  async function saveAppearance(next: Appearance) {
    storeAppearance(next);
    setLocalAppearance(next);
    setUser((current) => current ? { ...current, appearance: next } : current);
    try {
      const saved = await api.appearance(next);
      storeAppearance(saved);
      setLocalAppearance(saved);
      setUser((current) => current ? { ...current, appearance: saved } : current);
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 401) {
        noteAuthWarning(caught, "Тема сохранена на этом устройстве; синхронизация с аккаунтом временно недоступна.");
      }
      setToast("Тема применена на этом устройстве");
    }
  }

  async function install() {
    if (installPrompt) {
      await installPrompt.prompt();
      await installPrompt.userChoice;
      setInstallPrompt(null);
    } else {
      setShowInstallHelp(true);
    }
  }

  async function logout() {
    try {
      await api.logout();
    } catch {
      // Local logout must still complete when the network is unavailable.
    }
    setToken("");
    setUser(null);
    setDashboard(null);
  }

  if (loading) return <LoadingScreen />;
  if (!user) {
    return (
      <>
        <AuthScreen
          onInstall={() => void install()}
          onAuthenticated={(nextUser) => {
            storeAppearance(nextUser.appearance);
            setLocalAppearance(nextUser.appearance);
            setUser(nextUser);
            setError("");
            setLoading(true);
	            api.dashboard()
	              .then(setDashboard)
	              .catch((caught) => {
	                if (!noteAuthWarning(caught, "Вход выполнен, но кабинет не загрузился. Нажмите «Обновить».")) setError(caught.message);
	              })
              .finally(() => setLoading(false));
          }}
        />
        {showInstallHelp && <InstallHelp onClose={() => setShowInstallHelp(false)} />}
        {error && <div className="global-toast error" role="alert">{error}</div>}
      </>
    );
  }

  return (
    <div className={`app-shell ${sidebarCollapsed ? "sidebar-collapsed" : ""}`}>
      <aside className={`sidebar ${mobileMenu ? "open" : ""} ${sidebarCollapsed ? "collapsed" : ""}`}>
        <div className="sidebar-head">
          <Logo />
          <button className="sidebar-toggle" onClick={() => setSidebarCollapsed((value) => !value)} aria-label={sidebarCollapsed ? "Развернуть меню" : "Свернуть меню"} title={sidebarCollapsed ? "Развернуть меню" : "Свернуть меню"}>
            <Icon name="menu" />
          </button>
          <button className="mobile-close" onClick={() => setMobileMenu(false)} aria-label="Закрыть меню">
            <Icon name="close" />
          </button>
        </div>
        <nav className="side-nav" aria-label="Основная навигация">
          {NAV_ITEMS.map((item) => (
            <button
              aria-current={view === item.id ? "page" : undefined}
              key={item.id}
              onClick={() => {
                setView(item.id);
                setMobileMenu(false);
              }}
            >
              <Icon name={item.icon} size={19} />
              <span>{item.label}</span>
              {item.id === "jobs" && dashboard?.metrics.golden ? <b>{dashboard.metrics.golden}</b> : null}
              {item.id === "special-attention" && specialAttention?.jobs.length ? <b>{specialAttention.jobs.length}</b> : null}
              {item.id === "gigs" && dashboard?.metrics.gigs ? <b>{dashboard.metrics.gigs}</b> : null}
              {item.id === "internships" && dashboard?.metrics.internships ? <b>{dashboard.metrics.internships}</b> : null}
              {item.id === "combine" && dashboard?.metrics.combine ? <b>{dashboard.metrics.combine}</b> : null}
            </button>
          ))}
        </nav>
        <div className="sidebar-foot">
          <div className="account-mini">
            <span>{user.email.slice(0, 1).toUpperCase()}</span>
            <div><strong>{user.email.split("@")[0]}</strong><small>{user.email}</small></div>
          </div>
          <Button className="ghost full" onClick={logout}>Выйти</Button>
        </div>
      </aside>

      {mobileMenu && <button className="scrim" onClick={() => setMobileMenu(false)} aria-label="Закрыть меню" />}

      <div className="workspace">
        <header className="topbar">
          <button className="menu-button" onClick={() => setMobileMenu(true)} aria-label="Открыть меню">
            <Icon name="menu" />
          </button>
          <div className="topbar-title">
            <strong>{NAV_ITEMS.find((item) => item.id === view)?.label}</strong>
            <ServiceBadge health={serviceHealth} />
          </div>
          <div className="topbar-actions">
            <Button className="secondary install-button" icon="download" onClick={install}>Установить</Button>
            <Button className="primary" disabled={view === "special-attention" ? specialSearching : searching} icon="refresh" onClick={() => void (view === "special-attention" ? searchSpecial() : startSearch())}>
              {(view === "special-attention" ? specialSearching : searching) ? "Ищем…" : "Обновить"}
            </Button>
          </div>
        </header>

        <main className="content">
          {error && (
            <div className="page-error" role="alert">
              <div><strong>Не удалось выполнить действие</strong><span>{error}</span></div>
              <button onClick={() => setError("")} aria-label="Закрыть"><Icon name="close" /></button>
            </div>
          )}

          {view === "today" && (
            <div className="page-stack">
              <AppearanceToolbar
                appearance={appearance}
                onChange={async (next) => {
                  try {
                    await saveAppearance(next);
                  } catch (caught) {
                    setError(caught instanceof Error ? caught.message : "Не удалось сохранить оформление.");
                  }
                }}
              />
              <section className="hero-panel">
                <div className="hero-copy">
                  <span className="eyebrow">CAREERMOVE · DAILY SIGNAL</span>
                  <h1>Только подходящие вакансии — без шума</h1>
                  <p>
                    Строгая география, реальный опыт каждого кандидата и ручное решение
                    перед отправкой отклика.
                  </p>
                  <div className="hero-actions">
                    <Button className="primary" disabled={searching} icon="refresh" onClick={() => void startSearch()}>
                      {searching ? "Подборка обновляется" : "Обновить подборку"}
                    </Button>
                    <Button className="secondary" icon="download" onClick={install}>Установить приложение</Button>
                  </div>
                </div>
                <div className="signal-card" aria-hidden="true">
                  {[44, 72, 56, 88, 64, 48, 76, 92, 58].map((height, index) => (
                    <i key={index} style={{ height: `${height}%` }} />
                  ))}
                </div>
              </section>

              <WorkflowGuide onNavigate={(next) => setView(next)} />

              <div className="network-note">
                <Icon name="shield" size={18} />
                <span><strong>Серверный поиск:</strong> {dashboard?.network || "подключение проверяется"}. VPN пользователю не нужен.</span>
              </div>

              <section className="metrics-grid" aria-label="Статистика">
                <MetricCard label="Найдено" value={dashboard?.metrics.found || 0} note="актуальных карточек" tone="blue" />
                <MetricCard label="Подходящих" value={dashboard?.metrics.golden || 0} note="совпадение от 60%" tone="violet" />
                <MetricCard label="Отправлено" value={dashboard?.metrics.sent || 0} note="подтверждено вручную" tone="teal" />
                <MetricCard label="Источников" value={dashboard?.metrics.live_sources || 0} note={`автолент · ${dashboard?.metrics.sources || 0} в каталоге`} tone="neutral" />
              </section>

              <SearchStatus run={dashboard?.search || null} />

              <section>
                <div className="section-heading">
                  <div>
                    <span className="eyebrow">ГЛАВНЫЕ СОВПАДЕНИЯ</span>
                    <h2>Вакансии 60%+</h2>
                    <p>Сначала проверяйте эти карточки. Роли 45-59% остаются во вкладке вакансий как review-кандидаты.</p>
                  </div>
                  <button className="text-button" onClick={() => setView("jobs")}>Все вакансии <span>→</span></button>
                </div>
                {uniqueGoldenJobs.length ? (
                  <div className="jobs-grid">
                    {uniqueGoldenJobs.map((job) => <JobCard job={job} key={`unique-${job.id}`} onStatus={setJobStatus} />)}
                  </div>
                ) : (
                  <EmptyState
                    title="Сегодня вакансий 60%+ пока нет"
                    text="Запустите обновление. Строгий фильтр лучше пустого списка нерелевантных ролей."
                    action={<Button className="primary" icon="refresh" onClick={() => void startSearch()}>Запустить поиск</Button>}
                  />
                )}
              </section>
            </div>
          )}

          {view === "jobs" && (
            <div className="page-stack">
              <div className="section-heading page-title">
                <div>
                  <span className="eyebrow">КАРТОЧКИ</span>
                  <h1>Подходящие вакансии</h1>
                  <p>Действия меняют только выбранную карточку — страница не перезагружается.</p>
                </div>
                <Button className="primary" disabled={searching} icon="refresh" onClick={() => void startSearch()}>
                  {searching ? "Обновляем…" : "Новый поиск"}
                </Button>
              </div>
              <SearchStatus run={dashboard?.search || null} />
              <section className="job-filter-panel" aria-label="Фильтры вакансий">
                <div><span className="eyebrow">ФИЛЬТРЫ</span><h2>Покажите нужное</h2><p>Например: «Manual QA», «Дананг», «Вьетнам» или выберите тег.</p></div>
                <div className="job-filter-controls">
                  <label><span>Поиск</span><input value={jobQuery} onChange={(event) => setJobQuery(event.target.value)} placeholder="Должность, компания, город" /></label>
                  <label><span>Тег</span><select value={jobTag} onChange={(event) => setJobTag(event.target.value)}><option value="">Все теги</option>{allJobTags.map((tag) => <option key={tag} value={tag}>{tag}</option>)}</select></label>
                  <label><span>Техника</span><select value={jobEquipment} onChange={(event) => setJobEquipment(event.target.value)}><option value="">Любая</option><option value="provided">Указано, что выдают</option><option value="missing">Не указана</option></select></label>
                  <label><span>График</span><select value={jobSchedule} onChange={(event) => setJobSchedule(event.target.value)}><option value="">Любой</option><option value="listed">Указан</option><option value="missing">Не указан</option></select></label>
                  <Button className="ghost" disabled={!jobQuery && !jobTag && !jobEquipment && !jobSchedule} onClick={() => { setJobQuery(""); setJobTag(""); setJobEquipment(""); setJobSchedule(""); }}>Сбросить</Button>
                </div>
              </section>
              <ManualVacancyForm candidates={dashboard?.candidates || []} onAdded={loadDashboard} />
              <VacancyCleanup onChanged={loadDashboard} />
              {visibleJobs.length ? (
                <div className="jobs-grid">
                  {visibleJobs.map((job) => <JobCard job={job} key={job.id} onStatus={setJobStatus} />)}
                </div>
              ) : (
                <EmptyState title="Карточек пока нет" text="Добавьте профиль и запустите поиск подходящих вакансий." />
              )}
            </div>
          )}

          {view === "special-attention" && (
            <SpecialAttentionPage data={specialAttention} loading={specialSearching} onSearch={searchSpecial} onStatus={setJobStatus}/>
          )}

          {view === "gigs" && (
            <GigsPage candidates={dashboard?.candidates || []} gigs={dashboard?.gigs || []} onChanged={loadDashboard} />
          )}

          {view === "internships" && (
            <InternshipsPage candidates={dashboard?.candidates || []} internships={dashboard?.internships || []} onChanged={loadDashboard} />
          )}

          {view === "combine" && (
            <div className="page-stack">
              <div className="section-heading page-title">
                <div>
                  <span className="eyebrow">ДЛЯ СОВМЕЩЕНИЯ</span>
                  <h1>Вакансии, которые можно совмещать</h1>
                  <p>Сюда дублируются только карточки с явным сигналом: part-time, freelance, contract, flexible/asynchronous или no exclusivity.</p>
                </div>
                <Button className="primary" disabled={searching} icon="refresh" onClick={() => void startSearch()}>{searching ? "Обновляем…" : "Обновить"}</Button>
              </div>
              {combineJobs.length ? (
                <div className="jobs-grid">
                  {combineJobs.map((job) => <JobCard job={job} key={`combine-${job.id}`} onStatus={setJobStatus} />)}
                </div>
              ) : (
                <EmptyState title="Совместимых вакансий пока нет" text="Сервис не считает молчание работодателя разрешением на совмещение. Нужна явная формулировка в вакансии." />
              )}
            </div>
          )}

          {view === "higher-education" && (
            <HigherEducationPage
              applicantResources={dashboard?.applicant_resources || []}
              guide={dashboard?.education_application_guide || []}
              options={dashboard?.higher_education_options || []}
              relocationResources={dashboard?.relocation_resources || []}
            />
          )}

          {view === "ai-chat" && (
            <AiChatPage health={serviceHealth} dashboard={dashboard} />
          )}

          {view === "education" && (
            <EducationPage items={dashboard?.education_recommendations || []} />
          )}

          {view === "work" && (
            <div className="page-stack">
              <div className="section-heading page-title"><div><span className="eyebrow">МОЯ ВОРОНКА</span><h1>Вакансии в работе</h1><p>Здесь только выбранные, отложенные, завершённые и избранные карточки. Отправки остаются под вашим контролем.</p></div><Button className="secondary" onClick={() => setView("jobs")}>Вернуться к каталогу</Button></div>
              {workJobs.length ? <div className="jobs-grid">{workJobs.map((job) => <JobCard job={job} key={job.id} onStatus={setJobStatus} inWork />)}</div> : <EmptyState title="Пока ничего не взято в работу" text="В каталоге нажмите «Взять в работу» или добавьте вакансию в избранное." action={<Button className="primary" onClick={() => setView("jobs")}>Открыть вакансии</Button>} />}
            </div>
          )}

          {view === "profiles" && (
            <div className="page-stack">
              <div className="section-heading page-title">
                <div>
                  <span className="eyebrow">КАНДИДАТЫ И РЕЗЮМЕ</span>
                  <h1>Профили</h1>
                  <p>Каждая вакансия оценивается отдельно для каждого опыта.</p>
                </div>
                {dashboard?.candidates.length ? <Button className="secondary" onClick={() => { setAddingProfile((value) => !value); setEditingCandidateId(null); }}>{addingProfile ? "Отменить" : "Добавить ещё"}</Button> : null}
              </div>
              {(!dashboard?.candidates.length || addingProfile || editingCandidateId !== null) && <CandidateEditor
                candidate={addingProfile ? undefined : dashboard?.candidates.find((item) => item.id === editingCandidateId) || dashboard?.candidates[0]}
                onSaved={() => { void loadDashboard(); setToast("Профиль сохранён"); setAddingProfile(false); setEditingCandidateId(null); }}
              />}
              {dashboard?.candidates.length ? (
                <>
                  <CareerAssistantSetup candidates={dashboard.candidates} onChanged={loadDashboard} />
                  <div className="candidate-grid">{dashboard.candidates.map((candidate) => <CandidateCard candidate={candidate} key={candidate.id} onEdit={() => { setAddingProfile(false); setEditingCandidateId(candidate.id); window.scrollTo({ top: 0, behavior: "smooth" }); }} />)}</div>
                  {dashboard.candidates.map((candidate) => <ResumeStudio candidate={candidate} key={`resume-${candidate.id}`} onChanged={loadDashboard} />)}
                </>
              ) : (
                <EmptyState title="Создайте первый профиль" text="Заполните форму выше: вакансия будет оцениваться по этому опыту отдельно." />
              )}
            </div>
          )}

          {view === "applications" && (
            <div className="page-stack">
              <div className="section-heading page-title">
                <div>
                  <span className="eyebrow">РУЧНАЯ ОТПРАВКА</span>
                  <h1>Отклики</h1>
                  <p>Готово, отправлено, ошибка или дубликат — статусы остаются прозрачными.</p>
                </div>
              </div>
              <ApplicationTrackerPanel />
              <ApplicationStudio jobs={dashboard?.jobs || []} applications={dashboard?.applications || []} onChanged={loadDashboard} />
              <ApplicationsList applications={dashboard?.applications || []} />
            </div>
          )}

          {view === "settings" && (
            <div className="page-stack">
              <div className="section-heading page-title">
                <div>
                  <span className="eyebrow">ПЕРСОНАЛИЗАЦИЯ</span>
                  <h1>Настройки</h1>
                  <p>Только необходимые параметры. Остальные инструменты не мешают основному сценарию.</p>
                </div>
              </div>
              <AppearanceSettings appearance={appearance} onSave={saveAppearance} />
              <AiSearchSettings />
              <SearchControlCenter dashboard={dashboard} onChanged={loadDashboard} />
              <CareerBotSettings />
              <NotificationSettings />
              <section className="settings-card install-settings">
                <div>
                  <span className="eyebrow">ПРИЛОЖЕНИЕ</span>
                  <h2>Установить CareerMove</h2>
                  <p>После установки CareerMove открывается отдельным окном и доступен из Dock или с домашнего экрана.</p>
                </div>
                <Button className="primary" icon="download" onClick={install}>Показать установку</Button>
              </section>
            </div>
          )}
        </main>
      </div>

      {showInstallHelp && <InstallHelp onClose={() => setShowInstallHelp(false)} />}

      {toast && <div className="global-toast" role="status"><Icon name="check" size={18} /> {toast}</div>}
    </div>
  );
}
