# Wallet Setup Guide

Polymarket uses your Polygon wallet's private key to automatically generate API credentials. You don't need to manually create an API key.

## Step 1: Get a Polygon Wallet

You need a wallet on Polygon network. Options:

### Option A: MetaMask (Recommended)
1. Install MetaMask: https://metamask.io
2. Create a new wallet or import an existing one
3. Add Polygon network:
   - Network Name: Polygon
   - RPC URL: https://polygon-rpc.com
   - Chain ID: 137
   - Currency Symbol: MATIC

### Option B: Use Existing Wallet
If you already have a Polygon wallet, you can use that.

## Step 2: Get Your Private Key

**⚠️ SECURITY WARNING: Your private key gives full access to your wallet. Never share it!**

### From MetaMask:
1. Open MetaMask
2. Click the account menu (three dots)
3. Select "Account Details"
4. Click "Show Private Key"
5. Enter your password
6. Copy the private key (starts with `0x`)

### From Other Wallets:
Check your wallet's documentation for how to export the private key.

## Step 3: Add to Environment Variables

### Local Development (.env file):
```bash
POLYGON_WALLET_PRIVATE_KEY=0xYourPrivateKeyHere
```

### Railway Deployment:
1. Go to Railway → Your Service → Variables
2. Add new variable:
   - Name: `POLYGON_WALLET_PRIVATE_KEY`
   - Value: `0xYourPrivateKeyHere`

## Step 4: Fund Your Wallet (Optional)

If you want to trade, you'll need USDC on Polygon:
- Bridge USDC to Polygon: https://portal.polygon.technology/bridge
- Or buy MATIC/USDC on an exchange and send to Polygon

**Note:** For just reading orderbooks, you don't need funds!

## Step 5: How It Works

The code automatically:
1. Takes your private key
2. Creates/derives API credentials via `create_or_derive_api_creds()`
3. Uses those credentials for authenticated API calls

You don't need to manually create API keys - it's all automatic!

## Security Best Practices

1. **Never commit your private key to Git**
   - Always use `.env` file (already in `.gitignore`)
   - Use environment variables in production

2. **Use a separate wallet for bots**
   - Don't use your main wallet
   - Create a dedicated wallet with minimal funds

3. **Keep private key secure**
   - Store in password manager
   - Don't share or expose it

4. **Monitor wallet activity**
   - Check transactions regularly
   - Set up alerts if possible

## Troubleshooting

### "A private key is needed to interact with this endpoint"
- Make sure `POLYGON_WALLET_PRIVATE_KEY` is set
- Check that it starts with `0x`
- Verify it's a valid Polygon wallet private key

### "Invalid private key"
- Ensure the key starts with `0x`
- Check for extra spaces or newlines
- Verify it's the correct private key (not seed phrase)

### API credentials not working
- The code automatically creates credentials on first run
- Check Railway logs for any errors
- Try redeploying if credentials seem stale

