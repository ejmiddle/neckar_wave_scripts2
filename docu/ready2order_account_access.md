# ready2order Account Access

`READY2ORDER_BILL_API_TOKEN` must be the **Account Token**, not the Developer Token.

`.env`:

```env
READY2ORDER_BILL_DEV_API_TOKEN=...
READY2ORDER_BILL_API_TOKEN=...
```

Use the Developer Token only to create a temporary approval link:

```bash
curl -X POST "https://api.ready2order.com/v1/developerToken/grantAccessToken" \
  -H "Authorization: Bearer $READY2ORDER_BILL_DEV_API_TOKEN" \
  -H "Accept: application/json" \
  -H "Content-Type: application/json" \
  -d '{}'
```

Open `grantAccessUri` within 10 minutes, approve the ready2order account access, then store the returned `accountToken` as `READY2ORDER_BILL_API_TOKEN`.

Do not send `authorizationCallbackUri: null`; ready2order rejects it. Only send `authorizationCallbackUri` when it is a real callback URL.

Verify:

```bash
UV_CACHE_DIR=.uv-cache PYTHONPATH=. uv run python scripts/download_ready2order_product_sales.py
```
