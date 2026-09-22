const API_URL = import.meta.env.VITE_API_URL ?? "http://localhost:4000/api";

export interface Post {
  id: string;
  title: string;
  body: string;
  author: { id: string; name: string | null };
}

async function request<T>(path: string, init: RequestInit = {}, token?: string): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }
  const res = await fetch(`${API_URL}${path}`, { ...init, headers });
  if (!res.ok) {
    throw new Error(`API ${res.status}`);
  }
  return (await res.json()) as T;
}

export function login(email: string, password: string): Promise<{ token: string }> {
  return request("/auth/login", { method: "POST", body: JSON.stringify({ email, password }) });
}

export function fetchPosts(page = 1): Promise<Post[]> {
  return request(`/posts?page=${page}`);
}

export function createPost(token: string, title: string, body: string): Promise<Post> {
  return request("/posts", { method: "POST", body: JSON.stringify({ title, body }) }, token);
}
