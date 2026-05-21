TRADING API (Order Management)

Orders Overview
Detailed instructions for creating, placing, and managing orders using Polymarket’s CLOB API.

All orders are expressed as limit orders (can be marketable). The underlying order primitive must be in the form expected and executable by the on-chain binary limit order protocol contract. Preparing such an order is quite involved (structuring, hashing, signing), thus Polymarket suggests using the open source typescript, python and golang libraries.
​
Allowances
To place an order, allowances must be set by the funder address for the specified maker asset for the Exchange contract. When buying, this means the funder must have set a USDC allowance greater than or equal to the spending amount. When selling, the funder must have set an allowance for the conditional token that is greater than or equal to the selling amount. This allows the Exchange contract to execute settlement according to the signed order instructions created by a user and matched by the operator.
​
Signature Types
Polymarket’s CLOB supports 3 signature types. Orders must identify what signature type they use. The available typescript and python clients abstract the complexity of signing and preparing orders with the following signature types by allowing a funder address and signer type to be specified on initialization. The supported signature types are:
Type	ID	Description
EOA	0	EIP712 signature signed by an EOA
POLY_PROXY	1	EIP712 signatures signed by a signer associated with funding Polymarket proxy wallet
POLY_GNOSIS_SAFE	2	EIP712 signatures signed by a signer associated with funding Polymarket gnosis safe wallet
​
Validity Checks
Orders are continually monitored to make sure they remain valid. Specifically, this includes continually tracking underlying balances, allowances and on-chain order cancellations. Any maker that is caught intentionally abusing these checks (which are essentially real time) will be blacklisted.
Additionally, there are rails on order placement in a market. Specifically, you can only place orders that sum to less than or equal to your available balance for each market. For example if you have 500 USDC in your funding wallet, you can place one order to buy 1000 YES in marketA @ $.50, then any additional buy orders to that market will be rejected since your entire balance is reserved for the first (and only) buy order. More explicitly the max size you can place for an order is:
maxOrderSize
=
underlyingAssetBalance
−
∑
(
orderSize
−
orderFillAmount
)
maxOrderSize=underlyingAssetBalance−∑(orderSize−orderFillAmount)

Place Single Order
Detailed instructions for creating, placing, and managing orders using Polymarket’s CLOB API.

