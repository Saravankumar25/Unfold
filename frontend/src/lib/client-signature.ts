import { createHmac } from "node:crypto";

/**
 * `X-Unfold-Client: v1.<unix_ts>.<ip>.<hex HMAC-SHA256(secret, "<ts>|<ip>")>` (ADR 0016).
 *
 * Behind a Lambda Function URL the API sees only Vercel's egress addresses, so it cannot trust a peer address.
 * The proxy signs the address it observed; the API accepts it for 60 s and otherwise puts the caller in one shared
 * "unverified" bucket. The secret is the same `proxy_hmac_secret` the API reads from SSM (a server-side variable,
 * never exposed to the browser).
 */
export function signClient(secret: string, ip: string, nowSeconds: number = Math.floor(Date.now() / 1000)): string {
  const mac = createHmac("sha256", secret).update(`${nowSeconds}|${ip}`).digest("hex");
  return `v1.${nowSeconds}.${ip}.${mac}`;
}
