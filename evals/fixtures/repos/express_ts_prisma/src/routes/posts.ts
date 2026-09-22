import { Request, Response, Router } from "express";

import { requireAuth } from "../middleware/auth";
import * as postService from "../services/postService";
import { AuthenticatedRequest } from "../types";

const router = Router();

async function listPosts(req: Request, res: Response): Promise<void> {
  const page = Number(req.query.page ?? 1);
  res.json(await postService.listPublished(page));
}

async function getPost(req: Request, res: Response): Promise<void> {
  const post = await postService.getPost(req.params.id);
  if (!post) {
    res.status(404).json({ error: "not found" });
    return;
  }
  res.json(post);
}

async function createPost(req: AuthenticatedRequest, res: Response): Promise<void> {
  const post = await postService.createPost(req.user!.id, req.body);
  res.status(201).json(post);
}

async function addComment(req: AuthenticatedRequest, res: Response): Promise<void> {
  const comment = await postService.addComment(req.params.id, req.user!.id, req.body.body);
  res.status(201).json(comment);
}

router.get("/", listPosts);
router.get("/:id", getPost);
router.post("/", requireAuth, createPost);
router.post("/:id/comments", requireAuth, addComment);

export default router;
