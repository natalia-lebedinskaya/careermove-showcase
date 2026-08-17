import type { AiChatResponse, AiServiceStatus, AiSettings, Appearance, ApplicationPreferences, ApplicationTracker, Candidate, CandidateInput, Certificate, Dashboard, Gig, SearchRun, SpecialAttention, TelegramBotStatus, TrackerRow, User } from "./types";
import { getApiTargets } from "./apiConfig";

const configuredApiUrl = String(import.meta.env.VITE_API_URL || "");
const TOKEN_KEY = "careermove.pwa.session";
const API_URLS = getApiTargets(configuredApiUrl, import.meta.env.PROD);

export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

export function getToken() {
  return localStorage.getItem(TOKEN_KEY) || "";
}

export function setToken(token: string) {
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

export type ApplicationDraft = {
  vacancy_id?: number;
  gig_id?: number;
  candidate_id: number;
  company: string;
  position: string;
  recipient_email: string;
  recipient_contact?: string;
  contact_label?: string;
  delivery_channel?: "email" | "telegram" | "manual";
  subject: string;
  cover_letter: string;
  vacancy_language?: "RU" | "EN";
  resume_guidance?: string;
  from_email?: string;
  manual_review?: boolean;
  resume: { id: number; title: string; language: string; content: string } | null;
};

async function request<T>(
  path: string,
  options: RequestInit = {},
  authenticated = true,
): Promise<T> {
  const headers = new Headers(options.headers);
  if (options.body && !(options.body instanceof FormData)) headers.set("Content-Type", "application/json");
  const token = authenticated ? getToken() : "";
  if (authenticated && !token) throw new ApiError("Требуется вход.", 401);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const retryable = (
    !options.method
    || options.method === "GET"
    || ["/api/auth/login", "/api/auth/register", "/api/auth/password/forgot"].includes(path)
    || API_URLS.length > 1
  );
  let lastError: unknown;
  for (let attempt = 0; attempt < API_URLS.length; attempt += 1) {
    const baseUrl = API_URLS[attempt];
    const target = `${baseUrl}${path}`;
    const sameTargetAttempts = authenticated ? 3 : 2;
    for (let requestAttempt = 0; requestAttempt < sameTargetAttempts; requestAttempt += 1) {
      let response: Response;
      try {
        response = await fetch(target, { ...options, headers });
      } catch (error) {
        lastError = error;
        if (requestAttempt < sameTargetAttempts - 1) {
          await new Promise((resolve) => window.setTimeout(resolve, 300 * (requestAttempt + 1)));
          continue;
        }
        if (!retryable || attempt >= API_URLS.length - 1) {
          throw new ApiError(
            "Сервер временно недоступен. Проверьте соединение и повторите через несколько секунд.",
            0,
          );
        }
        break;
      }
      if (response.status === 204) return undefined as T;
      const contentType = response.headers.get("Content-Type") || "";
      const isJson = contentType.includes("application/json");
      const data = isJson ? await response.json().catch(() => ({})) : {};
      if (response.ok && !isJson) {
        if (retryable && attempt < API_URLS.length - 1) break;
        throw new ApiError("Сервис вернул некорректный ответ.", response.status);
      }
      if (response.ok) return data as T;
      const transientStatus = [401, 408, 425, 429, 500, 502, 503, 504].includes(response.status);
      if (transientStatus && requestAttempt < sameTargetAttempts - 1) {
        await new Promise((resolve) => window.setTimeout(resolve, 300 * (requestAttempt + 1)));
        continue;
      }
      const shouldRetry = retryable && attempt < API_URLS.length - 1 && ([404, 502, 503, 504].includes(response.status) || response.status === 401);
      if (!shouldRetry) {
        const message = typeof data.detail === "string" ? data.detail : "Сервис временно недоступен.";
        throw new ApiError(message, response.status);
      }
      break;
    }
  }
  throw new ApiError(
    lastError instanceof Error && lastError.message
      ? lastError.message
      : "Сервер временно недоступен. Проверьте соединение и повторите через несколько секунд.",
    0,
  );
}

export const api = {
  login: (email: string, password: string) =>
    request<{ token?: string; user?: User; requires_totp?: boolean; challenge?: string }>(
      "/api/auth/login",
      { method: "POST", body: JSON.stringify({ email, password }) },
      false,
    ),
  register: (email: string, password: string, privacy_accepted: boolean) =>
    request<{ token: string; user: User }>(
      "/api/auth/register",
      { method: "POST", body: JSON.stringify({ email, password, privacy_accepted }) },
      false,
    ),
  forgotPassword: (email: string) =>
    request<{ message: string; reset_url?: string; delivery?: string; delivery_detail?: string }>(
      "/api/auth/password/forgot",
      { method: "POST", body: JSON.stringify({ email }) },
      false,
    ),
  resetPassword: (token: string, password: string) =>
    request<{ message: string }>(
      "/api/auth/password/reset",
      { method: "POST", body: JSON.stringify({ token, password }) },
      false,
    ),
  totp: (challenge: string, code: string) =>
    request<{ token: string; user: User }>(
      "/api/auth/totp",
      { method: "POST", body: JSON.stringify({ challenge, code }) },
      false,
    ),
  logout: () => request<void>("/api/auth/logout", { method: "POST" }),
  me: () => request<User>("/api/me"),
  aiStatus: () => request<AiServiceStatus>("/api/ai/status"),
  aiSettings: () => request<AiSettings>("/api/ai/settings"),
  saveAiSettings: (payload: { enabled: boolean; mode: AiSettings["mode"]; max_providers: number; keys: Record<string, string>; models: Record<string, string>; clear: string[] }) =>
    request<AiSettings>("/api/ai/settings", { method: "PUT", body: JSON.stringify(payload) }),
  aiChat: (message: string) =>
    request<AiChatResponse>("/api/ai/chat", { method: "POST", body: JSON.stringify({ message }) }),
  dashboard: () => request<Dashboard>("/api/dashboard"),
  createCandidate: (candidate: CandidateInput) =>
    request<Candidate>("/api/candidates", { method: "POST", body: JSON.stringify(candidate) }),
  updateCandidate: (id: number, candidate: CandidateInput) =>
    request<Candidate>(`/api/candidates/${id}`, { method: "PATCH", body: JSON.stringify(candidate) }),
  applySerbiaPreset: (id: number, kind: "qa_candidate" | "support_candidate") =>
    request<Candidate>(`/api/candidates/${id}/serbia-preset`, { method: "POST", body: JSON.stringify({ kind }) }),
  applyVietnamPreset: (id: number, kind: "qa_candidate" | "support_candidate") =>
    request<Candidate>(`/api/candidates/${id}/vietnam-preset`, { method: "POST", body: JSON.stringify({ kind }) }),
  saveResume: (candidateId: number, payload: { title: string; language: "RU" | "EN" | "SR" | "OTHER"; content: string }) =>
    request<{ ok: boolean }>(`/api/candidates/${candidateId}/resumes`, { method: "POST", body: JSON.stringify(payload) }),
  transformResume: (payload: { candidate_id: number; content: string; language: "RU" | "EN" | "SR" | "OTHER" }) =>
    request<{ content: string; message: string }>("/api/resumes/transform", { method: "POST", body: JSON.stringify(payload) }),
  uploadPhoto: (candidateId: number, file: File, language: "RU" | "EN" | "SR" | "OTHER") => {
    const form = new FormData();
    form.append("photo", file);
    form.append("language", language);
    return request<{ ok: boolean; photo_data: string }>(`/api/candidates/${candidateId}/photo`, { method: "POST", body: form });
  },
  prepareApplications: (vacancyIds: number[]) =>
    request<{ prepared: number[]; sent: number; message: string }>("/api/applications/prepare", { method: "POST", body: JSON.stringify({ vacancy_ids: vacancyIds }) }),
  applicationPreferences: () => request<ApplicationPreferences>("/api/application-settings"),
  saveApplicationPreferences: (payload: Omit<ApplicationPreferences, "daily_limit" | "pro_enabled" | "prepared_today">) =>
    request<ApplicationPreferences>("/api/application-settings", { method: "PUT", body: JSON.stringify(payload) }),
  activateProCode: (code: string) => request<ApplicationPreferences>("/api/application-settings/pro-code", { method: "POST", body: JSON.stringify({ code }) }),
  composeApplication: (vacancyId: number, overrides: { tone?: "formal" | "friendly"; length?: "compact" | "detailed"; include_salary?: boolean } = {}) => request<ApplicationDraft>("/api/applications/compose", { method: "POST", body: JSON.stringify({ vacancy_id: vacancyId, ...overrides }) }),
  composeGigApplication: (gigId: number, overrides: { tone?: "formal" | "friendly"; length?: "compact" | "detailed" } = {}) => request<ApplicationDraft>(`/api/gigs/${gigId}/compose`, { method: "POST", body: JSON.stringify(overrides) }),
  addCertificate: (candidateId: number, payload: Omit<Certificate, "id">) =>
    request<Certificate>(`/api/candidates/${candidateId}/certificates`, { method: "POST", body: JSON.stringify(payload) }),
  deleteCertificate: (candidateId: number, certificateId: number) =>
    request<void>(`/api/candidates/${candidateId}/certificates/${certificateId}`, { method: "DELETE" }),
  appearance: (appearance: Appearance) =>
    request<Appearance>("/api/settings/appearance", {
      method: "PATCH",
      body: JSON.stringify({
        theme: appearance.theme,
        font_scale: appearance.font_scale,
        density: appearance.density,
      }),
    }),
  pushConfig: () =>
    request<{ enabled: boolean; public_key: string }>("/api/push/config"),
  pushSubscribe: (subscription: {
    endpoint: string;
    keys: { p256dh: string; auth: string };
  }) =>
    request<{ ok: boolean }>("/api/push/subscribe", {
      method: "POST",
      body: JSON.stringify(subscription),
    }),
  startSearch: (use_ai = true) =>
    request<SearchRun>("/api/search", {
      method: "POST",
      body: JSON.stringify({ force: true, use_ai }),
    }),
  continueSearch: (runId: string) =>
    request<SearchRun>(`/api/search/${runId}/continue`, { method: "POST" }),
  searchStatus: (runId: string) => request<SearchRun>(`/api/search/${runId}`),
  specialAttention: () => request<SpecialAttention>("/api/special-attention"),
  searchSpecialAttention: () => request<SpecialAttention>("/api/special-attention/search", { method: "POST" }),
  applicationTracker: () => request<ApplicationTracker>("/api/application-tracker"),
  updateTracker: (id: number, payload: Partial<Pick<TrackerRow, "response_at" | "result" | "comments" | "salary_range">>) =>
    request<{ row: TrackerRow; sync: { status: string; detail: string } }>(`/api/application-tracker/${id}`, { method: "PATCH", body: JSON.stringify(payload) }),
  saveGoogleSheets: (payload: { spreadsheet_url: string; webhook_url: string; webhook_secret: string }) =>
    request<ApplicationTracker["google_sheets"]>("/api/application-tracker/google-sheets", { method: "PUT", body: JSON.stringify(payload) }),
  syncApplicationTracker: () => request<{ results: Array<{ id: number; status: string; detail: string }>; synced: number; snapshot?: { status: string; rows: number; detail?: string }; pulled?: { status: string; updated: number; detail?: string } }>("/api/application-tracker/sync", { method: "POST" }),
  jobStatus: (id: number, status?: string, favorite?: boolean) =>
    request<{ id: number; status: string; favorite?: number }>(`/api/jobs/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ status, favorite }),
    }),
  jobPreview: (id: number) => request<{ id: number; company: string; position: string; source: string; link: string; remote_location: string; salary_text: string; posted_at: string; active_checked_at: string; is_active: number; preview: { description?: string; tags?: string[]; benefits?: string[] }; links: Array<{ url: string; source: string }>; contacts?: { emails?: string[]; phones?: string[]; telegram?: string[] }; equipment?: string[]; benefits?: string[]; schedule?: string; sector?: string; risk?: string }>(`/api/jobs/${id}/preview`),
  sources: () => request<Array<{ id: number; name: string; kind: string; region: string; url: string; enabled: number; notes?: string; status?: "checked" | "error" | "pending" | "paused"; last_checked_at?: string; jobs_found?: number; detail?: string }>>("/api/sources"),
  addSource: (payload: { name: string; kind?: string; region?: string; url?: string; notes?: string; enabled?: boolean }) => request("/api/sources", { method: "POST", body: JSON.stringify(payload) }),
  updateSource: (id: number, payload: { enabled?: boolean; notes?: string }) => request(`/api/sources/${id}`, { method: "PATCH", body: JSON.stringify(payload) }),
  schedule: () => request<{ enabled: number; frequency: "once" | "twice"; updated_at?: string; last_run_at?: string; last_run_status?: string }>("/api/search-schedule"),
  saveSchedule: (payload: { enabled: boolean; frequency: "once" | "twice" }) => request<{ enabled: number; frequency: "once" | "twice"; updated_at?: string; last_run_at?: string; last_run_status?: string }>("/api/search-schedule", { method: "PUT", body: JSON.stringify(payload) }),
  telegramBot: () => request<TelegramBotStatus>("/api/telegram-bot"),
  testTelegramBot: () => request<{ channel: string; status: string; detail: string }>("/api/telegram-bot/test", { method: "POST" }),
  hhStatus: () => request<{ configured: boolean; connected: boolean; detail: string }>("/api/hh/status"),
  hhConnect: () => request<{ url: string }>("/api/hh/connect"),
  cleanupVacancies: (mode: "inactive" | "ignored" | "all" | "reset") => request<{ archived: number; message: string }>("/api/vacancies/cleanup", { method: "POST", body: JSON.stringify({ mode }) }),
  addManualVacancy: (payload: { candidate_id: number; company: string; position: string; link: string; location?: string; source?: string; posted_at?: string; salary_text?: string; description?: string }) => request<{ ok: boolean; score: number }>("/api/vacancies/manual", { method: "POST", body: JSON.stringify(payload) }),
  collectGigs: () => request<{ saved: number; checked: number; message: string }>("/api/gigs/collect", { method: "POST", body: JSON.stringify({}) }),
  collectInternships: () => request<{ saved: number; checked: number; message: string }>("/api/internships/collect", { method: "POST", body: JSON.stringify({}) }),
  gigStatus: (id: number, status?: string, favorite?: boolean) =>
    request<{ id: number; status: string; favorite?: number }>(`/api/gigs/${id}`, { method: "PATCH", body: JSON.stringify({ status, favorite }) }),
  addManualGig: (payload: { candidate_id: number; title: string; client: string; link: string; category: string; work_format?: string; location?: string; pay_text?: string; posted_at?: string; description?: string }) =>
    request<{ ok: boolean; score: number }>("/api/gigs/manual", { method: "POST", body: JSON.stringify(payload) }),
};
