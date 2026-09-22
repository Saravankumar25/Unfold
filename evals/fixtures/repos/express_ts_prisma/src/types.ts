import { Request } from "express";

export type Role = "READER" | "AUTHOR" | "ADMIN";

export interface AuthUser {
  id: string;
  email: string;
  role: Role;
}

export interface AuthenticatedRequest extends Request {
  user?: AuthUser;
}

export interface CreatePostInput {
  title: string;
  body: string;
  published?: boolean;
}
