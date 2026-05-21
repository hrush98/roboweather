# Polymarket CLOB API Guide (for Cursor docs)

This guide is the “live odds / orderbook” half of our Polymarket integration.

**Architecture (what we’re mirroring vs OddsPapi):**
- **Gamma API** = discovery + mappings (market metadata, IDs, token ids)
- **CLOB API** = live prices / orderbook + (optionally) order placement + order management

> **Key link:** Gamma `GET /markets` includes `conditionId` (market identifier) and also `clobTokenIds` (token IDs we need for CLOB). :contentReference[oaicite:0]{index=0}

---

## Core identifiers (how Gamma ↔ CLOB connect)

### `conditionId` (Gamma) ↔ `market` (CLOB)
In CLOB messages/docs, `market` is the **condition ID**. :contentReference[oaicite:1]{index=1}

### `clobTokenIds` (Gamma) ↔ `token_id` / `asset_id` (CLOB)
Gamma market payloads include `clobTokenIds`. :contentReference[oaicite:2]{index=2}  
CLOB uses `token_id` as the query param for orderbook/pricing endpoints, and orderbook responses include `asset_id`. :contentReference[oaicite:3]{index=3}

**Practical mapping table we store:**

| Concept | Gamma field | CLOB field |
|---|---|---|
| Market identifier | `conditionId` | `market` |
| Outcome token id(s) | `clobTokenIds` | `token_id` (query) / `asset_id` (responses) |

---

## Base URLs

- **CLOB REST API base:** `https://clob.polymarket.com` :contentReference[oaicite:4]{index=4}
- **Gamma REST API base:** `https://gamma-api.polymarket.com` :contentReference[oaicite:5]{index=5}

---

## Public market data (no auth required)

### 1) Orderbook

#### GET `/book` — Get order book summary (single token)
- **Endpoint:** `GET https://clob.polymarket.com/book` :contentReference[oaicite:6]{index=6}
- **Query params:**
  - `token_id` (string, required) :contentReference[oaicite:7]{index=7}
- **Response (high level):**
  - `market` (string) — market identifier :contentReference[oaicite:8]{index=8}
  - `asset_id` (string) — token id :contentReference[oaicite:9]{index=9}
  - `bids[]`, `asks[]` with `{ price: string, size: string }` :contentReference[oaicite:10]{index=10}
  - `min_order_size`, `tick_size`, `neg_risk` :contentReference[oaicite:11]{index=11}

#### POST `/books` — Get multiple order book summaries by request
- **Endpoint:** `POST https://clob.polymarket.com/books` :contentReference[oaicite:12]{index=12}
- **Body:** array of `{ token_id: string }` :contentReference[oaicite:13]{index=13}
- **Response:** array of orderbook summary objects (same shape as `/book`) :contentReference[oaicite:14]{index=14}

---

### 2) Pricing

#### GET `/price` — Get market price (single token + side)
- **Endpoint:** `GET https://clob.polymarket.com/price` :contentReference[oaicite:15]{index=15}
- **Query params:**
  - `token_id` (string, required) :contentReference[oaicite:16]{index=16}
  - `side` (`BUY` | `SELL`, required) :contentReference[oaicite:17]{index=17}
- **Response:** `{ "price": "..." }` (string for precision) :contentReference[oaicite:18]{index=18}

#### GET `/prices` — Get multiple market prices
- **Endpoint:** `GET https://clob.polymarket.com/prices` :contentReference[oaicite:19]{index=19}
- **Docs show response as:** map of `token_id -> { BUY: string, SELL: string }` :contentReference[oaicite:20]{index=20}
- **Note:** the docs page does not show query parameters for selecting which tokens (it only shows the endpoint + response shape). :contentReference[oaicite:21]{index=21}  
  - **Workaround:** use the POST form below where you explicitly specify token ids + sides.

#### POST `/prices` — Get multiple market prices by request (explicit token ids + sides)
- **Endpoint:** `POST https://clob.polymarket.com/prices` :contentReference[oaicite:22]{index=22}
- **Headers:** `Content-Type: application/json` :contentReference[oaicite:23]{index=23}
- **Body:** array (max length `500`) of:
  - `token_id` (string, required)
  - `side` (`BUY` | `SELL`, required) :contentReference[oaicite:24]{index=24}
- **Response:** map of `token_id -> { BUY|SELL: string }` (prices as strings) :contentReference[oaicite:25]{index=25}

#### GET `/midpoint` — Get midpoint price
- **Endpoint:** `GET https://clob.polymarket.com/midpoint` :contentReference[oaicite:26]{index=26}
- **Query params:** `token_id` (string, required) :contentReference[oaicite:27]{index=27}
- **Response:** `{ "mid": "..." }` (string for precision) :contentReference[oaicite:28]{index=28}

