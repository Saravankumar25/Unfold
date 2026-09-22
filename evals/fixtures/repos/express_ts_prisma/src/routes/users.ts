import { Response, Router } from "express";

import { requireAuth, requireRole } from "../middleware/auth";
import { deleteUser, getUserById, listUsers } from "../services/userService";
import { AuthenticatedRequest } from "../types";

const router = Router();

async function getMe(req: AuthenticatedRequest, res: Response): Promise<void> {
  const user = await getUserById(req.user!.id);
  res.json(user);
}

async function getUsers(_req: AuthenticatedRequest, res: Response): Promise<void> {
  res.json(await listUsers());
}

async function removeUser(req: AuthenticatedRequest, res: Response): Promise<void> {
  await deleteUser(req.params.id);
  res.status(204).end();
}

router.get("/me", requireAuth, getMe);
router.get("/", requireAuth, requireRole("ADMIN"), getUsers);
router.delete("/:id", requireAuth, requireRole("ADMIN"), removeUser);

export default router;
