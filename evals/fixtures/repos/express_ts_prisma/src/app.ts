import cors from "cors";
import express, { Express } from "express";
import helmet from "helmet";

import { errorHandler } from "./middleware/errorHandler";
import apiRouter from "./routes";

/** Build the Express application with security middleware and all API routes. */
export function createApp(): Express {
  const app = express();
  app.use(helmet());
  app.use(cors());
  app.use(express.json({ limit: "1mb" }));

  app.get("/health", (_req, res) => {
    res.json({ status: "ok" });
  });

  app.use("/api", apiRouter);
  app.use(errorHandler);
  return app;
}
