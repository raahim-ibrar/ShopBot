CREATE TABLE IF NOT EXISTS products (
    channel text NOT NULL CHECK (channel IN ('website', 'grading')),
    store_id text NOT NULL,
    sku text NOT NULL,
    name text NOT NULL,
    category text NOT NULL,
    description text NOT NULL,
    compatible_devices text[] NOT NULL,
    price_cents integer NOT NULL CHECK (price_cents > 0),
    stock integer NOT NULL CHECK (stock >= 0),
    PRIMARY KEY (channel, store_id, sku)
);

CREATE TABLE IF NOT EXISTS checkout_quotes (
    id uuid PRIMARY KEY,
    channel text NOT NULL,
    store_id text NOT NULL,
    customer_key text NOT NULL,
    thread_key text NOT NULL,
    items jsonb NOT NULL,
    total_cents integer NOT NULL CHECK (total_cents > 0),
    expires_at timestamptz NOT NULL DEFAULT (now() + interval '10 minutes'),
    UNIQUE (id, channel, store_id, customer_key)
);

CREATE TABLE IF NOT EXISTS orders (
    id uuid PRIMARY KEY,
    quote_id uuid NOT NULL UNIQUE,
    channel text NOT NULL,
    store_id text NOT NULL,
    customer_key text NOT NULL,
    items jsonb NOT NULL,
    total_cents integer NOT NULL CHECK (total_cents > 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (quote_id, channel, store_id, customer_key)
        REFERENCES checkout_quotes (id, channel, store_id, customer_key)
);

CREATE INDEX IF NOT EXISTS orders_store_created_idx ON orders (channel, store_id, created_at DESC);

CREATE TABLE IF NOT EXISTS model_budget_lock (
    id integer PRIMARY KEY CHECK (id = 1)
);

INSERT INTO model_budget_lock (id) VALUES (1) ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS model_usage (
    id uuid PRIMARY KEY,
    stage text NOT NULL CHECK (stage IN ('development', 'evaluation')),
    reserved_microdollars integer NOT NULL CHECK (reserved_microdollars > 0),
    actual_microdollars integer CHECK (actual_microdollars >= 0),
    input_tokens integer CHECK (input_tokens >= 0),
    output_tokens integer CHECK (output_tokens >= 0),
    created_at timestamptz NOT NULL DEFAULT now()
);
