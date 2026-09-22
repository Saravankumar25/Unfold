import { prisma } from "../db";
import { CreatePostInput } from "../types";

const PAGE_SIZE = 20;

export class PostNotFoundError extends Error {}

export async function listPublished(page: number) {
  return prisma.post.findMany({
    where: { published: true },
    include: { author: { select: { id: true, name: true } } },
    orderBy: { createdAt: "desc" },
    skip: (page - 1) * PAGE_SIZE,
    take: PAGE_SIZE,
  });
}

export async function getPost(id: string) {
  return prisma.post.findUnique({ where: { id }, include: { comments: true, author: true } });
}

export async function createPost(authorId: string, input: CreatePostInput) {
  return prisma.post.create({ data: { ...input, authorId } });
}

export async function addComment(postId: string, authorId: string, body: string) {
  const post = await getPost(postId);
  if (!post) {
    throw new PostNotFoundError(postId);
  }
  return prisma.comment.create({ data: { postId, authorId, body } });
}
