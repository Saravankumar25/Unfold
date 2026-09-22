import dotenv from "dotenv";

dotenv.config();

export const config = {
  port: Number(process.env.PORT ?? 4000),
  jwtSecret: process.env.JWT_SECRET ?? "",
  jwtExpiresIn: "12h",
  databaseUrl: process.env.DATABASE_URL ?? "",
};
