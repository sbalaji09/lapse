export function apiFetch(path: string, init?: RequestInit) {
  const base = process.env.NEXT_PUBLIC_API_URL ?? "";
  return fetch(`${base}${path}`, init);
}
