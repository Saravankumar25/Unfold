import { describe, expect, it } from "vitest";
import { signClient } from "@/lib/client-signature";

describe("signClient", () => {
  // Vectors produced by the API's own signer (backend/src/unfold/api/proxy_auth.py `sign`), so the two
  // implementations are proven to agree byte for byte.
  it("matches the backend signer for IPv4", () => {
    expect(signClient("test-secret", "203.0.113.7", 1760000000)).toBe(
      "v1.1760000000.203.0.113.7.1e506e23681d2befa1c8fd55210f49083620fb6127c5fb9e9adee24e0d6fb86c",
    );
  });

  it("matches the backend signer for IPv6", () => {
    expect(signClient("test-secret", "2001:db8::1", 1760000000)).toBe(
      "v1.1760000000.2001:db8::1.78900e0ae28148e5b14f24783d664589620abadb2c31a944f3fb20b23f295bc5",
    );
  });

  it("changes with the secret and the address", () => {
    const a = signClient("a", "1.2.3.4", 1);
    expect(signClient("b", "1.2.3.4", 1)).not.toBe(a);
    expect(signClient("a", "1.2.3.5", 1)).not.toBe(a);
  });
});
