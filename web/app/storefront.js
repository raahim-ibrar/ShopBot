"use client";

import { useEffect, useRef, useState } from "react";
import { money, shopApi } from "../lib/shop-client";
import Icon from "./icons";

const categories = { charger: ["Chargers", "charger"], cable: ["Cables", "cable"], "power bank": ["Power banks", "battery"], hub: ["Hubs", "hub"], audio: ["Audio", "audio"] };
const starter = "Tell me your device and what you need. I can check compatibility, prices, and stock.";

function Product({ product, disabled, add }) {
  const [category, icon] = categories[product.category] || [product.category, "plug"];
  return <article className="product">
    <div className={`product-icon product-${icon}`}><Icon name={icon} size={24} /></div>
    <p className="product-category">{category}</p>
    <h3>{product.name}</h3>
    <p className="product-detail">{product.description}</p>
    <div className="product-bottom">
      <div><strong>{money(product.price_cents)}</strong><small className={product.stock ? "" : "low"}>{product.stock ? `${product.stock} in stock` : "Out of stock"}</small></div>
      <button type="button" className="button small outline" disabled={disabled || !product.stock} onClick={add} aria-label={`Add ${product.name} to cart`}>Add to cart</button>
    </div>
  </article>;
}

export default function Storefront() {
  const [products, setProducts] = useState([]);
  const [cart, setCart] = useState({});
  const [chat, setChat] = useState([{ role: "assistant", text: starter }]);
  const [message, setMessage] = useState("");
  const [category, setCategory] = useState("");
  const [query, setQuery] = useState("");
  const [panel, setPanel] = useState("chat");
  const [busy, setBusy] = useState("");
  const [ready, setReady] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [quote, setQuote] = useState(null);
  const [receipt, setReceipt] = useState(null);
  const panelRef = useRef(null);
  const chatLog = useRef(null);
  const running = useRef(false);

  useEffect(() => {
    let active = true;
    Promise.all([shopApi("catalog"), shopApi("cart")]).then(([catalog, basket]) => {
      if (active) {
        setProducts(catalog);
        setCart(basket.cart);
        setReady(true);
      }
    }).catch((failure) => { if (active) setError(failure.message); });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (chatLog.current) chatLog.current.scrollTop = chatLog.current.scrollHeight;
  }, [chat, busy]);

  const matching = products.filter((product) => (
    (!category || product.category === category) &&
    `${product.name} ${product.description} ${product.sku}`.toLowerCase().includes(query.trim().toLowerCase())
  ));
  const bySku = new Map(products.map((product) => [product.sku, product]));
  const count = Object.values(cart).reduce((sum, quantity) => sum + quantity, 0);
  const total = Object.entries(cart).reduce((sum, [sku, quantity]) => sum + (bySku.get(sku)?.price_cents || 0) * quantity, 0);

  async function perform(task, action) {
    if (running.current) return;
    running.current = true;
    setBusy(task);
    setError("");
    setNotice("");
    try { await action(); }
    catch (failure) { setError(failure.message); }
    finally { running.current = false; setBusy(""); }
  }

  function showPanel(next) {
    setPanel(next);
    panelRef.current?.scrollIntoView({ block: "nearest" });
  }

  function setQuantity(sku, quantity) {
    return perform("cart", async () => {
      const value = await shopApi("cart", "PUT", { sku, quantity });
      setCart(value.cart);
      setQuote(null);
      setReceipt(null);
      setNotice(quantity ? `Cart updated for ${bySku.get(sku)?.name || sku}.` : "Item removed from cart.");
    });
  }

  function sendChat(event) {
    event.preventDefault();
    const text = message.trim();
    if (!text) return;
    perform("chat", async () => {
      setMessage("");
      setChat((previous) => [...previous, { role: "shopper", text }]);
      const result = await shopApi("chat", "POST", { message: text });
      setChat((previous) => [...previous, { role: "assistant", text: result.reply }]);
      if (result.tool_calls.some(({ name, status }) => name === "set_cart" && status === "ok")) {
        const basket = await shopApi("cart");
        setCart(basket.cart);
        setQuote(null);
      }
      const order = result.tool_calls.find(({ name, status }) => name === "place_order" && status === "ok")?.result;
      if (order) {
        setCart({});
        setQuote(null);
        setReceipt(order);
        setProducts(await shopApi("catalog"));
      }
    });
  }

  function reviewOrder() {
    perform("quote", async () => {
      setReceipt(null);
      setQuote(await shopApi("quote", "POST", {}));
    });
  }

  function confirmOrder() {
    perform("order", async () => {
      try {
        const order = await shopApi("confirm", "POST", { quote_id: quote.quote_id });
        setReceipt(order);
        setQuote(null);
        setCart({});
        setProducts(await shopApi("catalog"));
      } catch (failure) {
        if (failure.status === 409) setQuote(null);
        throw failure;
      }
    });
  }

  return <main id="main" className="site-width storefront">
    <section className="hero">
      <div className="hero-copy">
        <h1>Good gear.<br />Right fit.</h1>
        <p>Find chargers, cables, and tech accessories that work with the devices you already own.</p>
        <div className="hero-actions"><a className="button primary" href="#catalog">Shop accessories</a><button className="button text" onClick={() => showPanel("chat")}>Ask PortPair</button></div>
      </div>
    </section>

    <div className="shop-grid">
      <section id="catalog" aria-labelledby="catalog-title">
        <div className="catalog-heading"><h2 id="catalog-title">Shop accessories</h2><span>{ready ? `${matching.length} products` : ""}</span></div>
        <label className="search"><Icon name="search" size={20} /><span className="sr-only">Search accessories</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search accessories" /></label>
        <div className="filters" aria-label="Filter by category">
          <button className={category === "" ? "filter selected" : "filter"} onClick={() => setCategory("")} aria-pressed={category === ""}>All</button>
          {Object.entries(categories).map(([value, [label]]) => <button key={value} className={category === value ? "filter selected" : "filter"} onClick={() => setCategory(value)} aria-pressed={category === value}>{label}</button>)}
        </div>
        <p className="sr-only" role="status">{notice}</p>
        {!ready && <p className="empty">{error ? "Catalog unavailable. Reload the page to try again." : "Loading products…"}</p>}
        {ready && !matching.length && <p className="empty">No matching products. Try a different search or category.</p>}
        <div className="products">
          {matching.map((product) => <Product key={product.sku} product={product} disabled={Boolean(busy) || !ready || (cart[product.sku] || 0) >= Math.min(10, product.stock)} add={() => setQuantity(product.sku, (cart[product.sku] || 0) + 1)} />)}
        </div>
      </section>

      <aside className="assistant-panel" ref={panelRef} aria-label="Shopping assistant and cart">
        <div className="panel-tabs">
          <button className={panel === "chat" ? "active" : ""} onClick={() => setPanel("chat")} aria-pressed={panel === "chat"}><Icon name="chat" size={19} /> Assistant</button>
          <button className={panel === "cart" ? "active" : ""} onClick={() => setPanel("cart")} aria-pressed={panel === "cart"}><Icon name="bag" size={18} /> Cart <span aria-live="polite">{count}</span></button>
        </div>
        {error && <p className="error" role="alert">{error}</p>}
        {panel === "chat" ? <div className="panel-content">
          <h2>Ask PortPair</h2>
          <div className="chat-log" role="log" aria-label="Conversation" aria-live="polite" ref={chatLog}>
            {chat.map((entry, index) => <p className={`bubble ${entry.role}`} key={index}><span className="sr-only">{entry.role === "assistant" ? "PortPair: " : "You: "}</span>{entry.text}</p>)}
            {busy === "chat" && <p className="chat-wait" role="status">Checking with PortPair…</p>}
          </div>
          <form className="chat-form" onSubmit={sendChat}>
            <label className="sr-only" htmlFor="shopper-message">Message</label>
            <textarea id="shopper-message" value={message} onChange={(event) => setMessage(event.target.value)} placeholder="Will this work with my iPhone 15?" maxLength={4000} rows={2} />
            <button className="button primary" type="submit" disabled={!ready || Boolean(busy) || !message.trim()}>Send</button>
          </form>
        </div> : <div className="panel-content">
          <h2>Your cart</h2>
          {receipt && <div className="receipt" role="status"><strong>Demo order placed</strong><span>{money(receipt.total_cents)} · {receipt.order_id.slice(0, 8)}</span></div>}
          {!count && !receipt && <p className="empty">Your cart is empty.</p>}
          {Object.entries(cart).map(([sku, quantity]) => {
            const product = bySku.get(sku);
            return <div className="cart-line" key={sku}>
              <div><strong>{product?.name || sku}</strong><small>{product ? money(product.price_cents * quantity) : "Unavailable"}</small></div>
              <div className="quantity"><button aria-label={`Decrease quantity of ${product?.name || sku}`} disabled={Boolean(busy)} onClick={() => setQuantity(sku, quantity - 1)}>−</button><span>{quantity}</span><button aria-label={`Increase quantity of ${product?.name || sku}`} disabled={Boolean(busy) || quantity >= Math.min(10, product?.stock || 0)} onClick={() => setQuantity(sku, quantity + 1)}>+</button></div>
            </div>;
          })}
          {count > 0 && <div className="cart-total"><span>Total</span><strong>{money(total)}</strong></div>}
          {count > 0 && !quote && <button className="button primary full" disabled={Boolean(busy)} onClick={reviewOrder}>{busy === "quote" ? "Checking stock…" : "Review demo order"}</button>}
          {quote && <div className="quote">
            <h3>Review demo order</h3>
            <ul>{quote.items.map((item) => <li key={item.sku}>{item.quantity} × {item.name}</li>)}</ul>
            <p><strong>{money(quote.total_cents)}</strong> · No payment is collected.</p>
            <button className="button primary full" disabled={Boolean(busy)} onClick={confirmOrder}>{busy === "order" ? "Placing order…" : "Confirm demo order"}</button>
            <button className="button text full" disabled={Boolean(busy)} onClick={() => perform("cancel", async () => { await shopApi("cancel", "POST", {}); setQuote(null); })}>Back to cart</button>
          </div>}
        </div>}
      </aside>
    </div>
  </main>;
}
