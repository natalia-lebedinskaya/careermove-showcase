export type Theme = "system-light" | "system-dark" | "cyber-aurora";
export type Density = "auto" | "compact" | "comfortable";
export type View = "today" | "jobs" | "special-attention" | "gigs" | "internships" | "combine" | "higher-education" | "ai-chat" | "education" | "work" | "profiles" | "applications" | "settings";

export interface Appearance {
  theme: Theme;
  font_scale: number;
  density: Density;
}

export interface User {
  id: number;
  email: string;
  appearance: Appearance;
}

export interface Metrics {
  found: number;
  golden: number;
  gigs?: number;
  internships?: number;
  combine?: number;
  sent: number;
  candidates: number;
  sources: number;
  live_sources?: number;
}

export interface Candidate {
  id: number;
  name: string;
  target_title: string;
  english_level: string;
  desired_countries: string;
  salary_min: number;
  resume_count: number;
  notes?: string;
  hard_exclude?: string;
  hard_require?: string;
  preferred_regions?: string;
  preferred_cities?: string;
  preferred_companies?: string;
  priority_titles?: string;
  contact_email?: string;
  cover_tone?: "formal" | "friendly" | "";
  cover_length?: "compact" | "detailed" | "";
  manual_review?: number;
  skills?: string[];
  photo_data?: string;
  resumes?: Array<{ id: number; title: string; language: string; content: string; photo_data?: string; updated_at?: string }>;
  certificates?: Certificate[];
}

export interface Certificate { id: number; title: string; issuer?: string; credential_url?: string; issued_at?: string; notes?: string; include_in_resume?: number; }
export interface ApplicationPreferences {
  tone: "formal" | "friendly"; length: "compact" | "detailed"; include_certificates: boolean; include_achievements: boolean;
  from_email: string; daily_limit: number; pro_enabled: boolean; prepared_today: number;
}

export interface AiServiceStatus {
  api: "ok";
  checked_at: string;
  ai: string;
  title: string;
  detail: string;
  level: "ok" | "warning" | "error" | "muted";
  provider?: string;
  model?: string;
  providers?: Array<{ provider: string; model: string }>;
  last_statuses?: Array<{ code?: string; title?: string; detail?: string; provider?: string; model?: string }>;
}

export interface AiProviderSetting {
  id: "vercel_gateway" | "openai" | "gemini" | "anthropic" | "groq" | "openrouter";
  label: string;
  configured: boolean;
  account_configured: boolean;
  environment_configured: boolean;
  model: string;
  default_model: string;
}

export interface AiSettings {
  enabled: boolean;
  mode: "auto" | "ensemble" | "vercel_gateway" | "openai" | "gemini" | "anthropic" | "groq" | "openrouter";
  max_providers: number;
  providers: AiProviderSetting[];
}

export interface AiChatResponse {
  answer: string;
  mode: "online" | "local";
  provider?: string;
  model?: string;
  status?: { code?: string; title?: string; detail?: string; provider?: string; model?: string };
}

export interface CandidateInput {
  name: string;
  target_title: string;
  english_level: string;
  desired_countries: string;
  salary_min: number;
  notes: string;
  hard_exclude: string;
  hard_require: string;
  preferred_regions: string;
  preferred_cities: string;
  preferred_companies: string;
  priority_titles: string;
  contact_email: string;
  cover_tone: "formal" | "friendly" | "";
  cover_length: "compact" | "detailed" | "";
  manual_review: number;
  skills: string[];
}

export interface Job {
  id: number;
  candidate_id: number;
  candidate: string;
  company: string;
  position: string;
  source: string;
  link: string;
  remote_location: string;
  salary_text: string;
  score: number;
  status: string;
  company_rating: number | null;
  company_rating_verified?: boolean;
  company_rating_note?: string;
  favorite?: number;
  strengths?: string;
  weaknesses?: string;
  positioning?: string;
  recommendation?: string;
  risk?: string;
  posted_at?: string;
  employer_email?: string;
  employer_contact?: string;
  final_salary_advice?: string;
  verified_at?: string;
  active_checked_at?: string;
  is_active?: number;
  is_manual?: number;
  hot?: boolean;
  priority_label?: string;
  priority_rank?: number;
  equipment_priority?: boolean;
  relocation_priority?: boolean;
  preview?: { description?: string; tags?: string[]; location?: string; salary?: string; posted_at?: string; benefits?: string[] } | null;
  contacts?: { emails?: string[]; phones?: string[]; telegram?: string[] };
  equipment?: string[];
  benefits?: string[];
  schedule?: string;
  sector?: string;
  moonlight_compatible?: number;
  moonlight_reason?: string;
  last_seen?: string;
  links?: Array<{
    url: string;
    source: string;
    posted_at?: string;
  }>;
}

