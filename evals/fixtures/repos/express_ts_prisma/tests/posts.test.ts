import request from "supertest";

import { createApp } from "../src/app";

describe("posts API", () => {
  const app = createApp();

  it("returns health", async () => {
    const res = await request(app).get("/health");
    expect(res.status).toBe(200);
  });

  it("rejects unauthenticated post creation", async () => {
    const res = await request(app).post("/api/posts").send({ title: "x", body: "y" });
    expect(res.status).toBe(401);
  });
});
