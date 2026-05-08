#!/usr/bin/env node
import 'source-map-support/register';
import * as cdk from 'aws-cdk-lib';
import { DataStack } from '../lib/data-stack';
import { McpServersStack } from '../lib/mcp-servers-stack';
import { ApiStack } from '../lib/api-stack';
import { AgentRuntimeStack } from '../lib/agent-runtime-stack';
import { FrontendStack } from '../lib/frontend-stack';
import { ObservabilityStack } from '../lib/observability-stack';
import { devConfig } from '../config';

const app = new cdk.App();

// Allow stage override via context: `cdk deploy -c stage=prod`
const stage = app.node.tryGetContext('stage') || 'dev';
const config = stage === 'prod' ? require('../config').prodConfig : devConfig;

// ─── Stack 1: Data (stateful — rarely changes, protected) ────────────
const data = new DataStack(app, `DeepResearch-Data-${config.stage}`, {
  env: config.env,
  config,
});

// ─── Stack 2: MCP Servers (stateless Lambdas — change frequently) ────
const mcpServers = new McpServersStack(app, `DeepResearch-McpServers-${config.stage}`, {
  env: config.env,
  config,
  researchBucket: data.researchBucket,
  trackingTable: data.trackingTable,
  secretArn: config.secretArn,
});

// ─── Stack 3: API (REST + WebSocket + Cognito) ───────────────────────
const api = new ApiStack(app, `DeepResearch-Api-${config.stage}`, {
  env: config.env,
  config,
  connectionsTable: data.connectionsTable,
  trackingTable: data.trackingTable,
  researchBucket: data.researchBucket,
});

// ─── Stack 4: AgentCore Runtime (the brain) ──────────────────────────
const agentRuntime = new AgentRuntimeStack(app, `DeepResearch-AgentRuntime-${config.stage}`, {
  env: config.env,
  config,
  researchBucket: data.researchBucket,
  trackingTable: data.trackingTable,
  connectionsTable: data.connectionsTable,
  mcpFunctions: mcpServers.functions,
  userPool: api.userPool,
  userPoolClient: api.userPoolClient,
  webSocketApi: api.webSocketApi,
  webSocketStage: api.webSocketStage,
  secretArn: config.secretArn,
});

// ─── Stack 5: Frontend (CloudFront + S3 SPA) ─────────────────────────
const frontend = new FrontendStack(app, `DeepResearch-Frontend-${config.stage}`, {
  env: config.env,
  config,
  researchBucketName: data.researchBucket.bucketName,
});

// ─── Stack 6: Observability (Dashboard + Alarms) ─────────────────────
const observability = new ObservabilityStack(app, `DeepResearch-Observability-${config.stage}`, {
  env: config.env,
  config,
  mcpFunctions: mcpServers.functions,
});

// Explicit dependency ordering (CDK handles cross-stack refs automatically,
// these are for deploy ordering of independent stacks)
mcpServers.addDependency(data);
api.addDependency(data);
agentRuntime.addDependency(mcpServers);
agentRuntime.addDependency(api);
observability.addDependency(mcpServers);

app.synth();
