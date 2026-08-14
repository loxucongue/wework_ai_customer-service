# V3 Sidecar Deployment Runbook

## Goal

Run V3 as an independent grey-release service without replacing the existing V1/V2 service.

V3 keeps the platform workflow-compatible request shape, but uses its own URL:

- `POST /reply/workflow-compatible-v3`
- `GET /health-v3`

The normal V1/V2 service keeps using:

- `POST /reply/workflow-compatible`
- `POST /reply/workflow-compatible-v2`
- `GET /health`

## Service Boundary

Use a separate release root:

- V1/V2: `/opt/ai-paths/current`
- V3: `/opt/ai-paths-v3/current`

Use a separate systemd service:

- V1/V2: `ai-paths.service`
- V3: `ai-paths-v3.service`

Use a separate local port:

- V1/V2: existing service port
- V3: `8013`

V3 environment requirements:

```env
AI_PATHS_SERVICE_ROLE=model_led_sales_brain_v3
AI_PATHS_BACKGROUND_WORKERS_ENABLED=false
```

`AI_PATHS_BACKGROUND_WORKERS_ENABLED=false` is required because V3 shares business data but must not run duplicate SOP, outreach, retention, or snapshot workers.

## Nginx Routing

Add V3 routes without changing existing V1/V2 routes:

```nginx
location = /reply/workflow-compatible-v3 {
    proxy_pass http://127.0.0.1:8013/reply/workflow-compatible-v3;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-AI-Paths-V3-Trusted-Proxy 1;
    proxy_read_timeout 180s;
    proxy_send_timeout 180s;
}

location = /health-v3 {
    proxy_pass http://127.0.0.1:8013/health;
}
```

The V3 route also accepts the existing bearer workflow token. The trusted proxy header is only accepted from local or explicitly trusted proxy hosts.

## Shared Data

V3 may read the same authoritative business data as V1/V2:

- store visibility and store snapshot
- order facts
- payment facts
- read-only SOP configuration
- read-only business rules

V3 runtime observations must be versioned:

- `interface_version=v3`
- `reply_chain_mode=model_led_sales_brain_v3`
- `v3_sidecar=true`

Persistence keys for V3 sidecar data must include:

- `corp_id`
- `wechat`
- `external_userid` or `customer_id`
- `interface_version`

Do not reuse V1/V2 send-once, SOP progress, or model-observation keys without a version boundary.

## Smoke Test

After deployment:

```powershell
curl http://47.252.81.104/health-v3
```

Expected health release fields must show the V3 release identity.

Then send an isolated workflow-compatible request to:

```text
POST http://47.252.81.104/reply/workflow-compatible-v3
```

The request must use synthetic or approved grey customer IDs only. Confirm in run logs:

- `interface_version` is `v3`
- `reply_chain_mode` is `model_led_sales_brain_v3`
- `v3_sidecar` is `true`
- no V1/V2 route handled the request

## Rollback

Rollback V3 by removing the platform grey URL or stopping only:

```powershell
systemctl stop ai-paths-v3.service
```

Do not restart or relink `/opt/ai-paths/current` when rolling back V3.
