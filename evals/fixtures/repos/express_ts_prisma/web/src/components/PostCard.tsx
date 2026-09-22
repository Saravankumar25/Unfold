import { Post } from "../api/client";

export const PostCard = ({ post }: { post: Post }) => (
  <article>
    <h2>{post.title}</h2>
    <p>{post.body}</p>
    <small>by {post.author.name ?? "anonymous"}</small>
  </article>
);
