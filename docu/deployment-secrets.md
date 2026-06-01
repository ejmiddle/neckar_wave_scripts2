# Deployment Secrets

This repo uses one encrypted local source-of-truth file and syncs selected keys into Mittwald through the API.

Conceptually:

- `config/secrets/production.enc.env` is the encrypted source of truth in git
- `secrets.keys` is the local `age` private key and stays out of git
- Mittwald runtime secrets are normal container environment variables
- deployment sync is done by `scripts/deploy_mittwald_service.py`

This is intentionally not runtime SOPS-in-container.

## Why This Setup

This matches the actual hosting model better:

- Mittwald can provide runtime environment variables
- Mittwald can run the normal container entrypoint
- we do not need to decrypt inside the container

That keeps production simpler:

- no `sops` binary in the container
- no `SOPS_AGE_KEY` in the runtime container
- one encrypted file in git for operator convenience

## Files

```text
.sops.yaml
config/secrets/production.enc.env
config/secrets/production.env.example
config/deploy/accounting.env.keys
config/deploy/main.env.keys
scripts/sops-encrypt-env.sh
scripts/sops-decrypt-env.sh
scripts/deploy_mittwald_service.py
secrets.keys            # local only, gitignored
```

## Current Per-App Secret Upload Scope

Accounting deploy uploads only:

- `OPENAI_API_KEY`
- `SEVDESK_KEY`

Main app deploy uploads only:

- `API_BASE_URL`
- `API_BEARER_TOKEN`
- `API_TLS_VERIFY`
- `GEMINI_API_KEY`
- `NOTION_DATABASE_ID`
- `NOTION_TOKEN`
- `OPENAI_API_KEY`
- `SHOPIFY_KEY`

Main app allowlist is configured in:

- `config/deploy/main.env.keys`

Important:

- the encrypted env file may contain more keys than a given service receives
- upload is filtered through the per-app `*.env.keys` file
- runtime-only operator tokens like `MITTWALD_API_TOKEN` and `GHCR_TOKEN` are not uploaded to the app container

## Copy-Paste Commands

All commands below assume you are in the repo root:

```bash
cd /Users/andreasschmidt/CodingProjects/neckarwave_scripts
```

### 1. Generate the local key once

```bash
age-keygen -o secrets.keys
```

Show the public recipient:

```bash
age-keygen -y secrets.keys
```

### 2. Create or update the plaintext production env

```bash
cp config/secrets/production.env.example config/secrets/production.env
${EDITOR:-vi} config/secrets/production.env
```

### 3. Encrypt the production env

Using the helper script:

```bash
export SOPS_AGE_KEY_FILE=./secrets.keys
chmod +x scripts/sops-encrypt-env.sh
./scripts/sops-encrypt-env.sh config/secrets/production.env config/secrets/production.enc.env
```

Using raw `sops`:

```bash
export SOPS_AGE_KEY_FILE=./secrets.keys
sops --encrypt \
  --filename-override config/secrets/production.enc.env \
  --input-type dotenv \
  --output-type dotenv \
  config/secrets/production.env > config/secrets/production.enc.env
```

### 4. Verify the encrypted file

```bash
export SOPS_AGE_KEY_FILE=./secrets.keys
sops --decrypt --input-type dotenv --output-type dotenv config/secrets/production.enc.env >/dev/null
```

Show only the key names:

```bash
export SOPS_AGE_KEY_FILE=./secrets.keys
./scripts/sops-decrypt-env.sh config/secrets/production.enc.env | awk -F= '/^[A-Za-z_][A-Za-z0-9_]*=/{print $1}'
```

### 5. Sync accounting secrets into Mittwald

Requirements:

- `MITTWALD_API_TOKEN` available in your shell or `.env`
- local `secrets.keys` available

```bash
export SOPS_AGE_KEY_FILE=./secrets.keys
UV_CACHE_DIR=.uv-cache uv run python scripts/deploy_mittwald_service.py \
  sync-secrets \
  --project suedseite \
  --service accounting \
  --env-file config/secrets/production.enc.env \
  --env-keys-file config/deploy/accounting.env.keys \
  --recreate
```

### 6. Publish the accounting image and deploy it to Mittwald

This task:

1. builds the image
2. pushes the image to GHCR
3. syncs accounting env vars into Mittwald
4. triggers Mittwald to pull the image and recreate the service

```bash
task acc_docker_publish
```

If you only want to re-sync secrets without rebuilding:

```bash
task acc_mittwald_sync_secrets
```

If you already pushed a new accounting image and only want the Mittwald redeploy step:

```bash
task acc_mittwald_deploy
```

### 7. Sync main app secrets into Mittwald

Requirements:

- `MITTWALD_API_TOKEN` available in your shell or `.env`
- local `secrets.keys` available

By default these tasks target Mittwald service `testcontainer`. If your real service name differs, set `MAIN_MITTWALD_SERVICE=<service-name>`.

```bash
export SOPS_AGE_KEY_FILE=./secrets.keys
UV_CACHE_DIR=.uv-cache uv run python scripts/deploy_mittwald_service.py \
  sync-secrets \
  --project suedseite \
  --service testcontainer \
  --env-file config/secrets/production.enc.env \
  --env-keys-file config/deploy/main.env.keys \
  --recreate
```

### 8. Publish the main image and deploy it to Mittwald

This task:

1. builds the image
2. pushes the image to GHCR
3. syncs main app env vars into Mittwald
4. triggers Mittwald to pull the image and recreate the service

```bash
task main_docker_publish
```

If you only want to re-sync secrets without rebuilding:

```bash
task main_mittwald_sync_secrets
```

If you already pushed a new main image and only want the Mittwald redeploy step:

```bash
task main_mittwald_deploy
```

## What `acc_docker_publish` Actually Does

`acc_docker_publish` now calls:

- `acc_docker_build`
- `acc_docker_push`
- `acc_mittwald_deploy`

`acc_mittwald_deploy` uses:

- project selector: `suedseite`
- Mittwald service: `accounting`
- image: `ghcr.io/ejmiddle/neckarwave-scripts-accounting:latest`
- env source: `config/secrets/production.enc.env`
- env filter: `config/deploy/accounting.env.keys`

## What `main_docker_publish` Actually Does

`main_docker_publish` now calls:

- `main_docker_build`
- `main_docker_push`
- `main_mittwald_deploy`

`main_mittwald_deploy` uses:

- project selector: `suedseite`
- Mittwald service: `testcontainer` by default, overridable with `MAIN_MITTWALD_SERVICE`
- image: `ghcr.io/ejmiddle/neckarwave-scripts-main:latest`
- env source: `config/secrets/production.enc.env`
- env filter: `config/deploy/main.env.keys`

## Git Rules

Commit:

- `.sops.yaml`
- `config/secrets/production.enc.env`
- `config/secrets/production.env.example`
- `config/deploy/accounting.env.keys`
- `config/deploy/main.env.keys`
- the helper scripts

Do not commit:

- `secrets.keys`
- `config/secrets/production.env`
- `.env`

## Security Model

This is basic reasonable security, not high security.

- secrets are encrypted at rest in git
- secrets are decrypted locally before being sent to Mittwald
- Mittwald stores runtime secrets as normal env vars
- the running container sees plaintext env vars, which is expected

What this avoids:

- runtime SOPS decryption in the container
- shipping the SOPS private key into production
- manually re-entering many env vars by hand on every update
