#!/usr/bin/env bash
#
# post-deploy-test.sh — One-shot end-to-end test for Deep Research Cloud
#
# Wires the invoker Lambda, creates a Cognito user, authenticates,
# submits a research request, and polls until completion.
#
# Usage:
#   ./post-deploy-test.sh
#   ./post-deploy-test.sh --email you@example.com --password 'MyP@ss1234'
#   ./post-deploy-test.sh --query "Compare Bedrock vs SageMaker for RAG"
#   ./post-deploy-test.sh --skip-setup  # Skip user creation + invoker wiring (already done)
#
set -euo pipefail

# ─── Configuration ────────────────────────────────────────────────────

REGION="${AWS_REGION:-us-west-2}"
STAGE="${STAGE:-dev}"
STACK_PREFIX="DeepResearch"

# Defaults (override via flags)
TEST_EMAIL="${TEST_EMAIL:-deepresearch-test@example.com}"
TEST_PASSWORD="${TEST_PASSWORD:-TestP@ssw0rd!2025}"
TEST_QUERY="${TEST_QUERY:-What is Amazon Bedrock AgentCore and how does it compare to self-hosted agent runtimes?}"
TEST_DEPTH="${TEST_DEPTH:-quick}"
POLL_INTERVAL=10
POLL_TIMEOUT=600  # 10 minutes max
SKIP_SETUP=false
VERBOSE=false

# ─── Argument Parsing ─────────────────────────────────────────────────

while [[ $# -gt 0 ]]; do
  case $1 in
    --email) TEST_EMAIL="$2"; shift 2 ;;
    --password) TEST_PASSWORD="$2"; shift 2 ;;
    --query) TEST_QUERY="$2"; shift 2 ;;
    --depth) TEST_DEPTH="$2"; shift 2 ;;
    --timeout) POLL_TIMEOUT="$2"; shift 2 ;;
    --skip-setup) SKIP_SETUP=true; shift ;;
    --verbose|-v) VERBOSE=true; shift ;;
    --help|-h)
      echo "Usage: $0 [OPTIONS]"
      echo ""
      echo "Options:"
      echo "  --email EMAIL        Cognito test user email (default: deepresearch-test@example.com)"
      echo "  --password PASS      Cognito test user password (default: auto-generated)"
      echo "  --query QUERY        Research query to submit"
      echo "  --depth DEPTH        Research depth: quick|standard|comprehensive (default: quick)"
      echo "  --timeout SECONDS    Max seconds to wait for completion (default: 600)"
      echo "  --skip-setup         Skip invoker wiring + user creation (rerun test only)"
      echo "  --verbose, -v        Show detailed output"
      echo "  --help, -h           Show this help"
      exit 0
      ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

# ─── Helpers ──────────────────────────────────────────────────────────

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log()   { echo -e "${BLUE}[INFO]${NC} $*"; }
ok()    { echo -e "${GREEN}[✓]${NC} $*"; }
warn()  { echo -e "${YELLOW}[!]${NC} $*"; }
err()   { echo -e "${RED}[✗]${NC} $*" >&2; }
debug() { [[ "$VERBOSE" == "true" ]] && echo -e "    $*" || true; }

die() { err "$*"; exit 1; }

require_cmd() {
  command -v "$1" &>/dev/null || die "Required command not found: $1"
}

get_stack_output() {
  local stack="$1" key="$2"
  aws cloudformation describe-stacks \
    --stack-name "${STACK_PREFIX}-${stack}-${STAGE}" \
    --query "Stacks[0].Outputs[?OutputKey==\`${key}\`].OutputValue" \
    --output text --region "$REGION" 2>/dev/null
}

# ─── Preflight Checks ────────────────────────────────────────────────

log "Running preflight checks..."
require_cmd aws
require_cmd curl
require_cmd python3
require_cmd jq

# Verify AWS credentials
AWS_ACCOUNT=$(aws sts get-caller-identity --query Account --output text --region "$REGION" 2>/dev/null) \
  || die "AWS credentials not configured. Run 'aws configure' or set AWS_PROFILE."
ok "AWS Account: $AWS_ACCOUNT (region: $REGION)"

# Verify stacks exist
for stack in Data McpServers Api AgentRuntime; do
  aws cloudformation describe-stacks \
    --stack-name "${STACK_PREFIX}-${stack}-${STAGE}" \
    --region "$REGION" &>/dev/null \
    || die "Stack ${STACK_PREFIX}-${stack}-${STAGE} not found. Deploy first."
done
ok "All required stacks are deployed"

# ─── Gather Stack Outputs ─────────────────────────────────────────────

log "Gathering stack outputs..."

