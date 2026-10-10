import createClient, { type Middleware } from "openapi-fetch";
import type { components, paths } from "./schema";

export type Schemas = components["schemas"];

const TOKEN_KEY = "asw.token";

/** Token lives in sessionStorage: cleared when the tab closes, never in a cookie the API would trust. */
export const tokenStore = {
  get(): string | null {
    try {
      return sessionStorage.getItem(TOKEN_KEY);
    } catch {
      return null;
    }
  },
  set(token: string | null): void {
    try {
      if (token) sessionStorage.setItem(TOKEN_KEY, token);
      else sessionStorage.removeItem(TOKEN_KEY);
    } catch {
      /* storage unavailable: token only lives for this page load */
    }
  },
};

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public details: Record<string, unknown> = {},
    public correlationId?: string | null,
  ) {
    super(message);
  }
}

const authMiddleware: Middleware = {
  onRequest({ request }) {
    const token = tokenStore.get();
    if (token) request.headers.set("Authorization", `Bearer ${token}`);
    return request;
  },
};

// Absolute origin keeps Request construction valid outside browsers (tests); fetch is looked up per call.
export const api = createClient<paths>({
  baseUrl: globalThis.location?.origin ?? "",
  fetch: (request) => globalThis.fetch(request),
});
api.use(authMiddleware);

type Result<T> = { data?: T; error?: unknown; response: Response };

/** Unwrap an openapi-fetch result, throwing a typed ApiError on failure. */
export async function unwrap<T>(promise: Promise<Result<T>>): Promise<T> {
  const { data, error, response } = await promise;
  if (!response.ok) {
    const body = (error ?? {}) as {
      error?: { code?: string; message?: string; details?: Record<string, unknown>; correlation_id?: string };
    };
    if (response.status === 401) window.dispatchEvent(new CustomEvent("asw:unauthorized"));
    throw new ApiError(
      response.status,
      body.error?.code ?? "http_error",
      body.error?.message ?? `Request failed (${response.status})`,
      body.error?.details ?? {},
      body.error?.correlation_id ?? response.headers.get("X-Request-ID"),
    );
  }
  return data as T;
}

/** Multipart upload helper (openapi-fetch's typed body does not cover FormData well). */
export async function uploadForm<T>(url: string, form: FormData): Promise<T> {
  const token = tokenStore.get();
  const res = await fetch(url, {
    method: "POST",
    body: form,
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  const json = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new ApiError(
      res.status,
      json?.error?.code ?? "http_error",
      json?.error?.message ?? "Upload failed",
      json?.error?.details ?? {},
    );
  }
  return json as T;
}
