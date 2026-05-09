/**
 * Cognito authentication using amazon-cognito-identity-js.
 * Provides login, logout, token refresh, and session management.
 */
import {
  CognitoUserPool,
  CognitoUser,
  AuthenticationDetails,
  CognitoUserSession,
} from 'amazon-cognito-identity-js';
import { config } from './config';

const userPool = new CognitoUserPool({
  UserPoolId: config.userPoolId,
  ClientId: config.userPoolClientId,
});

export interface AuthState {
  isAuthenticated: boolean;
  token: string | null;
  email: string | null;
}

export function getCurrentSession(): Promise<CognitoUserSession | null> {
  return new Promise((resolve) => {
    const user = userPool.getCurrentUser();
    if (!user) return resolve(null);

    user.getSession((err: Error | null, session: CognitoUserSession | null) => {
      if (err || !session?.isValid()) return resolve(null);
      resolve(session);
    });
  });
}

export async function getToken(): Promise<string | null> {
  const session = await getCurrentSession();
  return session?.getIdToken().getJwtToken() || null;
}

export function login(email: string, password: string): Promise<string> {
  return new Promise((resolve, reject) => {
    const user = new CognitoUser({ Username: email, Pool: userPool });
    const authDetails = new AuthenticationDetails({ Username: email, Password: password });

    user.authenticateUser(authDetails, {
      onSuccess: (session) => resolve(session.getIdToken().getJwtToken()),
      onFailure: (err) => reject(err),
      newPasswordRequired: () => reject(new Error('NEW_PASSWORD_REQUIRED')),
    });
  });
}

export function logout(): void {
  const user = userPool.getCurrentUser();
  if (user) user.signOut();
}

export function getCurrentEmail(): string | null {
  const user = userPool.getCurrentUser();
  return user?.getUsername() || null;
}
