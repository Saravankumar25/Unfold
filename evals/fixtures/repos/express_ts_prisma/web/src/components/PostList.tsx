import { useState } from "react";

import { usePosts } from "../hooks/usePosts";
import { PostCard } from "./PostCard";

interface PostListProps {
  token: string;
}

export function PostList({ token }: PostListProps) {
  const [page, setPage] = useState(1);
  const { posts, error } = usePosts(page);
  if (error) {
    return <p role="alert">{error}</p>;
  }
  return (
    <section data-token={token.length > 0}>
      {posts.map((p) => (
        <PostCard key={p.id} post={p} />
      ))}
      <button onClick={() => setPage(page + 1)}>More</button>
    </section>
  );
}
