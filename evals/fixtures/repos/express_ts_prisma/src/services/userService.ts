import { prisma } from "../db";

const publicFields = { id: true, email: true, name: true, role: true, createdAt: true } as const;

export async function getUserById(id: string) {
  return prisma.user.findUnique({ where: { id }, select: publicFields });
}

export async function listUsers() {
  return prisma.user.findMany({ select: publicFields, orderBy: { createdAt: "desc" } });
}

export async function deleteUser(id: string): Promise<void> {
  await prisma.user.delete({ where: { id } });
}
