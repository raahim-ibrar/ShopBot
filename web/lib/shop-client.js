export async function shopApi(path, method = "GET", body) {
  const response = await fetch(`/api/store/${path}`, {
    method,
    cache: "no-store",
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const value = await response.json();
  if (!response.ok) {
    const error = new Error(typeof value.detail === "string" ? value.detail : "Check the values and try again.");
    error.status = response.status;
    throw error;
  }
  return value;
}

export function money(cents) {
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(cents / 100);
}
