# Fee Rate (fee_rate_bps) Explanation

## What is `fee_rate_bps`?

`fee_rate_bps` stands for **"Fee Rate in Basis Points"**. It's a parameter you must specify when placing orders on Polymarket's CLOB (Central Limit Order Book).

### Basis Points (BPS) Conversion

- **1 BPS = 0.01%**
- **100 BPS = 1%**
- **1000 BPS = 10%**
- **10000 BPS = 100%**

### Example Conversions

```python
fee_rate_bps = 1000  # = 10%
fee_rate_bps = 100   # = 1%
fee_rate_bps = 10    # = 0.1%
fee_rate_bps = 1     # = 0.01%
```

## Why Do We Need to Specify It?

When placing an order, you must specify what fee rate your order is willing to accept. This is important because:

1. **Market Matching**: The exchange needs to know if your order can match with existing orders that have different fee rates
2. **Fee Structure**: Different markets may have different fee structures (maker vs taker fees)
3. **Order Validation**: The exchange validates that your specified fee rate matches the market's requirements

## The Error We Encountered

When we first tried to place an order without specifying `fee_rate_bps`, we got this error:

```
invalid fee rate (0), current market's taker fee: 1000
```

This means:
- We didn't specify a fee rate (defaulted to 0)
- The market requires a taker fee of **1000 BPS (10%)**
- Our order was rejected because the fee rates didn't match

## Maker vs Taker Fees

### Important Distinction: Limit Order ≠ Maker

**Both limit orders and market orders can be makers or takers!** The distinction depends on whether your order fills immediately or sits on the book.

### Taker (Fills Immediately)
- **Taker**: Your order matches an existing order on the book immediately
- **Limit orders can be takers**: If you place a limit BUY at the best ask (or better), it fills immediately = TAKER
- **Market orders are always takers**: They always fill immediately
- **Taker Fee**: Fee paid when your order matches an existing order
- **For BTC 15-minute markets**: Typically **1000 BPS (10%)** for takers

### Maker (Sits on Book)
- **Maker**: Your order sits on the order book, waiting to be matched (adds liquidity)
- **Limit orders can be makers**: If you place a limit BUY below the best ask, it sits on the book = MAKER
- **Maker Fee**: Often lower or zero (makers provide liquidity and may get rebates)
- **For BTC 15-minute markets**: Makers may receive rebates instead of paying fees

### Example Scenarios

**Scenario 1: Limit Order as Taker**
```python
# Best ask is $0.99
# You place limit BUY at $0.99 (or $1.00)
# → Fills immediately = TAKER (needs fee_rate_bps=1000)
pm.execute_order(price=0.99, size=1.0, side=BUY, token_id=token_id, fee_rate_bps=1000)
```

**Scenario 2: Limit Order as Maker**
```python
# Best ask is $0.99
# You place limit BUY at $0.95
# → Sits on book = MAKER (might use fee_rate_bps=0 or lower)
pm.execute_order(price=0.95, size=1.0, side=BUY, token_id=token_id, fee_rate_bps=0)
```

## How It Works in Practice

### When Placing a Limit Order That Fills Immediately (Taker)

If you place a limit BUY order **at or above** the best ask price (or a limit SELL **at or below** the best bid), it will fill immediately. Even though it's a limit order, you're a **taker** because it matches immediately:

```python
# Limit order at best ask (fills immediately = taker)
pm.execute_order(
    price=0.99,           # Best ask price (or better)
    size=1.02,            # Number of shares
    side=BUY,
    token_id=token_id,
    fee_rate_bps=1000     # Market's taker fee (10%) - required!
)
```

### When Placing a Limit Order That Sits on Book (Maker)

If you place a limit order **away from** the market (e.g., bid below best ask for BUY, ask above best bid for SELL), it sits on the book. This makes you a **maker**, and you might use a different fee rate:

```python
# Limit order below market (sits on book = maker)
pm.execute_order(
    price=0.95,           # Below best ask
    size=1.0,
    side=BUY,
    token_id=token_id,
    fee_rate_bps=0        # Maker fee (often 0 or lower)
)
```

## Current Implementation

In our code, we set the default to `fee_rate_bps=1000` (10%) because:

1. **Most orders are marketable**: When testing or executing strategies, orders often fill immediately
2. **BTC 15-minute markets**: These markets typically have a 10% taker fee
3. **Conservative default**: Better to use a higher fee rate that works than risk rejection

### Code Location

```python
def execute_order(self, price, size, side, token_id, fee_rate_bps: int = 1000) -> Dict:
    """
    Place a limit order.
    
    Args:
        ...
        fee_rate_bps: Fee rate in basis points (default: 1000 = 10%)
    """
    return self.client.create_and_post_order(
        OrderArgs(price=price, size=size, side=side, token_id=token_id, fee_rate_bps=fee_rate_bps)
    )
```

## How to Determine the Correct Fee Rate

### Option 1: Query the Market
You can query the market's fee structure via the CLOB API to get the exact taker/maker fees required.

### Option 2: Try and Error
Start with `fee_rate_bps=1000` (10%) for marketable orders. If you get an error, check the error message which will tell you the required fee rate.

### Option 3: Check Market Type
- **BTC 15-minute markets**: Usually 1000 BPS (10%) for takers
- **Other markets**: May have different fee structures
- **Maker orders**: Often 0 BPS (no fee) or lower

## Important Notes

1. **Fee Rate ≠ Actual Fee Charged**: The `fee_rate_bps` parameter specifies what fee rate your order accepts, not necessarily what you'll pay. The actual fee depends on whether you're a maker or taker.

2. **Order Matching**: Orders can only match if their fee rates are compatible. A taker order with `fee_rate_bps=1000` can match with a maker order that accepts that fee structure.

3. **Market-Specific**: Different markets may have different fee structures. Always check the market's requirements before placing orders.

4. **Fee Calculation**: The actual fee you pay is calculated based on:
   - Whether you're maker or taker
   - The market's fee structure
   - The order size and price
   - The fee_rate_bps you specified

## Example: Fee Calculation

If you place a taker order with:
- Size: 1.02 shares
- Price: $0.99
- Fee rate: 1000 BPS (10%)

The fee calculation would be:
- Order value: 1.02 × $0.99 = $1.0098
- Fee: $1.0098 × 10% = ~$0.101
- Net cost: $1.0098 + $0.101 = ~$1.11

(Note: Actual fee calculation may vary based on Polymarket's specific fee structure)

## Summary

- **fee_rate_bps**: Fee rate in basis points (1000 = 10%)
- **Required parameter**: Must match the market's fee structure
- **Default**: 1000 BPS (10%) for marketable/taker orders
- **Market-specific**: Different markets have different fee structures
- **Maker vs Taker**: Different fee rates apply depending on order type

