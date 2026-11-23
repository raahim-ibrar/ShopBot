import { randomUUID } from "node:crypto";
import { NextResponse } from "next/server.js";
import { readSession, setSession } from "../../../../lib/session.js";

export const maxDuration = 60;

const noCache = { "Cache-Control": "no-store" };
const routes = {
  "GET catalog": ["/catalog", "public"],
  "GET cart": ["/cart", "shopper"],
  "PUT cart": ["/cart", "shopper", ["sku", "quantity"]],
  "POST chat": ["/chat", "shopper", ["message"]],
  "POST quote": ["/cart/quote", "shopper", []],
  "POST cancel": ["/cart/cancel", "shopper", []],
  "POST confirm": ["/cart/confirm", "shopper", ["quote_id"]],
  "POST seller/login": ["/seller/login", "login", ["password"]],
  "GET seller/dashboard": ["/seller/dashboard", "seller"],
};

function reply(value, status = 200) {
  return NextResponse.json(value, { status, headers: noCache });
}

async function jsonBody(request) {
  const reader = request.body?.getReader();
  if (!reader) throw new Error("Missing request body");
  const chunks = [];
  let size = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    size += value.byteLength;
    if (size > 65_536) {
      await reader.cancel();
      throw new Error("Request body too large");
    }
    chunks.push(value);
  }
  const body = JSON.parse(Buffer.concat(chunks).toString("utf8"));
  if (!body || typeof body !== "object" || Array.isArray(body)) throw new Error("Expected a JSON object");
  return body;
}

async function route(request, context) {
  const { action } = await context.params;
  const path = action.join("/");
  const method = request.method;
  const host = request.headers.get("host") || request.nextUrl.host;
  const expectedOrigin = `${request.nextUrl.protocol}//${host}`;
  if (method !== "GET" && request.headers.get("origin") !== expectedOrigin) {
    return reply({ detail: "This request must come from the PortPair site." }, 403);
  }
  if (method === "POST" && path === "seller/logout") {
    return setSession(reply({ ok: true }), request, "seller", undefined, true);
  }
  const inventory = method === "PATCH" && /^seller\/inventory\/[A-Z0-9-]{1,64}$/.test(path);
  const match = inventory ? [`/${path}`, "seller", ["stock"]] : routes[`${method} ${path}`];
  if (!match) return reply({ detail: "Page not found." }, 404);

  try {
    const [endpoint, role, fields] = match;
    if (role === "seller" && !readSession(request, "seller")) {
      return reply({ detail: "Sign in to the seller dashboard." }, 401);
    }
    const existingShopper = role === "shopper" ? readSession(request, "shopper") : null;
    const shopper = role === "shopper" ? (existingShopper || randomUUID()) : null;
    const apiKey = role === "seller" || role === "login" ? process.env.ADMIN_API_SECRET : process.env.STOREFRONT_API_SECRET;
    if (!apiKey) return reply({ detail: "PortPair service configuration is missing." }, 503);
    let body;
    if (fields) {
      const input = await jsonBody(request).catch(() => null);
      if (!input) return reply({ detail: "Send a valid JSON request of at most 64 KiB." }, 400);
      body = Object.fromEntries(fields.map((key) => [key, input[key]]));
      if (shopper) body.session_id = shopper;
    }

    const base = process.env.PORTPAIR_API_URL || "http://127.0.0.1:8010";
    const target = `${base.replace(/\/$/, "")}/internal${endpoint}${method === "GET" && shopper ? `/${shopper}` : ""}`;
    const upstream = await fetch(target, {
      method, cache: "no-store", redirect: "error", signal: AbortSignal.timeout(55_000),
      headers: { "X-PortPair-Key": apiKey, "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const value = await upstream.json().catch(() => ({ detail: "PortPair is temporarily unavailable." }));
    const response = reply(value, upstream.status);
    const retryAfter = upstream.headers.get("retry-after");
    if (retryAfter) response.headers.set("Retry-After", retryAfter);
    if (upstream.ok && role === "login") return setSession(response, request, "seller");
    if (shopper && !existingShopper) return setSession(response, request, "shopper", shopper);
    return response;
  } catch {
    return reply({ detail: "PortPair is temporarily unavailable. Please retry." }, 503);
  }
}

export { route as GET, route as POST, route as PUT, route as PATCH };
