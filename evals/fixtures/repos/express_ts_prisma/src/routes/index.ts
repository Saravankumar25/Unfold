import { Router } from "express";

import authRouter from "./auth";
import postsRouter from "./posts";
import usersRouter from "./users";

const router = Router();

router.use("/auth", authRouter);
router.use("/users", usersRouter);
router.use("/posts", postsRouter);

export default router;
