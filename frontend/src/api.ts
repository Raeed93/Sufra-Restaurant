// Thin wrapper around fetch for the Sufra API.
// The login token lives in an httpOnly cookie, so this code never sees or stores it.

export type User = { id: number; username: string };
export type Preference = { id: number; kind: string; value: string };
export type ToolCall = { name: string; arguments: string; output: string };
export type Conversation = { id: number; title: string | null; created_at: string };
export type ChatMessage = {
  role: "user" | "assistant";
  content: string;
  toolCalls?: ToolCall[];
};

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(`/api${path}`, {
    method,
    credentials: "same-origin",
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    let message = `Request failed (${res.status})`;
    try {
      const data = await res.json();
      if (typeof data.detail === "string") message = data.detail;
      else if (Array.isArray(data.detail)) message = describeValidation(data.detail);
    } catch {
      /* body wasn't JSON; keep the generic message */
    }
    throw new ApiError(res.status, message);
  }
  return res.status === 204 ? (undefined as T) : res.json();
}

// Turn FastAPI's 422 details into one readable sentence.
function describeValidation(detail: { loc: string[]; msg: string }[]): string {
  const field = detail[0]?.loc?.at(-1);
  if (field === "username") return "Usernames are 3–30 letters, numbers or underscores.";
  if (field === "password") return "Passwords need at least 8 characters.";
  if (field === "message") return "Messages can be up to 2,000 characters.";
  return detail[0]?.msg ?? "Check what you entered and try again.";
}

export const api = {
  me: () => request<{ user: User; preferences: Preference[] }>("GET", "/me"),
  register: (username: string, password: string) =>
    request<User>("POST", "/auth/register", { username, password }),
  login: (username: string, password: string) =>
    request<User>("POST", "/auth/login", { username, password }),
  logout: () => request<void>("POST", "/auth/logout"),
  forget: (id: number) => request<void>("DELETE", `/me/preferences/${id}`),
  conversations: () => request<Conversation[]>("GET", "/conversations"),
  messages: (id: number) =>
    request<{ role: "user" | "assistant"; content: string }[]>("GET", `/conversations/${id}/messages`),
  chat: (message: string, conversationId: number | null) =>
    request<{
      conversation_id: number;
      reply: string;
      tool_calls: ToolCall[];
      preferences: Preference[];
    }>("POST", "/chat", { message, conversation_id: conversationId }),
};