---

### 3) Spreads

#### POST `/spreads` — Get bid-ask spreads (multiple tokens)
- **Endpoint:** `POST https://clob.polymarket.com/spreads` :contentReference[oaicite:29]{index=29}
- **Headers:** `Content-Type: application/json` :contentReference[oaicite:30]{index=30}
- **Body:** array of `{ token_id: string }` :contentReference[oaicite:31]{index=31}
- **Response:** (see docs for exact response shape; endpoint is explicitly documented as returning bid-ask spreads for multiple tokens) :contentReference[oaicite:32]{index=32}

---

### 4) Historical timeseries

#### GET `/prices-history` — Historical Timeseries Data
- **Endpoint:** `GET https://clob.polymarket.com/prices-history` :contentReference[oaicite:33]{index=33}
- **Response example:** `{ "history": [ { "t": <unix_seconds>, "p": <price_number> } ] }` :contentReference[oaicite:34]{index=34}

---

## Authentication (required for private endpoints like orders)

Polymarket docs distinguish **L1** vs **L2** auth headers.

### L1 Headers
Fields shown in docs:
- `POLY_ADDRESS`
- `POLY_SIGNATURE`
- `POLY_TIMESTAMP`
- `POLY_NONCE` :contentReference[oaicite:35]{index=35}

### L2 Headers
Fields shown in docs:
- `POLY_ADDRESS`
- `POLY_SIGNATURE`
- `POLY_TIMESTAMP`
- `POLY_NONCE`
- `POLY_API_KEY`
- `POLY_PASSPHRASE` :contentReference[oaicite:36]{index=36}

> Many order-management endpoints explicitly state they “require a L2 Header.” :contentReference[oaicite:37]{index=37}

---

## Order Management (private; requires L2 header)

### Get Order
- **HTTP request:** `GET /<clob-endpoint>/data/order/<order_hash>` :contentReference[oaicite:38]{index=38}
- Docs note: “This endpoint requires a L2 Header.” :contentReference[oaicite:39]{index=39}

### Cancel Orders (by order ids)
From docs:
- **HTTP request:** `DELETE /<clob-endpoint>/orders` :contentReference[oaicite:40]{index=40}
- **Body parameters:**
  - `orderIDs` (string[], required): order IDs to cancel :contentReference[oaicite:41]{index=41}
- Docs note: requires L2 header. :contentReference[oaicite:42]{index=42}

### Cancel all orders
From docs:
- **HTTP request:** `DELETE /<clob-endpoint>/cancel-all` :contentReference[oaicite:43]{index=43}
- Docs note: requires L2 header. :contentReference[oaicite:44]{index=44}

> The Polymarket docs page shown above is the authoritative source for the remaining order-management endpoints (placing single orders, batching, active orders, onchain order info, trade history, etc.). If you want, paste those specific sections here and I’ll fold them into this file without guessing any missing fields.

---

## Recommended workflow (mirroring OddsPapi pattern)

### A) Build mappings (Gamma; not polled frequently)
1. **Gamma `GET /markets`** filtered to your domain (e.g., LoL / esports).
2. Store per market:
   - `conditionId`
   - `clobTokenIds`
   - any team/event metadata you care about :contentReference[oaicite:45]{index=45}

### B) Live pricing loop (CLOB; polled or streamed)
For each tracked token:
- Use `GET /book?token_id=...` for full best levels (bids/asks) :contentReference[oaicite:46]{index=46}
- Or use `GET /price?token_id=...&side=...` for single-sided price :contentReference[oaicite:47]{index=47}
- Or use `POST /prices` to fetch many token prices in one call (recommended) :contentReference[oaicite:48]{index=48}
- Optionally compute midpoint via `GET /midpoint?token_id=...` :contentReference[oaicite:49]{index=49}
- Optionally request spreads via `POST /spreads` :contentReference[oaicite:50]{index=50}

---

## FAQ

### Does Gamma provide the tokenID?
Yes — Gamma `GET /markets` includes a `clobTokenIds` field. :contentReference[oaicite:51]{index=51}

### How do we know what `market` means on the CLOB side?
Docs for the CLOB market websocket message specify `market` is the **condition ID**. :contentReference[oaicite:52]{index=52}

Websocket
WSS Overview
Overview and general information about the Polymarket Websocket

