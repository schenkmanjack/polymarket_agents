# How to Get Your Private Key from MetaMask

## Step-by-Step Instructions

### Step 1: Open MetaMask
- Open your MetaMask browser extension or mobile app

### Step 2: Switch to Polygon Network
- Look at the top of MetaMask
- Click the network dropdown (usually shows "Ethereum Mainnet" or current network)
- Select **"Polygon Mainnet"** (or add it if you haven't already)
- You should see "Polygon" at the top

### Step 3: Access Account Details
- Look at the top right of MetaMask
- You'll see your account name/address
- Click the **three dots (⋮)** or **account menu** next to your account name
- A dropdown menu will appear

### Step 4: Show Private Key
- Click **"Account Details"** from the dropdown
- A new window/page will open showing your account information
- Look for a button that says **"Show Private Key"** or **"Export Private Key"**
- Click it

### Step 5: Enter Password
- MetaMask will ask for your password (the one you use to unlock MetaMask)
- Enter your MetaMask password
- Click "Confirm" or "Unlock"

### Step 6: Copy the Private Key
- Your private key will be displayed (it starts with `0x` followed by many characters)
- Click **"Copy"** or select and copy it
- It looks like: `0x1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef`

### Step 7: Add to Your Code

#### For Local Development (.env file):
1. Open your `.env` file in the project root
2. Add this line:
   ```
   POLYGON_WALLET_PRIVATE_KEY=0xYourPrivateKeyHere
   ```
3. Replace `0xYourPrivateKeyHere` with the actual key you copied
4. Save the file

#### For Railway Deployment:
1. Go to Railway → Your Service → Variables tab
2. Click "+ New Variable"
3. Name: `POLYGON_WALLET_PRIVATE_KEY`
4. Value: Paste your private key (starts with `0x`)
5. Click "Add"

## Visual Guide

```
MetaMask Window:
┌─────────────────────────────┐
│  Polygon ▼  [Account Name] ⋮│ ← Click the three dots
├─────────────────────────────┤
│                             │
│  Account Details            │ ← Click this
│  Settings                   │
│  ...                        │
└─────────────────────────────┘

Account Details Window:
┌─────────────────────────────┐
│  Account 1                  │
│  0x1234...5678              │
│                             │
│  [Show Private Key]         │ ← Click this
└─────────────────────────────┘

After entering password:
┌─────────────────────────────┐
│  Your Private Key:          │
│  0xabcdef1234567890...      │
│  [Copy]                     │ ← Copy this
└─────────────────────────────┘
```

## Security Warnings

⚠️ **CRITICAL SECURITY:**

1. **Never share your private key**
   - Don't send it in emails, messages, or chats
   - Don't post it online
   - Don't commit it to Git (`.env` is already in `.gitignore`)

2. **Keep it secure**
   - Store in password manager
   - Only use in `.env` file or environment variables
   - Don't hardcode in your code

3. **Consider a separate wallet**
   - Create a new MetaMask wallet just for bots
   - Don't use your main wallet with lots of funds
   - Transfer minimal funds if needed

4. **If compromised**
   - Immediately transfer funds to a new wallet
   - Revoke any permissions if possible
   - Create a new wallet

## Verify It Works

After adding to `.env`, test it:

```bash
# Check if it's loaded
python -c "import os; from dotenv import load_dotenv; load_dotenv(); print('Key found!' if os.getenv('POLYGON_WALLET_PRIVATE_KEY') else 'Key not found')"
```

## Troubleshooting

**"Private key not showing"**
- Make sure you're on Polygon network
- Try clicking "Account Details" again
- Check you're using the correct account

**"Password not working"**
- Make sure you're using your MetaMask unlock password
- Not your seed phrase password
- Try unlocking MetaMask first, then accessing account details

**"Key doesn't start with 0x"**
- Make sure you copied the full key
- It should be 66 characters total (0x + 64 hex characters)
- If it's shorter, you might have copied the wrong thing

## What This Key Does

- **For orderbook logging**: Used for API authentication (WebSocket access)
- **For trading** (future): Would allow placing orders
- **Does NOT require funds**: Just having the key is enough for API access

## Next Steps

Once you have the key:
1. Add to `.env` file locally
2. Add to Railway environment variables
3. The code will automatically use WebSocket mode (lower latency)
4. You're ready to deploy!