​
Create and Place an Order
This endpoint requires a L2 Header
Create and place an order using the Polymarket CLOB API clients. All orders are represented as “limit” orders, but “market” orders are also supported. To place a market order, simply ensure your price is marketable against current resting limit orders, which are executed on input at the best price.
HTTP REQUEST
POST /<clob-endpoint>/order
​
Request Payload Parameters
Name	Required	Type	Description
order	yes	Order	signed object
owner	yes	string	api key of order owner
orderType	yes	string	order type (“FOK”, “GTC”, “GTD”)
postOnly	no	boolean	if true, the order will only rest on the book and not match immediately (default: false)
​
Post-only orders
postOnly submits a limit order that will not match resting liquidity upon entry.
If a postOnly order would cross the spread (i.e., it is marketable), it will be rejected rather than executed.
postOnly cannot be combined with market order types (e.g., FOK or FAK). If postOnly = true is sent with a market order type, the order will be rejected.
An order object is the form:
Name	Required	Type	Description
salt	yes	integer	random salt used to create unique order
maker	yes	string	maker address (funder)
signer	yes	string	signing address
taker	yes	string	taker address (operator)
tokenId	yes	string	ERC1155 token ID of conditional token being traded
makerAmount	yes	string	maximum amount maker is willing to spend
takerAmount	yes	string	minimum amount taker will pay the maker in return
expiration	yes	string	unix expiration timestamp
nonce	yes	string	maker’s exchange nonce of the order is associated
feeRateBps	yes	string	fee rate basis points as required by the operator
side	yes	string	buy or sell enum index
signatureType	yes	integer	signature type enum index
signature	yes	string	hex encoded signature
​
Order types
FOK: A Fill-Or-Kill order is an market order to buy (in dollars) or sell (in shares) shares that must be executed immediately in its entirety; otherwise, the entire order will be cancelled.
FAK: A Fill-And-Kill order is a market order to buy (in dollars) or sell (in shares) that will be executed immediately for as many shares as are available; any portion not filled at once is cancelled.
GTC: A Good-Til-Cancelled order is a limit order that is active until it is fulfilled or cancelled.
GTD: A Good-Til-Date order is a type of order that is active until its specified date (UTC seconds timestamp), unless it has already been fulfilled or cancelled. There is a security threshold of one minute. If the order needs to expire in 90 seconds the correct expiration value is: now + 1 minute + 30 seconds
​
Response Format
Name	Type	Description
success	boolean	boolean indicating if server-side err (success = false) -> server-side error
errorMsg	string	error message in case of unsuccessful placement (in case success = false, e.g. client-side error, the reason is in errorMsg)
orderId	string	id of order
orderHashes	string[]	hash of settlement transaction order was marketable and triggered a match
​
Insert Error Messages
If the errorMsg field of the response object from placement is not an empty string, the order was not able to be immediately placed. This might be because of a delay or because of a failure. If the success is not true, then there was an issue placing the order. The following errorMessages are possible:
​
Error
Error	Success	Message	Description
INVALID_ORDER_MIN_TICK_SIZE	yes	order is invalid. Price breaks minimum tick size rules	order price isn’t accurate to correct tick sizing
INVALID_ORDER_MIN_SIZE	yes	order is invalid. Size lower than the minimum	order size must meet min size threshold requirement
INVALID_ORDER_DUPLICATED	yes	order is invalid. Duplicated. Same order has already been placed, can’t be placed again	
INVALID_ORDER_NOT_ENOUGH_BALANCE	yes	not enough balance / allowance	funder address doesn’t have sufficient balance or allowance for order
INVALID_ORDER_EXPIRATION	yes	invalid expiration	expiration field expresses a time before now
INVALID_ORDER_ERROR	yes	could not insert order	system error while inserting order
INVALID_POST_ONLY_ORDER_TYPE	yes	invalid post-only order: only GTC and GTD order types are allowed	post only flag attached to a market order
INVALID_POST_ONLY_ORDER	yes	invalid post-only order: order crosses book	post only order would match
EXECUTION_ERROR	yes	could not run the execution	system error while attempting to execute trade
ORDER_DELAYED	no	order match delayed due to market conditions	order placement delayed
DELAYING_ORDER_ERROR	yes	error delaying the order	system error while delaying order
FOK_ORDER_NOT_FILLED_ERROR	yes	order couldn’t be fully filled, FOK orders are fully filled/killed	FOK order not fully filled so can’t be placed
MARKET_NOT_READY	no	the market is not yet ready to process new orders	system not accepting orders for market yet
​
Insert Statuses
When placing an order, a status field is included. The status field provides additional information regarding the order’s state as a result of the placement. Possible values include:
​
Status
Status	Description
matched	order placed and matched with an existing resting order
live	order placed and resting on the book
delayed	order marketable, but subject to matching delay
unmatched	order marketable, but failure delaying, placement successful

Order Management
Place Multiple Orders (Batching)
Instructions for placing multiple orders(Batch)

