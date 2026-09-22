import { useEffect, useState } from "react";

import { fetchPosts, Post } from "../api/client";

export function usePosts(page: number) {
  const [posts, setPosts] = useState<Post[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchPosts(page)
      .then(setPosts)
      .catch((e: Error) => setError(e.message));
  }, [page]);

  return { posts, error };
}
