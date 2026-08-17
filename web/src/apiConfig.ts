const DEFAULT_LOCAL_API_URL = "http://127.0.0.1:8080";

export function getApiTargets(configuredApiUrl: string, isProd: boolean) {
  const trimmed = configuredApiUrl.trim().replace(/\/$/, "");
  if (trimmed) {
    return [trimmed];
  }
  if (isProd) {
    // An empty base URL keeps a combined deployment on its own origin. A
    // separate API deployment should always provide VITE_API_URL at build time.
    return [""];
  }
  return [DEFAULT_LOCAL_API_URL];
}
