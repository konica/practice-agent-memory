const BASE = import.meta.env.VITE_API_BASE ?? "http://localhost:8000";

export type User = {
  sub: string;
  email: string;
  name: string | null;
  picture: string | null;
};

export type Conversation = {
  id: string;
  title: string | null;
  updated_at: string;
};

export type ChatMessage = { role: "user" | "assistant"; content: string };

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!response.ok) throw new Error(`${init.method ?? "GET"} ${path}: ${response.status}`);
  return response.json() as Promise<T>;
}

export async function getMe(): Promise<User | null> {
  try {
    return await request<User>("/auth/me");
  } catch {
    return null;
  }
}

export const listConversations = () => request<Conversation[]>("/conversations");

export const createConversation = () =>
  request<Conversation>("/conversations", { method: "POST" });

export const renameConversation = (id: string, title: string) =>
  request<{ ok: boolean }>(`/conversations/${id}`, {
    method: "PATCH",
    body: JSON.stringify({ title }),
  });

export const deleteConversation = (id: string) =>
  request<{ ok: boolean }>(`/conversations/${id}`, { method: "DELETE" });

export const getMessages = (id: string) =>
  request<{ messages: ChatMessage[] }>(`/conversations/${id}/messages`);

export const logout = () => request<{ ok: boolean }>("/auth/logout", { method: "POST" });

export const loginUrl = `${BASE}/auth/login`;