​
Overview
The Polymarket CLOB API provides websocket (wss) channels through which clients can get pushed updates. These endpoints allow clients to maintain almost real-time views of their orders, their trades and markets in general. There are two available channels user and market.
​
Subscription
To subscribe send a message including the following authentication and intent information upon opening the connection.
Field	Type	Description
auth	Auth	see next page for auth information
markets	string[]	array of markets (condition IDs) to receive events for (for user channel)
assets_ids	string[]	array of asset ids (token IDs) to receive events for (for market channel)
type	string	id of channel to subscribe to (USER or MARKET)
custom_feature_enabled	bool	enabling / disabling custom features
Where the auth field is of type Auth which has the form described in the WSS Authentication section below.
​
Subscribe to more assets
Once connected, the client can subscribe and unsubscribe to asset_ids by sending the following message:
Field	Type	Description
assets_ids	string[]	array of asset ids (token IDs) to receive events for (for market channel)
markets	string[]	array of market ids (condition IDs) to receive events for (for user channel)
operation	string	”subscribe” or “unsubscribe”
custom_feature_enabled	bool	enabling / disabling custom features


Websocket
WSS Quickstart
The following code samples and explanation will show you how to subscribe to the Marker and User channels of the Websocket. You’ll need your API keys to do this so we’ll start with that.
​
Getting your API Keys

DeriveAPIKeys-Python

DeriveAPIKeys-TS
from py_clob_client.client import ClobClient

host: str = "https://clob.polymarket.com"
key: str = "" #This is your Private Key. If using email login export from https://reveal.magic.link/polymarket otherwise export from your Web3 Application
chain_id: int = 137 #No need to adjust this
POLYMARKET_PROXY_ADDRESS: str = '' #This is the address you deposit/send USDC to to FUND your Polymarket account.

#Select from the following 3 initialization options to matches your login method, and remove any unused lines so only one client is initialized.

### Initialization of a client using a Polymarket Proxy associated with an Email/Magic account. If you login with your email use this example.
client = ClobClient(host, key=key, chain_id=chain_id, signature_type=1, funder=POLYMARKET_PROXY_ADDRESS)

### Initialization of a client using a Polymarket Proxy associated with a Browser Wallet(Metamask, Coinbase Wallet, etc)
client = ClobClient(host, key=key, chain_id=chain_id, signature_type=2, funder=POLYMARKET_PROXY_ADDRESS)

### Initialization of a client that trades directly from an EOA. 
client = ClobClient(host, key=key, chain_id=chain_id)

print( client.derive_api_key() )

See all 20 lines
​
Using those keys to connect to the Market or User Websocket

WSS-Connection
from websocket import WebSocketApp
import json
import time
import threading

MARKET_CHANNEL = "market"
USER_CHANNEL = "user"


class WebSocketOrderBook:
    def __init__(self, channel_type, url, data, auth, message_callback, verbose):
        self.channel_type = channel_type
        self.url = url
        self.data = data
        self.auth = auth
        self.message_callback = message_callback
        self.verbose = verbose
        furl = url + "/ws/" + channel_type
        self.ws = WebSocketApp(
            furl,
            on_message=self.on_message,
            on_error=self.on_error,
            on_close=self.on_close,
            on_open=self.on_open,
        )
        self.orderbooks = {}

    def on_message(self, ws, message):
        print(message)
        pass

    def on_error(self, ws, error):
        print("Error: ", error)
        exit(1)

    def on_close(self, ws, close_status_code, close_msg):
        print("closing")
        exit(0)

    def on_open(self, ws):
        if self.channel_type == MARKET_CHANNEL:
            ws.send(json.dumps({"assets_ids": self.data, "type": MARKET_CHANNEL}))
        elif self.channel_type == USER_CHANNEL and self.auth:
            ws.send(
                json.dumps(
                    {"markets": self.data, "type": USER_CHANNEL, "auth": self.auth}
                )
            )
        else:
            exit(1)

        thr = threading.Thread(target=self.ping, args=(ws,))
        thr.start()


    def subscribe_to_tokens_ids(self, assets_ids):
        if self.channel_type == MARKET_CHANNEL:
            self.ws.send(json.dumps({"assets_ids": assets_ids, "operation": "subscribe"}))

    def unsubscribe_to_tokens_ids(self, assets_ids):
        if self.channel_type == MARKET_CHANNEL:
            self.ws.send(json.dumps({"assets_ids": assets_ids, "operation": "unsubscribe"}))


    def ping(self, ws):
        while True:
            ws.send("PING")
            time.sleep(10)

    def run(self):
        self.ws.run_forever()


if __name__ == "__main__":
    url = "wss://ws-subscriptions-clob.polymarket.com"
    #Complete these by exporting them from your initialized client. 
    api_key = ""
    api_secret = ""
    api_passphrase = ""

    asset_ids = [
        "109681959945973300464568698402968596289258214226684818748321941747028805721376",
    ]
    condition_ids = [] # no really need to filter by this one

    auth = {"apiKey": api_key, "secret": api_secret, "passphrase": api_passphrase}

    market_connection = WebSocketOrderBook(
        MARKET_CHANNEL, url, asset_ids, auth, None, True
    )
    user_connection = WebSocketOrderBook(
        USER_CHANNEL, url, condition_ids, auth, None, True
    )

    market_connection.subscribe_to_tokens_ids(["123"])
    # market_connection.unsubscribe_to_tokens_ids(["123"])

    market_connection.run()
    # user_connection.run()