API_URL=$(get_stack_output "Api" "RestApiUrl")
WS_URL=$(get_stack_output "Api" "WebSocketUrl")
USER_POOL_ID=$(get_stack_output "Api" "UserPoolId")
CLIENT_ID=$(get_stack_output "Api" "UserPoolClientId")
RUNTIME_ID=$(get_stack_output "AgentRuntime" "AgentRuntimeId")
BUCKET_NAME=$(get_stack_output "Data" "ResearchBucketName")

[[ -n "$API_URL" ]] || die "Could not retrieve RestApiUrl from Api stack"
[[ -n "$USER_POOL_ID" ]] || die "Could not retrieve UserPoolId from Api stack"
[[ -n "$CLIENT_ID" ]] || die "Could not retrieve UserPoolClientId from Api stack"
[[ -n "$RUNTIME_ID" ]] || die "Could not retrieve AgentRuntimeId from AgentRuntime stack"
[[ -n "$BUCKET_NAME" ]] || die "Could not retrieve ResearchBucketName from Data stack"

ok "API URL: $API_URL"
ok "Agent Runtime ID: $RUNTIME_ID"
ok "Research Bucket: $BUCKET_NAME"
debug "WebSocket: $WS_URL"
debug "User Pool: $USER_POOL_ID"
debug "Client ID: $CLIENT_ID"

# ─── Step 1: Wire Invoker Lambda ─────────────────────────────────────

