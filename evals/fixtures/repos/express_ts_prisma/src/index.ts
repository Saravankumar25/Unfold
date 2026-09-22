import { createApp } from "./app";
import { config } from "./config";
import { prisma } from "./db";

async function main(): Promise<void> {
  await prisma.$connect();
  const app = createApp();
  app.listen(config.port, () => {
    console.log(`API listening on :${config.port}`);
  });
}

main().catch(async (err) => {
  console.error(err);
  await prisma.$disconnect();
  process.exit(1);
});
