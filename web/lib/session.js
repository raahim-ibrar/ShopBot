import { createHmac, randomUUID, timingSafeEqual } from "node:crypto";

const cookieNames = { shopper: "portpair-shopper", seller: "portpair-seller" };
const lifetimes = { shopper: 30 * 86400, seller: 8 * 3600 };

function sign(value) {
  const secret = process.env.SESSION_SECRET;
  if (!secret || secret.length < 32) throw new Error("Missing session configuration");
  return createHmac("sha256", secret).update(value).digest("base64url");
}

export function readSession(request, role) {
  const token = request.cookies.get(cookieNames[role])?.value;
  if (!token) return null;
  const [id, expires, signature, extra] = token.split(".");
  if (extra !== undefined || !/^[0-9a-f-]{36}$/.test(id) || !/^\d+$/.test(expires) || !signature) return null;
  if (Number(expires) <= Date.now() / 1000) return null;
  const actual = Buffer.from(signature);
  const expected = Buffer.from(sign(`${role}.${id}.${expires}`));
  return actual.length === expected.length && timingSafeEqual(actual, expected) ? id : null;
}

export function setSession(response, request, role, id = randomUUID(), clear = false) {
  const duration = clear ? 0 : lifetimes[role];
  const expires = Math.floor(Date.now() / 1000) + duration;
  const value = clear ? "" : `${id}.${expires}.${sign(`${role}.${id}.${expires}`)}`;
  response.cookies.set(cookieNames[role], value, {
    path: "/", maxAge: duration, httpOnly: true,
    sameSite: role === "seller" ? "strict" : "lax",
    secure: request.nextUrl.protocol === "https:" || process.env.VERCEL === "1",
  });
  return response;
}
