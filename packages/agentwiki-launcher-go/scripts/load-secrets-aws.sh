#!/usr/bin/env bash
# Source this file (do not exec) to populate Apple signing env vars from
# AWS Secrets Manager. Local-dev convenience — CI reads GitHub Actions
# secrets directly and never calls this script.
#
#   source scripts/load-secrets-aws.sh
#   make release
#
# Overrides:
#   AWS_PROFILE  (default: admin)
#   AWS_REGION   (default: us-east-2)

: "${AWS_PROFILE:=admin}"
: "${AWS_REGION:=us-east-2}"

_aws_secret() {
  aws --profile "$AWS_PROFILE" secretsmanager get-secret-value \
    --region "$AWS_REGION" --secret-id "$1" \
    --query SecretString --output text
}

APPLE_ID="$(_aws_secret deploy/apple-id)"
APPLE_TEAM_ID="$(_aws_secret deploy/apple-team-id)"
APPLE_APP_PASSWORD="$(_aws_secret deploy/apple-password)"
APPLE_CERT_BASE64="$(_aws_secret deploy/apple-certificate)"
APPLE_CERT_PASSWORD="$(_aws_secret deploy/apple-certificate-password)"
export APPLE_ID APPLE_TEAM_ID APPLE_APP_PASSWORD APPLE_CERT_BASE64 APPLE_CERT_PASSWORD

unset -f _aws_secret

echo "Apple signing env populated (apple-id=$APPLE_ID, team-id=$APPLE_TEAM_ID)"