This endpoint requires a L2 Header
Polymarket’s CLOB supports batch orders, allowing you to place up to 15 orders in a single request. Before using this feature, make sure you’re comfortable placing a single order first. You can find the documentation for that here.
HTTP REQUEST
POST /<clob-endpoint>/orders
​
Request Payload Parameters
Name	Required	Type	Description
PostOrder	yes	PostOrders[]	list of signed order objects (Signed Order + Order Type + Owner)
A PostOrder object is the form:
Name	Required	Type	Description
order	yes	order	See below table for details on crafting this object
orderType	yes	string	order type (“FOK”, “GTC”, “GTD”, “FAK”)
owner	yes	string	api key of order owner
postOnly	no	boolean	if true, the order will only rest on the book and not match immediately (default: false)
An order object is the form:
Name	Required	Type	Description
salt	yes	integer	random salt used to create unique order
maker	yes	string	maker address (funder)
signer	yes	string	signing address
taker	yes	string	taker address (operator)
tokenId	yes	string	ERC1155 token ID of conditional token being traded
makerAmount	yes	string	maximum amount maker is willing to spend
takerAmount	yes	string	minimum amount taker will pay the maker in return
expiration	yes	string	unix expiration timestamp
nonce	yes	string	maker’s exchange nonce of the order is associated
feeRateBps	yes	string	fee rate basis points as required by the operator
side	yes	string	buy or sell enum index
signatureType	yes	integer	signature type enum index
signature	yes	string	hex encoded signature
​
Order types
FOK: A Fill-Or-Kill order is an market order to buy (in dollars) or sell (in shares) shares that must be executed immediately in its entirety; otherwise, the entire order will be cancelled.
FAK: A Fill-And-Kill order is a market order to buy (in dollars) or sell (in shares) that will be executed immediately for as many shares as are available; any portion not filled at once is cancelled.
GTC: A Good-Til-Cancelled order is a limit order that is active until it is fulfilled or cancelled.
GTD: A Good-Til-Date order is a type of order that is active until its specified date (UTC seconds timestamp), unless it has already been fulfilled or cancelled. There is a security threshold of one minute. If the order needs to expire in 90 seconds the correct expiration value is: now + 1 minute + 30 seconds
​
Response Format
Name	Type	Description
success	boolean	boolean indicating if server-side err (success = false) -> server-side error
errorMsg	string	error message in case of unsuccessful placement (in case success = false, e.g. client-side error, the reason is in errorMsg)
orderId	string	id of order
orderHashes	string[]	hash of settlement transaction order was marketable and triggered a match
​
Insert Error Messages
If the errorMsg field of the response object from placement is not an empty string, the order was not able to be immediately placed. This might be because of a delay or because of a failure. If the success is not true, then there was an issue placing the order. The following errorMessages are possible:
​
Error
Error	Success	Message	Description
INVALID_ORDER_MIN_TICK_SIZE	yes	order is invalid. Price breaks minimum tick size rules	order price isn’t accurate to correct tick sizing
INVALID_ORDER_MIN_SIZE	yes	order is invalid. Size lower than the minimum	order size must meet min size threshold requirement
INVALID_ORDER_DUPLICATED	yes	order is invalid. Duplicated. Same order has already been placed, can’t be placed again	
INVALID_ORDER_NOT_ENOUGH_BALANCE	yes	not enough balance / allowance	funder address doesn’t have sufficient balance or allowance for order
INVALID_ORDER_EXPIRATION	yes	invalid expiration	expiration field expresses a time before now
INVALID_ORDER_ERROR	yes	could not insert order	system error while inserting order
INVALID_POST_ONLY_ORDER_TYPE	yes	invalid post-only order: only GTC and GTD order types are allowed	post only flag attached to a market order
INVALID_POST_ONLY_ORDER	yes	invalid post-only order: order crosses book	post only order would match
EXECUTION_ERROR	yes	could not run the execution	system error while attempting to execute trade
ORDER_DELAYED	no	order match delayed due to market conditions	order placement delayed
DELAYING_ORDER_ERROR	yes	error delaying the order	system error while delaying order
FOK_ORDER_NOT_FILLED_ERROR	yes	order couldn’t be fully filled, FOK orders are fully filled/killed	FOK order not fully filled so can’t be placed
MARKET_NOT_READY	no	the market is not yet ready to process new orders	system not accepting orders for market yet
​
Insert Statuses
When placing an order, a status field is included. The status field provides additional information regarding the order’s state as a result of the placement. Possible values include:
​
Status
Status	Description
matched	order placed and matched with an existing resting order
live	order placed and resting on the book
delayed	order marketable, but subject to matching delay
unmatched	order marketable, but failure delaying, placement successful

Order Management
Get Order
Get information about an existing order

This endpoint requires a L2 Header.
Get single order by id.
HTTP REQUEST
GET /<clob-endpoint>/data/order/<order_hash>
​
Request Parameters
Name	Required	Type	Description
id	no	string	id of order to get information about
​
Response Format
Name	Type	Description
order	OpenOrder	order if it exists
An OpenOrder object is of the form:
Name	Type	Description
associate_trades	string[]	any Trade id the order has been partially included in
id	string	order id
status	string	order current status
market	string	market id (condition id)
original_size	string	original order size at placement
outcome	string	human readable outcome the order is for
maker_address	string	maker address (funder)
owner	string	api key
price	string	price
side	string	buy or sell
size_matched	string	size of order that has been matched/filled
asset_id	string	token id
expiration	string	unix timestamp when the order expired, 0 if it does not expire
type	string	order type (GTC, FOK, GTD)
created_at	string	unix timestamp when the order was created

