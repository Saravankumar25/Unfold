import { Request, Response, Router } from "express";
import { z } from "zod";

import { login, register } from "@/services/authService";

const router = Router();

const credentials = z.object({ email: z.string().email(), password: z.string().min(8) });

async function registerHandler(req: Request, res: Response): Promise<void> {
  const input = credentials.extend({ name: z.string().optional() }).parse(req.body);
  const user = await register(input.email, input.password, input.name);
  res.status(201).json(user);
}

async function loginHandler(req: Request, res: Response): Promise<void> {
  const input = credentials.parse(req.body);
  const token = await login(input.email, input.password);
  res.json({ token });
}

router.post("/register", registerHandler);
router.post("/login", loginHandler);

export default router;
