# Troubleshooting Guide

## Common Errors and Solutions

### WebSocket Connection Errors

**Error: "WebSocket connection closed"**
- **Cause**: Connection dropped (network issue, server restart, timeout)
- **Solution**: Code will attempt to reconnect automatically
- **Check**: Railway logs for reconnection attempts

**Error: "RTDS error: ..."**
- **Cause**: Polymarket RTDS returned an error message
- **Common reasons**:
  - Invalid token ID
  - Rate limiting
  - Subscription limit exceeded
- **Solution**: Check the error message in logs for specific details

### Database Errors

**Error: "Error saving orderbook update"**
- **Cause**: Database connection issue or data format problem
- **Common reasons**:
  - Database connection lost
  - Invalid data format from WebSocket
  - Database locked (SQLite) or connection pool exhausted (PostgreSQL)
- **Solution**: 
  - Check DATABASE_URL is correct
  - Verify Neon database is accessible
  - Check database connection pool settings

**Error: "Connection pool exhausted"**
- **Cause**: Too many database connections
- **Solution**: Increase pool size or reduce concurrent operations

### API Errors

**Error: "Failed to fetch events/markets"**
- **Cause**: API request failed
- **Common reasons**:
  - Network timeout
  - API rate limiting
  - Invalid parameters
- **Solution**: Check API endpoint, retry logic, rate limits

### Data Format Errors

**Error: "Error parsing orderbook data"**
- **Cause**: WebSocket message format unexpected
- **Solution**: Code logs the data structure - check logs to see what format was received

## Error Signal Types

The code handles these error types:

1. **RTDS Error Messages**: `{"type": "error", "message": "..."}`
   - Logged as: `RTDS error: ...`
   - Usually indicates subscription or authentication issue

2. **WebSocket Connection Errors**:
   - ConnectionClosed: WebSocket disconnected
   - Network errors: Connection timeout or failure
   - Logged with full stack trace

3. **Database Errors**:
   - Connection errors: Can't connect to database
   - Transaction errors: Failed to save data
   - Logged with full stack trace

4. **Data Parsing Errors**:
   - JSON decode errors: Invalid message format
   - Type errors: Unexpected data structure
   - Logged with data structure info

## Debugging Steps

1. **Check Railway Logs**:
   - Go to Railway → Service → Logs
   - Look for ERROR level messages
   - Check for stack traces

2. **Verify Environment Variables**:
   - DATABASE_URL is set correctly
   - POLYGON_WALLET_PRIVATE_KEY is set (if using WebSocket)

3. **Test Database Connection**:
   ```python
   python scripts/python/test_db_connection.py
   ```

4. **Check WebSocket Connection**:
   - Look for "Connected to RTDS" message
   - Check for "Subscribed" confirmations
   - Watch for "RTDS error" messages

5. **Verify Data is Being Saved**:
   ```python
   python scripts/python/query_orderbook.py --limit 10
   ```

## Common Issues

### No Orderbook Updates
- **Check**: Are subscriptions confirmed? Look for "Successfully subscribed" messages
- **Check**: Is WebSocket connected? Look for "Connected to RTDS"
- **Check**: Is market still active? Markets expire after their time window

### Database Not Saving
- **Check**: Database connection string is correct
- **Check**: Database has write permissions
- **Check**: Connection pool not exhausted
- **Check**: No errors in logs when saving

### Service Keeps Restarting
- **Check**: Railway logs for crash reasons
- **Check**: Memory/CPU limits
- **Check**: Unhandled exceptions causing crashes

## Getting Help

If you see errors:
1. Copy the full error message from Railway logs
2. Include the stack trace (if available)
3. Note when the error occurs (on startup, during operation, etc.)
4. Check if it's intermittent or consistent

