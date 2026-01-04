# Wallet & Polymarket Explanation

## Key Concept: MetaMask IS Your Polygon Wallet

**MetaMask can work on multiple networks:**
- Ethereum Mainnet
- Polygon (what Polymarket uses)
- Other networks

When you use MetaMask on Polygon network, **that IS your Polygon wallet**. You don't need a separate wallet.

## How It Works

### 1. MetaMask on Polygon Network
- MetaMask supports Polygon network
- Same wallet, different network
- Your private key works on both Ethereum and Polygon

### 2. Polymarket Runs on Polygon
- Polymarket is a **decentralized exchange (DEX)** on Polygon
- You don't "transfer money to Polymarket"
- You connect your Polygon wallet (MetaMask) to Polymarket
- Your funds stay in YOUR wallet, not Polymarket's

### 3. The Private Key
- Your MetaMask private key (on Polygon network) = Polygon wallet private key
- Same key works for both
- No need for separate wallets

## Step-by-Step Setup

### Step 1: Add Polygon to MetaMask
1. Open MetaMask
2. Click network dropdown (top)
3. Click "Add Network" or "Add a network manually"
4. Enter Polygon details:
   - Network Name: Polygon Mainnet
   - RPC URL: https://polygon-rpc.com
   - Chain ID: 137
   - Currency Symbol: MATIC
   - Block Explorer: https://polygonscan.com

### Step 2: Fund Your Wallet (If Trading)
**For just reading orderbooks, you DON'T need funds!**

If you want to trade:
1. Get USDC on Polygon (not Ethereum!)
   - Bridge from Ethereum: https://portal.polygon.technology/bridge
   - Buy on exchange and withdraw to Polygon
   - Use a DEX like Uniswap on Polygon
2. Send USDC to your MetaMask address (on Polygon network)

### Step 3: Get Your Private Key
1. In MetaMask, click account menu (three dots)
2. Select "Account Details"
3. Click "Show Private Key"
4. Enter password
5. Copy the private key (starts with `0x`)

**This is your Polygon wallet private key!** Same wallet, just on Polygon network.

### Step 4: Use in Code
```bash
# In .env file
POLYGON_WALLET_PRIVATE_KEY=0xYourMetaMaskPrivateKeyHere
```

## Important Notes

### You DON'T Need to Transfer Money to Polymarket
- Polymarket is a DEX - your funds stay in YOUR wallet
- When you trade, transactions happen on-chain
- Polymarket just facilitates the trades

### Same Wallet, Different Network
- MetaMask on Ethereum = Ethereum wallet
- MetaMask on Polygon = Polygon wallet
- Same private key, different network
- You can switch networks in MetaMask

### For Orderbook Logging
- **You don't need any funds** to read orderbooks
- The private key is just for API authentication
- No money needs to be in the wallet

### For Trading (Future)
- If you want to trade later, you'll need USDC on Polygon
- But for now, just logging orderbooks = no funds needed

## Security Reminder

⚠️ **Never share your private key!**
- Keep it in `.env` file (already in `.gitignore`)
- Use environment variables in production
- Consider using a separate wallet for bots (not your main wallet)