See all 99 lines

ebsocket
WSS Authentication
Only connections to user channel require authentication.
Field	Optional	Description
apikey	yes	Polygon account’s CLOB api key
secret	yes	Polygon account’s CLOB api secret
passphrase	yes	Polygon account’s CLOB api passphrase
WSS Quickstart
User Channel
Ask a question...

User Channel
Authenticated channel for updates related to user activities (orders, trades), filtered for authenticated user by apikey.
SUBSCRIBE
<wss-channel> user
​
Trade Message
Emitted when:
when a market order is matched (“MATCHED”)
when a limit order for the user is included in a trade (“MATCHED”)
subsequent status changes for trade (“MINED”, “CONFIRMED”, “RETRYING”, “FAILED”)
​
Structure
Name	Type	Description
asset_id	string	asset id (token ID) of order (market order)
event_type	string	”trade”
id	string	trade id
last_update	string	time of last update to trade
maker_orders	MakerOrder[]	array of maker order details
market	string	market identifier (condition ID)
matchtime	string	time trade was matched
outcome	string	outcome
owner	string	api key of event owner
price	string	price
side	string	BUY/SELL
size	string	size
status	string	trade status
taker_order_id	string	id of taker order
timestamp	string	time of event
trade_owner	string	api key of trade owner
type	string	”TRADE”
Where a MakerOrder object is of the form:
Name	Type	Description
asset_id	string	asset of the maker order
matched_amount	string	amount of maker order matched in trade
order_id	string	maker order ID
outcome	string	outcome
owner	string	owner of maker order
price	string	price of maker order
Response
{
  "asset_id": "52114319501245915516055106046884209969926127482827954674443846427813813222426",
  "event_type": "trade",
  "id": "28c4d2eb-bbea-40e7-a9f0-b2fdb56b2c2e",
  "last_update": "1672290701",
  "maker_orders": [
    {
      "asset_id": "52114319501245915516055106046884209969926127482827954674443846427813813222426",
      "matched_amount": "10",
      "order_id": "0xff354cd7ca7539dfa9c28d90943ab5779a4eac34b9b37a757d7b32bdfb11790b",
      "outcome": "YES",
      "owner": "9180014b-33c8-9240-a14b-bdca11c0a465",
      "price": "0.57"
    }
  ],
  "market": "0xbd31dc8a20211944f6b70f31557f1001557b59905b7738480ca09bd4532f84af",
  "matchtime": "1672290701",
  "outcome": "YES",
  "owner": "9180014b-33c8-9240-a14b-bdca11c0a465",
  "price": "0.57",
  "side": "BUY",
  "size": "10",
  "status": "MATCHED",
  "taker_order_id": "0x06bc63e346ed4ceddce9efd6b3af37c8f8f440c92fe7da6b2d0f9e4ccbc50c42",
  "timestamp": "1672290701",
  "trade_owner": "9180014b-33c8-9240-a14b-bdca11c0a465",
  "type": "TRADE"
}
​
Order Message
Emitted when:
When an order is placed (PLACEMENT)
When an order is updated (some of it is matched) (UPDATE)
When an order is canceled (CANCELLATION)
​
Structure
Name	Type	Description
asset_id	string	asset ID (token ID) of order
associate_trades	string[]	array of ids referencing trades that the order has been included in
event_type	string	”order”
id	string	order id
market	string	condition ID of market
order_owner	string	owner of order
original_size	string	original order size
outcome	string	outcome
owner	string	owner of orders
price	string	price of order
side	string	BUY/SELL
size_matched	string	size of order that has been matched
timestamp	string	time of event
type	string	PLACEMENT/UPDATE/CANCELLATION
Response
{
  "asset_id": "52114319501245915516055106046884209969926127482827954674443846427813813222426",
  "associate_trades": null,
  "event_type": "order",
  "id": "0xff354cd7ca7539dfa9c28d90943ab5779a4eac34b9b37a757d7b32bdfb11790b",
  "market": "0xbd31dc8a20211944f6b70f31557f1001557b59905b7738480ca09bd4532f84af",
  "order_owner": "9180014b-33c8-9240-a14b-bdca11c0a465",
  "original_size": "10",
  "outcome": "YES",
  "owner": "9180014b-33c8-9240-a14b-bdca11c0a465",
  "price": "0.57",
  "side": "SELL",
  "size_matched": "0",
  "timestamp": "1672290687",
  "type": "PLACEMENT"
}