export interface Gig {
  id: number;
  candidate_id: number;
  candidate: string;
  title: string;
  client: string;
  source: string;
  link: string;
  location: string;
  category: string;
  work_format: string;
  pay_text: string;
  score: number;
  status: string;
  favorite?: number;
  posted_at?: string;
  active_checked_at?: string;
  is_active?: number;
  hot?: boolean;
  description?: string;
  contacts?: { emails?: string[]; phones?: string[]; telegram?: string[] };
  safety_note?: string;
  requirements_note?: string;
  links?: Array<{ url: string; source: string; posted_at?: string }>;
}

export interface EducationChance {
  score: number;
  label: string;
  note: string;
}

export interface HigherEducationOption {
  rank: number;
  kind: "university" | "college";
  institution: string;
  program: string;
  location: string;
  mode: string;
  language: string;
  credential: string;
  recognition: string;
  cost: string;
  funding: string;
  admission: string;
  math: string;
  deadline: string;
  ease_score: number;
  budget_score: number;
  qa_candidate: EducationChance;
  support_candidate: EducationChance;
  preparation: string[];
  caveat: string;
  url: string;
  apply_url: string;
  scholarship_url?: string;
  community_url?: string;
  contact: string;
}

export interface EducationResource {
  title: string;
  kind: string;
  detail: string;
  url: string;
  safety?: string;
}

export interface Application {
  id: number;
  company: string;
  position: string;
  status: string;
  created_at: string;
  link: string;
  cover_letter?: string;
  recipient_email?: string;
  subject?: string;
  resume_id?: number;
}

export interface SpecialSourceStatus {
  name: "CareerSpace" | "SETTERS Media" | "Hirify";
  url: string;
  status: "checked" | "error" | "pending";
  checked: number;
  target?: number;
  matched: number;
  last_checked_at?: string;
  detail: string;
}

export interface SpecialAttention {
  jobs: Job[];
  sources: SpecialSourceStatus[];
  checked_at: string;
  summary?: { checked: number; saved: number; archived: number };
}

export interface TrackerRow {
  id: number;
  vacancy_id: number;
  candidate_id: number;
  candidate: string;
  applied_at: string;
  response_at: string;
  position: string;
  company: string;
  result: string;
  comments: string;
  salary_range: string;
  language: string;
  vacancy_link: string;
  vacancy_source: string;
  sync_status: "pending" | "synced" | "error";
  sync_error?: string;
  synced_at?: string;
  updated_at?: string;
  item_type?: string;
  score?: number;
  priority?: string;
  favorite?: number;
  status?: string;
  cover_formal?: string;
  cover_friendly?: string;
  cover_detailed?: string;
  from_email?: string;
}

export interface TelegramBotStatus {
  connected: boolean;
  subscribed: boolean;
  username: string;
  chat_id: string;
  start_url: string;
  schedule: string;
  detail: string;
}

export interface ApplicationTracker {
  rows: TrackerRow[];
  results: string[];
  google_sheets: {
    spreadsheet_url: string;
    webhook_url: string;
    secret_configured: boolean;
    connected: boolean;
    last_sync_at?: string;
    last_sync_status?: string;
    last_sync_detail?: string;
  };
}

export interface SearchRun {
  run_id: string;
  status: "queued" | "running" | "completed" | "failed";
  stage: string;
  detail: string;
  error?: string;
  created_at?: string;
  updated_at?: string;
  result?: {
    raw_count: number;
    saved_count?: number;
    active_count?: number;
    new_count?: number;
    updated_count?: number;
    rechecked_count?: number;
    archived_count?: number;
    golden_count: number;
    gigs_saved?: number;
    gigs_refreshed?: number;
    internships_saved?: number;
    internships_refreshed?: number;
    next_batch?: number;
    batch_count?: number;
    sources_total?: number;
    sources_processed?: number;
    deferred_sources?: number;
    candidate_stats?: Array<{
      candidate: string;
      review: number;
      golden: number;
    }>;
    notifications?: Array<{
      channel: string;
      status: string;
      detail: string;
    }>;
  };
}

export interface Dashboard {
  metrics: Metrics;
  candidates: Candidate[];
  jobs: Job[];
  gigs?: Gig[];
  internships?: Gig[];
  education_recommendations?: Array<{
    track: string;
    title: string;
    format: string;
    fit: string;
    eligibility: string;
    priority: string;
    url: string;
  }>;
  higher_education_options?: HigherEducationOption[];
  education_application_guide?: Array<{ step: number; title: string; detail: string }>;
  applicant_resources?: EducationResource[];
  relocation_resources?: EducationResource[];
  applications: Application[];
  sources_list?: Array<{ id: number; name: string; kind: string; region: string; url: string; enabled: number; notes?: string; status?: "checked" | "error" | "pending" | "paused"; last_checked_at?: string; jobs_found?: number; detail?: string }>;
  company_ratings?: Array<{ id: number; company: string; country?: string; rating: number; stability?: number; remote_friendly?: number; b1_friendly?: number; official_score?: number; notes?: string }>;
  search: SearchRun | null;
  network: string;
}
