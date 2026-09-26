export function apiUrl(path: string) {
  const base = process.env.NEXT_PUBLIC_API_URL ?? "";
  return `${base}${path}`;
}

export function apiFetch(path: string, init?: RequestInit) {
  return fetch(apiUrl(path), init);
}
