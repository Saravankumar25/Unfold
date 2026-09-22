import { FormEvent, useState } from "react";

import { login } from "../api/client";

interface LoginFormProps {
  onLogin: (token: string) => void;
}

export function LoginForm({ onLogin }: LoginFormProps) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    const { token } = await login(email, password);
    onLogin(token);
  }

  return (
    <form onSubmit={handleSubmit}>
      <input value={email} onChange={(e) => setEmail(e.target.value)} placeholder="email" />
      <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
      <button type="submit">Sign in</button>
    </form>
  );
}
