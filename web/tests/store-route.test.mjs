import assert from "node:assert/strict";
import test from "node:test";
import { NextRequest } from "next/server.js";
import { GET, POST, PUT } from "../app/api/store/[...action]/route.js";

const origin = "http://127.0.0.1:3010";

function invoke(handler, method, relative, { body, cookie, from = origin } = {}) {
  const request = new NextRequest(`${origin}/api/store/${relative}`, {
    method,
    headers: { host: new URL(origin).host, origin: from, ...(cookie ? { cookie } : {}), ...(body ? { "Content-Type": "application/json" } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  return handler(request, { params: Promise.resolve({ action: relative.split("/") }) });
}

test("store proxy isolates shopper cookies, strips browser identities, and gates seller routes", async (context) => {
  process.env.SESSION_SECRET = "s".repeat(40);
  process.env.STOREFRONT_API_SECRET = "storefront-test-key";
  process.env.ADMIN_API_SECRET = "seller-test-key";
  const calls = [];
  context.mock.method(globalThis, "fetch", async (target, options) => {
    calls.push({ path: new URL(target).pathname, key: options.headers["X-PortPair-Key"], body: JSON.parse(options.body || "null") });
    return Response.json({ ok: true });
  });

  const first = await invoke(GET, "GET", "cart");
  const second = await invoke(GET, "GET", "cart");
  const shopperCookie = first.headers.get("set-cookie").split(";")[0];
  assert.match(first.headers.get("set-cookie"), /HttpOnly/i);
  assert.notEqual(first.headers.get("set-cookie"), second.headers.get("set-cookie"));
  assert.notEqual(calls[0].path, calls[1].path);

  assert.equal((await invoke(PUT, "PUT", "cart", { cookie: shopperCookie, body: { sku: "CBL-C2C1", quantity: 1, session_id: "forged" } })).status, 200);
  assert.deepEqual(calls.at(-1).body, { sku: "CBL-C2C1", quantity: 1, session_id: calls[0].path.split("/").at(-1) });
  assert.equal(calls.at(-1).key, "storefront-test-key");
  assert.equal((await invoke(PUT, "PUT", "cart", { cookie: shopperCookie, body: { sku: "CBL-C2C1", quantity: 1 }, from: "https://unrelated.example" })).status, 403);
  assert.equal((await invoke(GET, "GET", "seller/dashboard", { cookie: shopperCookie })).status, 401);

  const login = await invoke(POST, "POST", "seller/login", { body: { password: "test-password" } });
  assert.equal(calls.at(-1).key, "seller-test-key");
  const sellerCookie = login.headers.get("set-cookie").split(";")[0];
  assert.equal((await invoke(GET, "GET", "seller/dashboard", { cookie: sellerCookie })).status, 200);
  assert.equal((await invoke(GET, "GET", "seller/dashboard", { cookie: sellerCookie + "x" })).status, 401);
  const logout = await invoke(POST, "POST", "seller/logout", { cookie: sellerCookie, body: {} });
  assert.match(logout.headers.get("set-cookie"), /Max-Age=0/i);
});