Market Channel
Public channel for updates related to market updates (level 2 price data).
SUBSCRIBE
<wss-channel> market
​
book Message
Emitted When:
First subscribed to a market
When there is a trade that affects the book
​
Structure
Name	Type	Description
event_type	string	”book”
asset_id	string	asset ID (token ID)
market	string	condition ID of market
timestamp	string	unix timestamp the current book generation in milliseconds (1/1,000 second)
hash	string	hash summary of the orderbook content
buys	OrderSummary[]	list of type (size, price) aggregate book levels for buys
sells	OrderSummary[]	list of type (size, price) aggregate book levels for sells
Where a OrderSummary object is of the form:
Name	Type	Description
price	string	price of the orderbook level
size	string	size available at that price level
Response
{
  "event_type": "book",
  "asset_id": "65818619657568813474341868652308942079804919287380422192892211131408793125422",
  "market": "0xbd31dc8a20211944f6b70f31557f1001557b59905b7738480ca09bd4532f84af",
  "bids": [
    { "price": ".48", "size": "30" },
    { "price": ".49", "size": "20" },
    { "price": ".50", "size": "15" }
  ],
  "asks": [
    { "price": ".52", "size": "25" },
    { "price": ".53", "size": "60" },
    { "price": ".54", "size": "10" }
  ],
  "timestamp": "123456789000",
  "hash": "0x0...."
}
​
price_change Message
⚠️ Breaking Change Notice: The price_change message schema will be updated on September 15, 2025 at 11 PM UTC. Please see the migration guide for details.
Emitted When:
A new order is placed
An order is cancelled
​
Structure
Name	Type	Description
event_type	string	”price_change”
market	string	condition ID of market
price_changes	PriceChange[]	array of price change objects
timestamp	string	unix timestamp in milliseconds
Where a PriceChange object is of the form:
Name	Type	Description
asset_id	string	asset ID (token ID)
price	string	price level affected
size	string	new aggregate size for price level
side	string	”BUY” or “SELL”
hash	string	hash of the order
best_bid	string	current best bid price
best_ask	string	current best ask price
Response
{
    "market": "0x5f65177b394277fd294cd75650044e32ba009a95022d88a0c1d565897d72f8f1",
    "price_changes": [
        {
            "asset_id": "71321045679252212594626385532706912750332728571942532289631379312455583992563",
            "price": "0.5",
            "size": "200",
            "side": "BUY",
            "hash": "56621a121a47ed9333273e21c83b660cff37ae50",
            "best_bid": "0.5",
            "best_ask": "1"
        },
        {
            "asset_id": "52114319501245915516055106046884209969926127482827954674443846427813813222426",
            "price": "0.5",
            "size": "200",
            "side": "SELL",
            "hash": "1895759e4df7a796bf4f1c5a5950b748306923e2",
            "best_bid": "0",
            "best_ask": "0.5"
        }
    ],
    "timestamp": "1757908892351",
    "event_type": "price_change"
}
​
tick_size_change Message
Emitted When:
The minimum tick size of the market changes. This happens when the book’s price reaches the limits: price > 0.96 or price < 0.04
​
Structure
Name	Type	Description
event_type	string	”price_change”
asset_id	string	asset ID (token ID)
market	string	condition ID of market
old_tick_size	string	previous minimum tick size
new_tick_size	string	current minimum tick size
side	string	buy/sell
timestamp	string	time of event
Response
{
"event_type": "tick_size_change",
"asset_id": "65818619657568813474341868652308942079804919287380422192892211131408793125422",\
"market": "0xbd31dc8a20211944f6b70f31557f1001557b59905b7738480ca09bd4532f84af",
"old_tick_size": "0.01",
"new_tick_size": "0.001",
"timestamp": "100000000"
}
​
last_trade_price Message
Emitted When:
When a maker and taker order is matched creating a trade event.
Response
{
"asset_id":"114122071509644379678018727908709560226618148003371446110114509806601493071694",
"event_type":"last_trade_price",
"fee_rate_bps":"0",
"market":"0x6a67b9d828d53862160e470329ffea5246f338ecfffdf2cab45211ec578b0347",
"price":"0.456",
"side":"BUY",
"size":"219.217767",
"timestamp":"1750428146322"
}
​
best_bid_ask Message
Emitted When:
The best bid and ask prices for a market change.
(This message is behind the custom_feature_enabled flag)
​
Structure
Name	Type	Description
event_type	string	”best_bid_ask”
market	string	condition ID of market
asset_id	string	asset ID (token ID)
best_bid	string	current best bid price
best_ask	string	current best ask price
spread	string	spread between best bid and ask
timestamp	string	unix timestamp in milliseconds
​
Example
Response
{
  "event_type": "best_bid_ask",
  "market": "0x0005c0d312de0be897668695bae9f32b624b4a1ae8b140c49f08447fcc74f442",
  "asset_id": "85354956062430465315924116860125388538595433819574542752031640332592237464430",
  "best_bid": "0.73",
  "best_ask": "0.77",
  "spread": "0.04",
  "timestamp": "1766789469958"
}
​
new_market Message
Emitted When:
A new market is created.
(This message is behind the custom_feature_enabled flag)
​
Structure
Name	Type	Description
id	string	market ID
question	string	market question
market	string	condition ID of market
slug	string	market slug
description	string	market description
assets_ids	string[]	list of asset IDs
outcomes	string[]	list of outcomes
event_message	object	event message object
timestamp	string	unix timestamp in milliseconds
event_type	string	”new_market”
Where a EventMessage object is of the form:
Name	Type	Description
id	string	event message ID
ticker	string	event message ticker
slug	string	event message slug
title	string	event message title
description	string	event message description
​
Example
Response
{
    "id": "1031769",
    "question": "Will NVIDIA (NVDA) close above $240 end of January?",
    "market": "0x311d0c4b6671ab54af4970c06fcf58662516f5168997bdda209ec3db5aa6b0c1",
    "slug": "nvda-above-240-on-january-30-2026",
    "description": "This market will resolve to \"Yes\" if the official closing price for NVIDIA (NVDA) on the final trading day of January 2026 is higher than the listed price. Otherwise, this market will resolve to \"No\".\n\nIf the final trading day of the month is shortened (for example, due to a market-holiday schedule), the official closing price published for that shortened session will still be used for resolution.\n\nIf no official closing price is published for that session (for example, due to a trading halt into the close, system issue, or other disruption), the market will use the last valid on-exchange trade price of the regular session as the effective closing price.\n\nThe resolution source for this market is Yahoo Finance — specifically, the NVIDIA (NVDA) \"Close\" prices available at https://finance.yahoo.com/quote/NVDA/history, published under \"Historical Prices.\"\n\nIn the event of a stock split, reverse stock split, or similar corporate action affecting the listed company during the listed time frame, this market will resolve based on split-adjusted prices as displayed on Yahoo Finance.",
    "assets_ids": [
        "76043073756653678226373981964075571318267289248134717369284518995922789326425",
        "31690934263385727664202099278545688007799199447969475608906331829650099442770"
    ],
    "outcomes": [
        "Yes",
        "No"
    ],
    "event_message": {
        "id": "125819",
        "ticker": "nvda-above-in-january-2026",
        "slug": "nvda-above-in-january-2026",
        "title": "Will NVIDIA (NVDA) close above ___ end of January?",
        "description": "This market will resolve to \"Yes\" if the official closing price for NVIDIA (NVDA) on the final trading day of January 2026 is higher than the listed price. Otherwise, this market will resolve to \"No\".\n\nIf the final trading day of the month is shortened (for example, due to a market-holiday schedule), the official closing price published for that shortened session will still be used for resolution.\n\nIf no official closing price is published for that session (for example, due to a trading halt into the close, system issue, or other disruption), the market will use the last valid on-exchange trade price of the regular session as the effective closing price.\n\nThe resolution source for this market is Yahoo Finance — specifically, the NVIDIA (NVDA) \"Close\" prices available at https://finance.yahoo.com/quote/NVDA/history, published under \"Historical Prices.\"\n\nIn the event of a stock split, reverse stock split, or similar corporate action affecting the listed company during the listed time frame, this market will resolve based on split-adjusted prices as displayed on Yahoo Finance."
    },
    "timestamp": "1766790415550",
    "event_type": "new_market"
}
​
market_resolved Message
Emitted When:
A market is resolved.
(This message is behind the custom_feature_enabled flag)
​
Structure
Name	Type	Description
id	string	market ID
question	string	market question
market	string	condition ID of market
slug	string	market slug
description	string	market description
assets_ids	string[]	list of asset IDs
outcomes	string[]	list of outcomes
winning_asset_id	string	winning asset ID
winning_outcome	string	winning outcome
event_message	object	event message object
timestamp	string	unix timestamp in milliseconds
event_type	string	”market_resolved”
Where a EventMessage object is of the form:
Name	Type	Description
id	string	event message ID
ticker	string	event message ticker
slug	string	event message slug
title	string	event message title
description	string	event message description
​
Example
Response
{
    "id": "1031769",
    "question": "Will NVIDIA (NVDA) close above $240 end of January?",
    "market": "0x311d0c4b6671ab54af4970c06fcf58662516f5168997bdda209ec3db5aa6b0c1",
    "slug": "nvda-above-240-on-january-30-2026",
    "description": "This market will resolve to \"Yes\" if the official closing price for NVIDIA (NVDA) on the final trading day of January 2026 is higher than the listed price. Otherwise, this market will resolve to \"No\".\n\nIf the final trading day of the month is shortened (for example, due to a market-holiday schedule), the official closing price published for that shortened session will still be used for resolution.\n\nIf no official closing price is published for that session (for example, due to a trading halt into the close, system issue, or other disruption), the market will use the last valid on-exchange trade price of the regular session as the effective closing price.\n\nThe resolution source for this market is Yahoo Finance — specifically, the NVIDIA (NVDA) \"Close\" prices available at https://finance.yahoo.com/quote/NVDA/history, published under \"Historical Prices.\"\n\nIn the event of a stock split, reverse stock split, or similar corporate action affecting the listed company during the listed time frame, this market will resolve based on split-adjusted prices as displayed on Yahoo Finance.",
    "assets_ids": [
        "76043073756653678226373981964075571318267289248134717369284518995922789326425",
        "31690934263385727664202099278545688007799199447969475608906331829650099442770"
    ],
    "winning_asset_id": "76043073756653678226373981964075571318267289248134717369284518995922789326425",
    "winning_outcome": "Yes",
    "event_message": {
        "id": "125819",
        "ticker": "nvda-above-in-january-2026",
        "slug": "nvda-above-in-january-2026",
        "title": "Will NVIDIA (NVDA) close above ___ end of January?",
        "description": "This market will resolve to \"Yes\" if the official closing price for NVIDIA (NVDA) on the final trading day of January 2026 is higher than the listed price. Otherwise, this market will resolve to \"No\".\n\nIf the final trading day of the month is shortened (for example, due to a market-holiday schedule), the official closing price published for that shortened session will still be used for resolution.\n\nIf no official closing price is published for that session (for example, due to a trading halt into the close, system issue, or other disruption), the market will use the last valid on-exchange trade price of the regular session as the effective closing price.\n\nThe resolution source for this market is Yahoo Finance — specifically, the NVIDIA (NVDA) \"Close\" prices available at https://finance.yahoo.com/quote/NVDA/history, published under \"Historical Prices.\"\n\nIn the event of a stock split, reverse stock split, or similar corporate action affecting the listed company during the listed time frame, this market will resolve based on split-adjusted prices as displayed on Yahoo Finance."
    },
    "timestamp": "1766790415550",
    "event_type": "new_market"
}


