import { useState } from "react";

import { LoginForm } from "./components/LoginForm";
import { PostList } from "./components/PostList";

export default function App() {
  const [token, setToken] = useState<string | null>(null);
  return (
    <main>
      <h1>Blogpost</h1>
      {token ? <PostList token={token} /> : <LoginForm onLogin={setToken} />}
    </main>
  );
}
