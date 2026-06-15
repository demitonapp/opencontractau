#!/usr/bin/env bash
# One-time Azure setup for data.demiton.io OCDS publishing.
# Run this manually once. Requires az login with Owner or Contributor on the subscription.
#
# After this script completes:
#   1. Add CNAME in Netlify: data -> <CDN_ENDPOINT_HOSTNAME>
#   2. Add cdnverify CNAME in Netlify: cdnverify.data -> cdnverify.<CDN_ENDPOINT_HOSTNAME>
#   3. Wait for Azure to issue the managed HTTPS cert (~10 min), then delete the cdnverify record.
#   4. Add AZURE_CLIENT_ID / TENANT_ID / SUBSCRIPTION_ID secrets to the opencontractsau GitHub repo.
set -euo pipefail

RG="rg-demiton-prod-aue"
LOCATION="australiaeast"
STORAGE_ACCOUNT="demitonpublicdata"
CONTAINER="au-contracts"
CDN_PROFILE="demiton-cdn"
CDN_ENDPOINT="demiton-data"
CUSTOM_DOMAIN="data.demiton.io"

echo "==> Creating storage account $STORAGE_ACCOUNT"
az storage account create \
  --name "$STORAGE_ACCOUNT" \
  --resource-group "$RG" \
  --location "$LOCATION" \
  --sku Standard_LRS \
  --kind StorageV2 \
  --allow-blob-public-access true \
  --min-tls-version TLS1_2

echo "==> Creating public container $CONTAINER"
az storage container create \
  --name "$CONTAINER" \
  --account-name "$STORAGE_ACCOUNT" \
  --public-access blob \
  --auth-mode login

echo "==> Setting CORS for browser access"
az storage cors add \
  --account-name "$STORAGE_ACCOUNT" \
  --services b \
  --methods GET HEAD OPTIONS \
  --origins "*" \
  --allowed-headers "*" \
  --exposed-headers "*" \
  --max-age 86400 \
  --auth-mode login

echo "==> Creating CDN profile (Standard Microsoft)"
az cdn profile create \
  --name "$CDN_PROFILE" \
  --resource-group "$RG" \
  --sku Standard_Microsoft

echo "==> Creating CDN endpoint -> blob origin"
az cdn endpoint create \
  --name "$CDN_ENDPOINT" \
  --profile-name "$CDN_PROFILE" \
  --resource-group "$RG" \
  --origin "${STORAGE_ACCOUNT}.blob.core.windows.net" \
  --origin-host-header "${STORAGE_ACCOUNT}.blob.core.windows.net" \
  --enable-compression true \
  --query-string-caching-behavior IgnoreQueryString

CDN_HOSTNAME=$(az cdn endpoint show \
  --name "$CDN_ENDPOINT" \
  --profile-name "$CDN_PROFILE" \
  --resource-group "$RG" \
  --query "hostName" -o tsv)

echo ""
echo "==> CDN endpoint hostname: $CDN_HOSTNAME"
echo ""
echo "==> Add these DNS records in Netlify NOW, then press Enter to continue:"
echo "    data           CNAME  $CDN_HOSTNAME"
echo "    cdnverify.data CNAME  cdnverify.$CDN_HOSTNAME"
read -p "Press Enter once DNS records are saved..."

echo "==> Adding custom domain $CUSTOM_DOMAIN to CDN endpoint"
az cdn custom-domain create \
  --name "data-demiton-io" \
  --endpoint-name "$CDN_ENDPOINT" \
  --profile-name "$CDN_PROFILE" \
  --resource-group "$RG" \
  --hostname "$CUSTOM_DOMAIN"

echo "==> Enabling HTTPS with Azure-managed certificate"
az cdn custom-domain enable-https \
  --name "data-demiton-io" \
  --endpoint-name "$CDN_ENDPOINT" \
  --profile-name "$CDN_PROFILE" \
  --resource-group "$RG"

echo ""
echo "Done. Certificate issuance takes ~10 minutes."
echo "Once https://data.demiton.io/au-contracts/index.json resolves, delete the cdnverify CNAME from Netlify."
echo ""
echo "Next: add federated credential to your Azure managed identity for the opencontractsau GitHub repo:"
echo "  Repo:   demitonapp/opencontractsau"
echo "  Branch: main"
echo "  Entity type: Branch"