Sports Websocket
Overview
Real-time sports results via WebSocket

The Polymarket Sports WebSocket API provides real-time sports results updates. Clients connect to receive live match data including scores, periods, and game status as events happen.
Endpoint:
wss://sports-api.polymarket.com/ws
No authentication is required. This is a public broadcast channel that streams updates for all active sports events.
​
How It Works
Once connected, clients automatically receive JSON messages whenever a sports event updates. There is no subscription message required—simply connect and start receiving data.
​
Connection Management
​
Automatic Ping/Pong Heartbeat
The server sends PING messages at regular intervals. Clients must respond with PONG to maintain the connection.
Parameter	Default	Description
PING Interval	5 seconds	How often the server sends PING messages
PONG Timeout	10 seconds	How long the server waits for a PONG response
If your client doesn’t respond to PING within 10 seconds, the connection will be closed automatically.
​
Connection Health
Server sends PING → Client must respond with PONG
No response within timeout → Connection terminated
Clients should implement automatic reconnection with exponential backoff
​
Session Affinity
The server uses cookie-based session affinity (sports-results cookie) to ensure clients maintain connection to the same backend instance. This is handled automatically by the browser.


Sports Websocket
Message Format
Structure of sports result update messages

Once connected to the Sports WebSocket, clients receive JSON messages whenever a sports event updates. Messages are broadcast to all connected clients automatically.
​
sport_result Message
Emitted when:
A match goes live
The score changes
The period changes (e.g., halftime, overtime)
A match ends
Possession changes (NFL and CFB only)
​
Structure
​
gameId
number
Unique identifier for the game
​
leagueAbbreviation
string
League identifier (e.g., "nfl", "nba", "cs2")
​
homeTeam
string
Home team name or abbreviation
​
awayTeam
string
Away team name or abbreviation
​
status
string
Game status (e.g., "InProgress", "finished")
​
live
boolean
true if the match is currently in progress
​
ended
boolean
true if the match has concluded
​
score
string
Current score (format varies by sport)
​
period
string
Current period (e.g., "Q4", "2H", "2/3")
​
elapsed
string
Time elapsed in current period (e.g., "05:09")
​
finishedTimestamp
string
Timestamp when the match ended (only present when ended: true)
​
turn
string
Team abbreviation with possession (NFL/CFB only)
The turn field is only present for NFL and CFB games and indicates which team currently has the ball.
​
Example Messages
NFL (in progress):
{
  "gameId": 19439,
  "leagueAbbreviation": "nfl",
  "homeTeam": "LAC",
  "awayTeam": "BUF",
  "status": "InProgress",
  "score": "3-16",
  "period": "Q4",
  "elapsed": "5:18",
  "live": true,
  "ended": false,
  "turn": "lac"
}
Esports - CS2 (finished):
{
  "gameId": 1317359,
  "leagueAbbreviation": "cs2",
  "homeTeam": "ARCRED",
  "awayTeam": "The glecs",
  "status": "finished",
  "score": "000-000|2-0|Bo3",
  "period": "2/3",
  "live": false,
  "ended": true
}
​
Slug Format
The slug field follows a consistent naming convention:
{league}-{team1}-{team2}-{date}
Examples:
nfl-buf-kc-2025-01-26 — NFL: Buffalo Bills vs Kansas City Chiefs
nba-lal-bos-2025-02-15 — NBA: LA Lakers vs Boston Celtics
mlb-nyy-bos-2025-04-01 — MLB: NY Yankees vs Boston Red Sox
​
Period Values
Period	Description
1H	First half
2H	Second half
1Q, 2Q, 3Q, 4Q	Quarters (NFL, NBA)
HT	Halftime
FT	Full time (match ended in regulation)
FT OT	Full time with overtime
FT NR	Full time, no result (draw or canceled)
End 1, End 2, etc.	End of inning (MLB)
1/3, 2/3, 3/3	Map number in Bo3 series (Esports)
1/5, 2/5, etc.	Map number in Bo5 series (Esports)
​
Handling Updates
When processing messages, use the gameId field as the unique identifier to update your local state:
// Update or insert based on gameId
setSportsData(prev => {
  const existing = prev.find(item => item.gameId === data.gameId);
  if (existing) {
    return prev.map(item => 
      item.gameId === data.gameId ? data : item
    );
  }
  return [...prev, data];
});