Order Management
Get Active Orders
This endpoint requires a L2 Header.
Get active order(s) for a specific market.
HTTP REQUEST
GET /<clob-endpoint>/data/orders
​
Request Parameters
Name	Required	Type	Description
id	no	string	id of order to get information about
market	no	string	condition id of market
asset_id	no	string	id of the asset/token
​
Response Format
Name	Type	Description
null	OpenOrder[]	list of open orders filtered by the query parameters

Order Management
Check Order Reward Scoring
Check if an order is eligble or scoring for Rewards purposes

This endpoint requires a L2 Header.
Returns a boolean value where it is indicated if an order is scoring or not.
HTTP REQUEST
GET /<clob-endpoint>/order-scoring?order_id={...}
​
Request Parameters
Name	Required	Type	Description
orderId	yes	string	id of order to get information about
​
Response Format
Name	Type	Description
null	OrdersScoring	order scoring data
An OrdersScoring object is of the form:
Name	Type	Description
scoring	boolean	indicates if the order is scoring or not
​
Check if some orders are scoring
This endpoint requires a L2 Header.
Returns to a dictionary with boolean value where it is indicated if an order is scoring or not.
HTTP REQUEST
POST /<clob-endpoint>/orders-scoring
​
Request Parameters
Name	Required	Type	Description
orderIds	yes	string[]	ids of the orders to get information about
​
Response Format
Name	Type	Description
null	OrdersScoring	orders scoring data
An OrdersScoring object is a dictionary that indicates the order by if it score.

Order Management
Cancel Orders(s)
Multiple endpoints to cancel a single order, multiple orders, all orders or all orders from a single market.

​
Cancel an single Order
This endpoint requires a L2 Header.
Cancel an order.
HTTP REQUEST
DELETE /<clob-endpoint>/order
​
Request Payload Parameters
Name	Required	Type	Description
orderID	yes	string	ID of order to cancel
​
Response Format
Name	Type	Description
canceled	string[]	list of canceled orders
not_canceled		a order id -> reason map that explains why that order couldn’t be canceled

Python

Typescript
resp = client.cancel(order_id="0x38a73eed1e6d177545e9ab027abddfb7e08dbe975fa777123b1752d203d6ac88")
print(resp)
​
Cancel Multiple Orders
This endpoint requires a L2 Header.
HTTP REQUEST
DELETE /<clob-endpoint>/orders
​
Request Payload Parameters
Name	Required	Type	Description
null	yes	string[]	IDs of the orders to cancel
​
Response Format
Name	Type	Description
canceled	string[]	list of canceled orders
not_canceled		a order id -> reason map that explains why that order couldn’t be canceled

Python

Typescript
resp = client.cancel_orders(["0x38a73eed1e6d177545e9ab027abddfb7e08dbe975fa777123b1752d203d6ac88", "0xaaaa..."])
print(resp)
​
Cancel ALL Orders
This endpoint requires a L2 Header.
Cancel all open orders posted by a user.
HTTP REQUEST
DELETE /<clob-endpoint>/cancel-all
​
Response Format
Name	Type	Description
canceled	string[]	list of canceled orders
not_canceled		a order id -> reason map that explains why that order couldn’t be canceled

Python

Typescript
resp = client.cancel_all()
print(resp)
print("Done!")
​
Cancel orders from market
This endpoint requires a L2 Header.
Cancel orders from market.
HTTP REQUEST
DELETE /<clob-endpoint>/cancel-market-orders
​
Request Payload Parameters
Name	Required	Type	Description
market	no	string	condition id of the market
asset_id	no	string	id of the asset/token
​
Response Format
Name	Type	Description
canceled	string[]	list of canceled orders
not_canceled		a order id -> reason map that explains why that order couldn’t be canceled

Python

