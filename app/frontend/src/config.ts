/**
 * Runtime configuration — injected post-deploy or from environment.
 * In production, these come from CloudFormation stack outputs.
 */
export const config = {
  // Cognito
  userPoolId: import.meta.env.VITE_USER_POOL_ID || '',
  userPoolClientId: import.meta.env.VITE_USER_POOL_CLIENT_ID || '',
  // API
  apiUrl: import.meta.env.VITE_API_URL || '',
  wsUrl: import.meta.env.VITE_WS_URL || '',
};
