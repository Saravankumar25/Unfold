import bcrypt from "bcryptjs";
import jwt from "jsonwebtoken";

import { config } from "../config";
import { prisma } from "../db";
import { HttpError } from "../middleware/errorHandler";

/** Create a user with a bcrypt password hash. */
export async function register(email: string, password: string, name?: string) {
  const passwordHash = await bcrypt.hash(password, 12);
  const user = await prisma.user.create({ data: { email, passwordHash, name } });
  return { id: user.id, email: user.email, name: user.name };
}

/** Verify credentials and issue a signed JWT. */
export async function login(email: string, password: string): Promise<string> {
  const user = await prisma.user.findUnique({ where: { email } });
  if (!user || !(await bcrypt.compare(password, user.passwordHash))) {
    throw new HttpError(401, "invalid credentials");
  }
  return signToken(user.id, user.email, user.role);
}

export function signToken(id: string, email: string, role: string): string {
  return jwt.sign({ sub: id, email, role }, config.jwtSecret, { expiresIn: config.jwtExpiresIn });
}