if [[ "$SKIP_SETUP" == "false" ]]; then
  log "Step 1: Wiring invoker Lambda with AgentCore Runtime ARN..."

  # Get the full runtime ARN from stack outputs
  RUNTIME_ARN=$(get_stack_output "AgentRuntime" "AgentRuntimeArn")
  [[ -n "$RUNTIME_ARN" ]] || die "Could not retrieve AgentRuntimeArn from AgentRuntime stack"

  # Get current env vars to preserve any existing ones
  CURRENT_ENV=$(aws lambda get-function-configuration \
    --function-name "deep-research-invoker-${STAGE}" \
    --query 'Environment.Variables' \
    --output json --region "$REGION" 2>/dev/null || echo '{}')

  # Build updated env vars (merge, don't clobber)
  UPDATED_ENV=$(echo "$CURRENT_ENV" | python3 -c "
import json, sys
env = json.load(sys.stdin) or {}
env['AGENT_RUNTIME_ARN'] = '$RUNTIME_ARN'
env['TRACKING_TABLE'] = 'deep-research-tracking-${STAGE}'
env['RESEARCH_BUCKET'] = '$BUCKET_NAME'
env['STAGE'] = '${STAGE}'
print(json.dumps({'Variables': env}))
")

  aws lambda update-function-configuration \
    --function-name "deep-research-invoker-${STAGE}" \
    --environment "$UPDATED_ENV" \
    --region "$REGION" \
    --output text --query 'FunctionName' >/dev/null

  ok "Invoker wired: AGENT_RUNTIME_ARN=$RUNTIME_ARN"

  # Wait for function update to complete
  log "  Waiting for Lambda update to propagate..."
  aws lambda wait function-updated \
    --function-name "deep-research-invoker-${STAGE}" \
    --region "$REGION" 2>/dev/null || sleep 5

  # ─── Step 2: Enable USER_PASSWORD_AUTH on Cognito Client ───────────

  log "Step 2: Ensuring USER_PASSWORD_AUTH is enabled on Cognito client..."

  aws cognito-idp update-user-pool-client \
    --user-pool-id "$USER_POOL_ID" \
    --client-id "$CLIENT_ID" \
    --explicit-auth-flows ALLOW_USER_PASSWORD_AUTH ALLOW_USER_SRP_AUTH ALLOW_REFRESH_TOKEN_AUTH \
    --prevent-user-existence-errors ENABLED \
    --region "$REGION" \
    --output text --query 'UserPoolClient.ClientId' >/dev/null

  ok "USER_PASSWORD_AUTH enabled"

  # ─── Step 3: Create Cognito Test User ──────────────────────────────

  log "Step 3: Creating Cognito test user: $TEST_EMAIL"

  # Check if user already exists
  USER_EXISTS=$(aws cognito-idp admin-get-user \
    --user-pool-id "$USER_POOL_ID" \
    --username "$TEST_EMAIL" \
    --region "$REGION" 2>/dev/null && echo "yes" || echo "no")

  if [[ "$USER_EXISTS" == "no" ]]; then
    aws cognito-idp admin-create-user \
      --user-pool-id "$USER_POOL_ID" \
      --username "$TEST_EMAIL" \
      --temporary-password "Temp${TEST_PASSWORD}" \
      --user-attributes Name=email,Value="$TEST_EMAIL" Name=email_verified,Value=true \
      --message-action SUPPRESS \
      --region "$REGION" >/dev/null

    ok "User created: $TEST_EMAIL"
  else
    ok "User already exists: $TEST_EMAIL"
  fi

  # Set permanent password (bypass force-change-password state)
  aws cognito-idp admin-set-user-password \
    --user-pool-id "$USER_POOL_ID" \
    --username "$TEST_EMAIL" \
    --password "$TEST_PASSWORD" \
    --permanent \
    --region "$REGION"

  ok "Password set (permanent)"
else
  log "Skipping setup (--skip-setup). Using existing configuration."
fi

# ─── Step 4: Authenticate ────────────────────────────────────────────

log "Step 4: Authenticating as $TEST_EMAIL..."

AUTH_RESULT=$(aws cognito-idp initiate-auth \
  --client-id "$CLIENT_ID" \
  --auth-flow USER_PASSWORD_AUTH \
  --auth-parameters USERNAME="$TEST_EMAIL",PASSWORD="$TEST_PASSWORD" \
  --region "$REGION" \
  --output json 2>&1) || die "Authentication failed. Check email/password.\n  Response: $AUTH_RESULT"

TOKEN=$(echo "$AUTH_RESULT" | jq -r '.AuthenticationResult.IdToken // empty')
[[ -n "$TOKEN" ]] || die "Failed to extract ID token from auth response:\n$AUTH_RESULT"

# Show token expiry
TOKEN_EXP=$(echo "$TOKEN" | cut -d. -f2 | python3 -c "
import sys, json, base64, datetime
payload = sys.stdin.read().strip()
payload += '=' * (4 - len(payload) % 4)
data = json.loads(base64.b64decode(payload))
exp = datetime.datetime.fromtimestamp(data['exp'])
print(exp.strftime('%H:%M:%S'))
" 2>/dev/null || echo "unknown")

ok "Authenticated (token expires: $TOKEN_EXP)"

# ─── Step 5: Submit Research Request ─────────────────────────────────

log "Step 5: Submitting research request..."
log "  Query: \"$TEST_QUERY\""
log "  Depth: $TEST_DEPTH"

SUBMIT_RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "${API_URL}research" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "$(jq -n \
    --arg query "$TEST_QUERY" \
    --arg depth "$TEST_DEPTH" \
    '{query: $query, options: {depth: $depth, sources: ["aws-docs", "web", "github"]}}'
  )")

HTTP_CODE=$(echo "$SUBMIT_RESPONSE" | tail -1)
SUBMIT_BODY=$(echo "$SUBMIT_RESPONSE" | sed '$d')

if [[ "$HTTP_CODE" != "202" ]]; then
  err "Submit failed (HTTP $HTTP_CODE)"
  err "Response: $SUBMIT_BODY"
  die "Expected HTTP 202. Check invoker Lambda logs."
fi

SLUG=$(echo "$SUBMIT_BODY" | jq -r '.slug')
TASK_ID=$(echo "$SUBMIT_BODY" | jq -r '.taskId')

[[ -n "$SLUG" && "$SLUG" != "null" ]] || die "No slug in response: $SUBMIT_BODY"

ok "Research submitted!"
ok "  Task ID: $TASK_ID"
ok "  Slug:    $SLUG"

# ─── Step 6: Poll for Completion ─────────────────────────────────────

log "Step 6: Polling for completion (timeout: ${POLL_TIMEOUT}s, interval: ${POLL_INTERVAL}s)..."
echo ""

ELAPSED=0
LAST_STATUS=""

while [[ $ELAPSED -lt $POLL_TIMEOUT ]]; do
  STATUS_RESPONSE=$(curl -s "${API_URL}research/${SLUG}/status" \
    -H "Authorization: Bearer $TOKEN" 2>/dev/null || echo '{"status":"POLL_ERROR"}')

  CURRENT_STATUS=$(echo "$STATUS_RESPONSE" | jq -r '.status // "UNKNOWN"')

  # Print status change
  if [[ "$CURRENT_STATUS" != "$LAST_STATUS" ]]; then
    case $CURRENT_STATUS in
      PENDING)       echo -e "  ${YELLOW}⏳ PENDING${NC} — waiting for agent to start..." ;;
      IN_PROGRESS)   echo -e "  ${BLUE}🔄 IN_PROGRESS${NC} — agent is running..." ;;
      RESEARCHING)   echo -e "  ${BLUE}🔬 RESEARCHING${NC} — sub-agents gathering data..." ;;
      SYNTHESIZING)  echo -e "  ${BLUE}📝 SYNTHESIZING${NC} — writing final report..." ;;
      COMPLETE)      echo -e "  ${GREEN}✅ COMPLETE${NC}" ;;
      FAILED)        echo -e "  ${RED}❌ FAILED${NC}" ;;
      *)             echo -e "  ⚙️  $CURRENT_STATUS" ;;
    esac
    LAST_STATUS="$CURRENT_STATUS"
  else
    # Progress dot
    printf "."
  fi

  # Terminal states
  if [[ "$CURRENT_STATUS" == "COMPLETE" ]]; then
    echo ""
    break
  fi

  if [[ "$CURRENT_STATUS" == "FAILED" ]]; then
    echo ""
    ERROR_MSG=$(echo "$STATUS_RESPONSE" | jq -r '.error // "Unknown error"')
    err "Research failed: $ERROR_MSG"
    echo ""
    warn "Debugging tips:"
    echo "  aws logs tail /aws/lambda/deep-research-invoker-${STAGE} --since 10m --region $REGION"
    echo "  aws logs tail /aws/lambda/deep-research-brave-mcp-${STAGE} --since 10m --region $REGION"
    die "Research task failed."
  fi

  sleep "$POLL_INTERVAL"
  ELAPSED=$((ELAPSED + POLL_INTERVAL))
