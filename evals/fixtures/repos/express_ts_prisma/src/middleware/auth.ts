import { NextFunction, Response } from "express";
import jwt from "jsonwebtoken";

import { config } from "../config";
import { AuthenticatedRequest, AuthUser, Role } from "../types";

/** Verify the Bearer JWT and attach the user to the request. */
export function requireAuth(req: AuthenticatedRequest, res: Response, next: NextFunction): void {
  const header = req.headers.authorization ?? "";
  const token = header.startsWith("Bearer ") ? header.slice(7) : null;
  if (!token) {
    res.status(401).json({ error: "missing token" });
    return;
  }
  try {
    const payload = jwt.verify(token, config.jwtSecret) as { sub: string; email: string; role: Role };
    const user: AuthUser = { id: payload.sub, email: payload.email, role: payload.role };
    req.user = user;
    next();
  } catch {
    res.status(401).json({ error: "invalid token" });
  }
}

/** Only allow users with the given role. */
export function requireRole(role: Role) {
  return (req: AuthenticatedRequest, res: Response, next: NextFunction): void => {
    if (req.user?.role !== role) {
      res.status(403).json({ error: "forbidden" });
      return;
    }
    next();
  };
}
