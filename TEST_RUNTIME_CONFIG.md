# Testing Runtime Configuration Locally

This guide will help you verify that the runtime configuration works correctly.

## Quick Test

Run the automated test script:

```bash
./scripts/test-runtime-config.sh
```

This will:

1. Build the frontend image
2. Run it with a dummy API URL: `https://dummy-api.example.com/api`
3. Show you the logs and generated config
4. Start the container on http://localhost:3001

## Manual Testing Steps

### Step 1: Build the Image

```bash
cd frontend
docker build -t arcl-frontend-test:local .
cd ..
```

### Step 2: Run with Dummy URL

```bash
docker run -d \
  --name arcl-frontend-test \
  -p 3001:3000 \
  -e NEXT_PUBLIC_API_URL="https://dummy-api.example.com/api" \
  arcl-frontend-test:local
```

### Step 3: Verify Configuration

Check the logs:

```bash
docker logs arcl-frontend-test
```

Expected output:

```
🚀 Starting ARCL CMP Frontend...
📝 Configuring runtime environment...
✅ API URL configured: https://dummy-api.example.com/api
🌐 Starting Next.js server...
```

Check the generated config file:

```bash
docker exec arcl-frontend-test cat /app/public/config.js
```

Expected output:

```javascript
window.__RUNTIME_CONFIG__ = {
  apiUrl: "https://dummy-api.example.com/api",
};
```

Test the endpoint:

```bash
curl http://localhost:3001/config.js
```

### Step 4: Test in Browser

1. Open http://localhost:3001 in your browser

2. Open DevTools (F12)

3. Go to Console tab and type:

   ```javascript
   window.__RUNTIME_CONFIG__;
   ```

   Expected output:

   ```javascript
   {
     apiUrl: "https://dummy-api.example.com/api";
   }
   ```

4. Go to Network tab and refresh the page

5. Look for API calls - they should go to:
   - `https://dummy-api.example.com/api/catalog/`
   - `https://dummy-api.example.com/api/deployments/`

   (They will fail with network errors, but that's OK! We just want to verify the URL is correct)

### Step 5: Test with Different URL

Stop the container:

```bash
docker stop arcl-frontend-test
docker rm arcl-frontend-test
```

Run with a DIFFERENT URL:

```bash
docker run -d \
  --name arcl-frontend-test \
  -p 3001:3000 \
  -e NEXT_PUBLIC_API_URL="https://another-test.com/v1/api" \
  arcl-frontend-test:local
```

Check the logs again:

```bash
docker logs arcl-frontend-test
```

Should show:

```
✅ API URL configured: https://another-test.com/v1/api
```

Refresh your browser and check `window.__RUNTIME_CONFIG__` again - it should show the new URL!

### Step 6: Cleanup

```bash
docker stop arcl-frontend-test
docker rm arcl-frontend-test
docker rmi arcl-frontend-test:local
```

## Success Criteria

✅ Logs show the correct dummy API URL
✅ config.js contains the dummy URL
✅ Browser console shows `window.__RUNTIME_CONFIG__` with dummy URL
✅ Network tab shows API calls going to the dummy URL
✅ Changing env var changes the URL (no rebuild needed!)

## Troubleshooting

### If config.js shows localhost:8000

- Check entrypoint script ran: `docker logs arcl-frontend-test`
- Check env var is set: `docker exec arcl-frontend-test env | grep NEXT_PUBLIC`

### If browser shows undefined

- Check config.js loads: `curl http://localhost:3001/config.js`
- Check browser console for errors
- Hard refresh: Ctrl+Shift+R (or Cmd+Shift+R on Mac)

### If API calls still go to localhost

- Clear browser cache completely
- Try incognito/private window
- Check Network tab for config.js request

## What This Proves

This test proves that:

1. The entrypoint script runs at container startup
2. It generates config.js with the environment variable value
3. The browser loads and uses this runtime configuration
4. You can change the API URL without rebuilding the image

Once this works locally, you can deploy to k3s with confidence!