Sports Websocket
Quickstart
Connect to the Sports WebSocket and receive live updates

Connect to the Sports WebSocket to receive real-time sports results. No authentication required—just connect and handle incoming messages.
​
Endpoint
wss://sports-api.polymarket.com/ws
​
JavaScript Example

JavaScript

React Hook
const ws = new WebSocket('wss://sports-api.polymarket.com/ws');

ws.onopen = () => {
  console.log('Connected to Sports WebSocket');
};

ws.onmessage = (event) => {
  // Respond to server PING
  if (event.data === 'ping') {
    ws.send('pong');
    return;
  }

  // Parse and handle sports updates
  const data = JSON.parse(event.data);
  console.log('Update:', data.slug, data.score, data.period);
};

ws.onclose = () => {
  console.log('Disconnected');
  // Reconnect after 1 second
  setTimeout(() => location.reload(), 1000);
};

ws.onerror = (error) => {
  console.error('WebSocket error:', error);
};
​
Critical: PING/PONG Handling
The server sends PING messages every 5 seconds. Your client must respond with PONG to stay connected.
// CORRECT - Handle PING messages
ws.onmessage = (event) => {
  if (event.data === 'ping') {
    ws.send('pong');  // Respond immediately
    return;
  }
  // Handle other messages...
  const data = JSON.parse(event.data);
  handleUpdate(data);
};
// WRONG - Ignoring PING messages will disconnect you
ws.onmessage = (event) => {
  const data = JSON.parse(event.data);  // Fails on "ping" string!
  handleUpdate(data);
};
If you don’t respond to PING within 10 seconds, your connection will be terminated.
​
Connection State Management
Always check connection state before sending:
if (ws.readyState === WebSocket.OPEN) {
  ws.send('pong');
} else {
  console.warn('WebSocket not connected');
}
​
Browser Tab Visibility
Connections may drop when browser tabs become inactive. Handle visibility changes:
document.addEventListener('visibilitychange', () => {
  if (!document.hidden && ws.readyState !== WebSocket.OPEN) {
    console.log('Tab became visible, reconnecting...');
    connect();
  }
});
​
Troubleshooting
Connection drops after exactly 10 seconds

Connection keeps dropping frequently

Messages not updating UI

Memory leaks with multiple connections

​
Debugging Tips
Enable verbose logging to diagnose connection issues:
ws.onopen = () => console.log('[connected]');
ws.onclose = (e) => console.log('[closed]', e.code, e.reason);
ws.onerror = (e) => console.error('[error]', e);
ws.onmessage = (e) => console.log('[message]', e.data);
Monitor connection state:
setInterval(() => {
  const states = ['CONNECTING', 'OPEN', 'CLOSING', 'CLOSED'];
  console.log('WebSocket state:', states[ws.readyState]);
}, 5000);