done

# Timeout check
if [[ $ELAPSED -ge $POLL_TIMEOUT ]]; then
  echo ""
  warn "Timed out after ${POLL_TIMEOUT}s. Last status: $CURRENT_STATUS"
  warn "The research may still be running. Check manually:"
  echo "  curl -s '${API_URL}research/${SLUG}/status' -H 'Authorization: Bearer $TOKEN' | jq"
  exit 2
fi

# ─── Step 7: Verify Results ──────────────────────────────────────────

echo ""
log "Step 7: Verifying results..."

# Get final status with cost
FINAL_STATUS=$(curl -s "${API_URL}research/${SLUG}/status" \
  -H "Authorization: Bearer $TOKEN")

debug "Full status response:"
debug "$(echo "$FINAL_STATUS" | jq .)"

# Check cost
TOTAL_TOKENS=$(echo "$FINAL_STATUS" | jq -r '.cost.totalTokens // 0')
COST_USD=$(echo "$FINAL_STATUS" | jq -r '.cost.estimatedCostUsd // 0')

if [[ "$TOTAL_TOKENS" != "0" ]]; then
  ok "Token usage: $TOTAL_TOKENS tokens (\$$COST_USD)"
fi

# List S3 artifacts
log "S3 artifacts:"
ARTIFACTS=$(aws s3 ls "s3://${BUCKET_NAME}/${SLUG}/" --recursive --region "$REGION" 2>/dev/null || echo "")

if [[ -n "$ARTIFACTS" ]]; then
  echo "$ARTIFACTS" | while read -r line; do
    echo -e "  ${GREEN}•${NC} $line"
  done
else
  warn "No S3 artifacts found (agent may write them async)"
fi

# Download and preview report
echo ""
log "Report preview:"
echo "─────────────────────────────────────────────────────────────"

REPORT=$(aws s3 cp "s3://${BUCKET_NAME}/${SLUG}/report.md" - --region "$REGION" 2>/dev/null || echo "")

if [[ -n "$REPORT" ]]; then
  echo "$REPORT" | head -50
  REPORT_LEN=${#REPORT}
  if [[ $REPORT_LEN -gt 3000 ]]; then
    echo ""
    echo "  ... [${REPORT_LEN} total bytes — showing first 50 lines]"
  fi
else
  warn "Report not found at s3://${BUCKET_NAME}/${SLUG}/report.md"
  warn "Check if agent wrote to a different key:"
  aws s3 ls "s3://${BUCKET_NAME}/${SLUG}/" --region "$REGION" 2>/dev/null || true
fi

echo "─────────────────────────────────────────────────────────────"

# ─── Summary ──────────────────────────────────────────────────────────

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo -e "${GREEN}  ✅ END-TO-END TEST PASSED${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "  Slug:     $SLUG"
echo "  Tokens:   $TOTAL_TOKENS"
echo "  Cost:     \$$COST_USD"
echo "  Report:   s3://${BUCKET_NAME}/${SLUG}/report.md"
echo "  Status:   ${API_URL}research/${SLUG}/status"
echo ""
echo "  To re-run (skip setup):"
echo "    $0 --skip-setup --query \"your new query\""
echo ""
echo "  To view full report:"
echo "    aws s3 cp s3://${BUCKET_NAME}/${SLUG}/report.md - | less"
echo ""