Typescript
resp = client.cancel_market_orders(market="0xbd31dc8a20211944f6b70f31557f1001557b59905b7738480ca09bd4532f84af", asset_id="52114319501245915516055106046884209969926127482827954674443846427813813222426")
print(resp)

Order Management
Onchain Order Info
​
How do I interpret the OrderFilled onchain event?
Given an OrderFilled event:
orderHash: a unique hash for the Order being filled
maker: the user generating the order and the source of funds for the order
taker: the user filling the order OR the Exchange contract if the order fills multiple limit orders
makerAssetId: id of the asset that is given out. If 0, indicates that the Order is a BUY, giving USDC in exchange for Outcome tokens. Else, indicates that the Order is a SELL, giving Outcome tokens in exchange for USDC.
takerAssetId: id of the asset that is received. If 0, indicates that the Order is a SELL, receiving USDC in exchange for Outcome tokens. Else, indicates that the Order is a BUY, receiving Outcome tokens in exchange for USDC.
makerAmountFilled: the amount of the asset that is given out.
takerAmountFilled: the amount of the asset that is received.
fee: the fees paid by the order maker

Trades Overview
​
Overview
All historical trades can be fetched via the Polymarket CLOB REST API. A trade is initiated by a “taker” who creates a marketable limit order. This limit order can be matched against one or more resting limit orders on the associated book. A trade can be in various states as described below. Note: in some cases (due to gas limitations) the execution of a “trade” must be broken into multiple transactions which case separate trade entities will be returned. To associate trade entities, there is a bucket_index field and a match_time field. Trades that have been broken into multiple trade objects can be reconciled by combining trade objects with the same market_order_id, match_time and incrementing bucket_index’s into a top level “trade” client side.
​
Statuses
Status	Terminal?	Description
MATCHED	no	trade has been matched and sent to the executor service by the operator, the executor service submits the trade as a transaction to the Exchange contract
MINED	no	trade is observed to be mined into the chain, no finality threshold established
CONFIRMED	yes	trade has achieved strong probabilistic finality and was successful
RETRYING	no	trade transaction has failed (revert or reorg) and is being retried/resubmitted by the operator
FAILED	yes	trade has failed and is not being retried

Trades
Get Trades
This endpoint requires a L2 Header.
Get trades for the authenticated user based on the provided filters.
HTTP REQUEST
GET /<clob-endpoint>/data/trades
​
Request Parameters
Name	Required	Type	Description
id	no	string	id of trade to fetch
taker	no	string	address to get trades for where it is included as a taker
maker	no	string	address to get trades for where it is included as a maker
market	no	string	market for which to get the trades (condition ID)
before	no	string	unix timestamp representing the cutoff up to which trades that happened before then can be included
after	no	string	unix timestamp representing the cutoff for which trades that happened after can be included
​
Response Format
Name	Type	Description
null	Trade[]	list of trades filtered by query parameters
A Trade object is of the form:
Name	Type	Description
id	string	trade id
taker_order_id	string	hash of taker order (market order) that catalyzed the trade
market	string	market id (condition id)
asset_id	string	asset id (token id) of taker order (market order)
side	string	buy or sell
size	string	size
fee_rate_bps	string	the fees paid for the taker order expressed in basic points
price	string	limit price of taker order
status	string	trade status (see above)
match_time	string	time at which the trade was matched
last_update	string	timestamp of last status update
outcome	string	human readable outcome of the trade
maker_address	string	funder address of the taker of the trade
owner	string	api key of taker of the trade
transaction_hash	string	hash of the transaction where the trade was executed
bucket_index	integer	index of bucket for trade in case trade is executed in multiple transactions
maker_orders	MakerOrder[]	list of the maker trades the taker trade was filled against
type	string	side of the trade: TAKER or MAKER
A MakerOrder object is of the form:
Name	Type	Description
order_id	string	id of maker order
maker_address	string	maker address of the order
owner	string	api key of the owner of the order
matched_amount	string	size of maker order consumed with this trade
fee_rate_bps	string	the fees paid for the taker order expressed in basic points
price	string	price of maker order
asset_id	string	token/asset id
outcome	string	human readable outcome of the maker order
side	string	the side of the maker order. Can be buy or sell


Polymarket Python CLOB Client
PyPI
Python client for the Polymarket Central Limit Order Book (CLOB).

