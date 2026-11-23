"use client";

import { useEffect, useState } from "react";
import { money, shopApi } from "../../lib/shop-client";
import Icon from "../icons";

function InventoryRow({ product, onSave, updating }) {
  const [stock, setStock] = useState(String(product.stock));
  return <tr>
    <th scope="row">{product.name}<small>{product.sku}</small></th>
    <td className="capitalize">{product.category}</td>
    <td>{money(product.price_cents)}</td>
    <td><span className={product.stock ? "" : "low"}>{product.stock}</span></td>
    <td><form className="stock-form" onSubmit={(event) => { event.preventDefault(); onSave(product.sku, Number(stock)); }}>
      <label className="sr-only" htmlFor={`stock-${product.sku}`}>New stock for {product.name}</label>
      <input id={`stock-${product.sku}`} type="number" min="0" max="10000" step="1" required value={stock} onChange={(event) => setStock(event.target.value)} />
      <button className="button small outline" disabled={updating || !stock || Number(stock) === product.stock}>Save</button>
    </form></td>
  </tr>;
}

function Orders({ orders }) {
  if (!orders.length) return <p className="empty">Confirmed demo orders will appear here.</p>;
  return <div className="table-wrap"><table>
    <thead><tr><th>Order</th><th>Items</th><th>Placed</th><th>Total</th></tr></thead>
    <tbody>{orders.map((order) => <tr key={order.order_id}>
      <th scope="row">{order.order_id.slice(0, 8)}</th>
      <td>{order.items.map((item) => <div key={item.sku}>{item.quantity} × {item.name}</div>)}</td>
      <td><time dateTime={order.created_at}>{new Date(order.created_at).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })}</time></td>
      <td>{money(order.total_cents)}</td>
    </tr>)}</tbody>
  </table></div>;
}

export default function Dashboard() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [password, setPassword] = useState("");
  const [section, setSection] = useState("inventory");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  useEffect(() => {
    let active = true;
    shopApi("seller/dashboard").then((result) => { if (active) setData(result); })
      .catch((failure) => { if (active && failure.status !== 401) setError(failure.message); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, []);

  async function perform(action) {
    if (busy) return;
    setBusy(true);
    setNotice("");
    setError("");
    try { await action(); }
    catch (failure) {
      setError(failure.message);
      if (failure.status === 401) setData(null);
    } finally { setBusy(false); }
  }

  function login(event) {
    event.preventDefault();
    perform(async () => {
      await shopApi("seller/login", "POST", { password });
      setPassword("");
      setData(await shopApi("seller/dashboard"));
    });
  }

  function saveStock(sku, stock) {
    perform(async () => {
      const updated = await shopApi(`seller/inventory/${sku}`, "PATCH", { stock });
      setData((prior) => ({ ...prior, products: prior.products.map((product) => product.sku === sku ? { ...product, stock: updated.stock } : product) }));
      setNotice(`Stock saved for ${sku}.`);
    });
  }

  if (loading) return <main id="main" className="site-width seller-page"><p className="empty">Checking seller session…</p></main>;
  if (!data) return <main id="main" className="site-width seller-page">
    <section className="login-card">
      <span className="brand-icon"><Icon name="plug" size={22} /></span>
      <h1>Seller sign-in</h1>
      <p>Sign in to manage stock and see demo orders.</p>
      {error && <p role="alert" className="error">{error}</p>}
      <form className="login-form" onSubmit={login}>
        <label htmlFor="seller-password">Seller password</label>
        <input id="seller-password" type="password" autoComplete="current-password" required maxLength={200} value={password} onChange={(event) => setPassword(event.target.value)} />
        <button className="button primary" disabled={busy || !password}>{busy ? "Signing in…" : "Sign in"}</button>
      </form>
    </section>
  </main>;

  return <main id="main" className="site-width seller-page">
    <div className="seller-heading">
      <h1>Seller dashboard</h1>
      <div className="seller-actions">
        <button className="button outline" disabled={busy} onClick={() => perform(async () => setData(await shopApi("seller/dashboard")))}>Refresh</button>
        <button className="button text" disabled={busy} onClick={() => perform(async () => { await shopApi("seller/logout", "POST", {}); setData(null); })}>Sign out</button>
      </div>
    </div>
    <nav className="seller-tabs" aria-label="Seller dashboard sections">
      <button className={section === "inventory" ? "selected" : ""} aria-pressed={section === "inventory"} onClick={() => setSection("inventory")}>Inventory <span>{data.products.length}</span></button>
      <button className={section === "orders" ? "selected" : ""} aria-pressed={section === "orders"} onClick={() => setSection("orders")}>Demo orders <span>{data.orders.length}</span></button>
    </nav>
    {error && <p className="error" role="alert">{error}</p>}
    {notice && <p className="success-notice" role="status">{notice}</p>}
    {section === "inventory" ? <section aria-label="Inventory" className="seller-section">
      <div className="table-wrap"><table>
        <thead><tr><th>Product</th><th>Category</th><th>Price</th><th>In stock</th><th>Set stock</th></tr></thead>
        <tbody>{data.products.map((product) => <InventoryRow key={`${product.sku}-${product.stock}`} product={product} onSave={saveStock} updating={busy} />)}</tbody>
      </table></div>
    </section> : <section aria-label="Demo orders" className="seller-section"><Orders orders={data.orders} /></section>}
  </main>;
}
