import { PrismaClient } from "@prisma/client";

/** Shared Prisma client (one connection pool per process). */
export const prisma = new PrismaClient();