Documentation
Installation
# install from PyPI (Python 3.9>)
pip install py-clob-client
Usage
The examples below are short and copy‑pasteable.

What you need:
Python 3.9+
Private key that owns funds on Polymarket
Optional: a proxy/funder address if you use an email or smart‑contract wallet
Tip: store secrets in environment variables (e.g., with .env)
Quickstart (read‑only)
from py_clob_client.client import ClobClient

client = ClobClient("https://clob.polymarket.com")  # Level 0 (no auth)

ok = client.get_ok()
time = client.get_server_time()
print(ok, time)
Start trading (EOA)
Note: If using MetaMask or hardware wallet, you must first set token allowances. See Token Allowances section below.

from py_clob_client.client import ClobClient

HOST = "https://clob.polymarket.com"
CHAIN_ID = 137
PRIVATE_KEY = "<your-private-key>"
FUNDER = "<your-funder-address>"

client = ClobClient(
    HOST,  # The CLOB API endpoint
    key=PRIVATE_KEY,  # Your wallet's private key
    chain_id=CHAIN_ID,  # Polygon chain ID (137)
    signature_type=1,  # 1 for email/Magic wallet signatures
    funder=FUNDER  # Address that holds your funds
)
client.set_api_creds(client.create_or_derive_api_creds())
Start trading (proxy wallet)
For email/Magic or browser wallet proxies, you need to specify two additional parameters:

Funder Address
The funder address is the actual address that holds your funds on Polymarket. When using proxy wallets (email wallets like Magic or browser extension wallets), the signing key differs from the address holding the funds. The funder address ensures orders are properly attributed to your funded account.

Signature Types
The signature_type parameter tells the system how to verify your signatures:

signature_type=0 (default): Standard EOA (Externally Owned Account) signatures - includes MetaMask, hardware wallets, and any wallet where you control the private key directly
signature_type=1: Email/Magic wallet signatures (delegated signing)
signature_type=2: Browser wallet proxy signatures (when using a proxy contract, not direct wallet connections)
from py_clob_client.client import ClobClient

HOST = "https://clob.polymarket.com"
CHAIN_ID = 137
PRIVATE_KEY = "<your-private-key>"
PROXY_FUNDER = "<your-proxy-or-smart-wallet-address>"  # Address that holds your funds

client = ClobClient(
    HOST,  # The CLOB API endpoint
    key=PRIVATE_KEY,  # Your wallet's private key
    chain_id=CHAIN_ID,  # Polygon chain ID (137)
    signature_type=1,  # 1 for email/Magic wallet signatures
    funder=PROXY_FUNDER  # Address that holds your funds
)
client.set_api_creds(client.create_or_derive_api_creds())
Find markets, prices, and orderbooks
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import BookParams

client = ClobClient("https://clob.polymarket.com")  # read-only

token_id = "<token-id>"  # Get a token ID: https://docs.polymarket.com/developers/gamma-markets-api/get-markets

mid = client.get_midpoint(token_id)
price = client.get_price(token_id, side="BUY")
book = client.get_order_book(token_id)
books = client.get_order_books([BookParams(token_id=token_id)])
print(mid, price, book.market, len(books))
Place a market order (buy by $ amount)
Note: EOA/MetaMask users must set token allowances before trading. See Token Allowances section below.

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import MarketOrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY

HOST = "https://clob.polymarket.com"
CHAIN_ID = 137
PRIVATE_KEY = "<your-private-key>"
FUNDER = "<your-funder-address>"

client = ClobClient(
    HOST,  # The CLOB API endpoint
    key=PRIVATE_KEY,  # Your wallet's private key
    chain_id=CHAIN_ID,  # Polygon chain ID (137)
    signature_type=1,  # 1 for email/Magic wallet signatures
    funder=FUNDER  # Address that holds your funds
)
client.set_api_creds(client.create_or_derive_api_creds())

mo = MarketOrderArgs(token_id="<token-id>", amount=25.0, side=BUY, order_type=OrderType.FOK)  # Get a token ID: https://docs.polymarket.com/developers/gamma-markets-api/get-markets
signed = client.create_market_order(mo)
resp = client.post_order(signed, OrderType.FOK)
print(resp)
Place a limit order (shares at a price)
Note: EOA/MetaMask users must set token allowances before trading. See Token Allowances section below.

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY

HOST = "https://clob.polymarket.com"
CHAIN_ID = 137
PRIVATE_KEY = "<your-private-key>"
FUNDER = "<your-funder-address>"

client = ClobClient(
    HOST,  # The CLOB API endpoint
    key=PRIVATE_KEY,  # Your wallet's private key
    chain_id=CHAIN_ID,  # Polygon chain ID (137)
    signature_type=1,  # 1 for email/Magic wallet signatures
    funder=FUNDER  # Address that holds your funds
)
client.set_api_creds(client.create_or_derive_api_creds())

order = OrderArgs(token_id="<token-id>", price=0.01, size=5.0, side=BUY)  # Get a token ID: https://docs.polymarket.com/developers/gamma-markets-api/get-markets
signed = client.create_order(order)
resp = client.post_order(signed, OrderType.GTC)
print(resp)
Manage orders
Note: EOA/MetaMask users must set token allowances before trading. See Token Allowances section below.

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OpenOrderParams

HOST = "https://clob.polymarket.com"
CHAIN_ID = 137
PRIVATE_KEY = "<your-private-key>"
FUNDER = "<your-funder-address>"

client = ClobClient(
    HOST,  # The CLOB API endpoint
    key=PRIVATE_KEY,  # Your wallet's private key
    chain_id=CHAIN_ID,  # Polygon chain ID (137)
    signature_type=1,  # 1 for email/Magic wallet signatures
    funder=FUNDER  # Address that holds your funds
)
client.set_api_creds(client.create_or_derive_api_creds())

open_orders = client.get_orders(OpenOrderParams())

order_id = open_orders[0]["id"] if open_orders else None
if order_id:
    client.cancel(order_id)

client.cancel_all()
Markets (read‑only)
from py_clob_client.client import ClobClient

client = ClobClient("https://clob.polymarket.com")
markets = client.get_simplified_markets()
print(markets["data"][:1])
User trades (requires auth)
Note: EOA/MetaMask users must set token allowances before trading. See Token Allowances section below.

from py_clob_client.client import ClobClient

HOST = "https://clob.polymarket.com"
CHAIN_ID = 137
PRIVATE_KEY = "<your-private-key>"
FUNDER = "<your-funder-address>"

client = ClobClient(
    HOST,  # The CLOB API endpoint
    key=PRIVATE_KEY,  # Your wallet's private key
    chain_id=CHAIN_ID,  # Polygon chain ID (137)
    signature_type=1,  # 1 for email/Magic wallet signatures
    funder=FUNDER  # Address that holds your funds
)
client.set_api_creds(client.create_or_derive_api_creds())

last = client.get_last_trade_price("<token-id>")
trades = client.get_trades()
print(last, len(trades))
Important: Token Allowances for MetaMask/EOA Users
Do I need to set allowances?
Using email/Magic wallet? No action needed - allowances are set automatically.
Using MetaMask or hardware wallet? You need to set allowances before trading.
What are allowances?
Think of allowances as permissions. Before Polymarket can move your funds to execute trades, you need to give the exchange contracts permission to access your USDC and conditional tokens.

Quick Setup
You need to approve two types of tokens:

USDC (for deposits and trading)
Conditional Tokens (the outcome tokens you trade)
Each needs approval for the exchange contracts to work properly.

Setting Allowances
Here's a simple breakdown of what needs to be approved:

For USDC (your trading currency):

Token: 0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174
Approve for these contracts:
0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E (Main exchange)
0xC5d563A36AE78145C45a50134d48A1215220f80a (Neg risk markets)
0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296 (Neg risk adapter)
For Conditional Tokens (your outcome tokens):

Token: 0x4D97DCd97eC945f40cF65F87097ACe5EA0476045
Approve for the same three contracts above
Example Code
See this Python example for setting allowances programmatically.

Pro tip: You only need to set these once per wallet. After that, you can trade freely.

Notes
To discover token IDs, use the Markets API Explorer: Get Markets.
Prices are in dollars from 0.00 to 1.00. Shares are whole or fractional units of the outcome token.

