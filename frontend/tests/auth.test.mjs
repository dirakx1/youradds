import test from 'node:test';
import assert from 'node:assert/strict';

function installClerk(client, onActive = () => {}) {
  let scripts = 0, scriptElement;
  class ClerkMock {
    constructor(key) { assert.equal(key, 'pk_test_ZXhhbXBsZS5jb20k'); this.client = client; this.user = client.user || {}; }
    async load() {}
    async setActive({ session }) { onActive(session); this.session = client.session || null; }
    async signOut() { this.session = null; if (client.signOut) await client.signOut(); }
  }
  globalThis.window = {};
  globalThis.document = {
    head: { append(node) { scripts++; assert.equal(node.attributes['data-clerk-publishable-key'], 'pk_test_ZXhhbXBsZS5jb20k'); window.Clerk = ClerkMock; queueMicrotask(() => node.onload()); } },
    createElement() {
      scriptElement = globalThis.script = { attributes: {}, setAttribute(key, value) { this.attributes[key] = value; },
        set async(_) {}, set crossOrigin(_) {}, set src(value) { this.source = value; }, get src() { return this.source; }, onload: null, onerror: null };
      return scriptElement;
    },
  };
  globalThis.fetch = async () => ({ ok: true, json: async () => ({ publishableKey: 'pk_test_ZXhhbXBsZS5jb20k', frontendApi: 'https://example.com' }) });
  const result = () => scripts;
  result.attribute = key => scriptElement?.attributes[key];
  result.source = () => scriptElement?.src;
  return result;
}

test('public legacy sign-in API activates only completed sessions and returns a fresh token', async () => {
  let active = null, tokenCalls = 0, params = null;
  const attempt = { status: 'complete', createdSessionId: 'sess_1', attemptFirstFactor: async p => { params = p; return attempt; } };
  const client = { signIn: { create: async p => { assert.deepEqual(p, { identifier: 'a@example.test' }); return attempt; } },
    session: { getToken: async () => { tokenCalls++; return 'signed-jwt'; } } };
  const scripts = installClerk(client, id => { active = id; });
  const { auth } = await import('../auth.js?test=legacy-signin');
  await Promise.all([auth.init(), auth.init()]);
  assert.deepEqual(await auth.signIn('a@example.test', 'correct-password'), { complete: true });
  assert.deepEqual(params, { strategy: 'password', password: 'correct-password' });
  assert.equal(await auth.token(), 'signed-jwt');
  assert.equal(scripts(), 1);
  assert.equal(scripts.attribute('data-clerk-publishable-key'), 'pk_test_ZXhhbXBsZS5jb20k');
  assert.equal(scripts.source(), 'https://example.com/npm/@clerk/clerk-js@6.40.1/dist/clerk.browser.js');
  assert.equal(active, 'sess_1');
  assert.equal(tokenCalls, 1);
});

test('custom sign-up sends email code and activates only after verification completes', async () => {
  let active = null, verifyParams = null, created = null;
  const signup = { status: 'missing_requirements', createdSessionId: null,
    prepareEmailAddressVerification: async p => { assert.deepEqual(p, { strategy: 'email_code' }); return signup; },
    attemptEmailAddressVerification: async p => { verifyParams = p; return { status: 'complete', createdSessionId: 'sess_signup' }; } };
  const client = { signUp: { create: async p => { created = p; return signup; },
    attemptEmailAddressVerification: async p => { verifyParams = p; return { status: 'complete', createdSessionId: 'sess_signup' }; } } };
  installClerk(client, id => { active = id; });
  const { auth } = await import('../auth.js?test=legacy-signup');
  assert.deepEqual(await auth.signUp('new@example.test', 'correct-password', 'Ada Lovelace'), { needsVerification: true });
  assert.deepEqual(created, { emailAddress: 'new@example.test', password: 'correct-password', firstName: 'Ada', lastName: 'Lovelace' });
  assert.equal(active, null);
  await auth.verifySignup('123456');
  assert.deepEqual(verifyParams, { code: '123456' });
  assert.equal(active, 'sess_signup');
});

test('device trust sends and verifies the documented email second factor before activation', async () => {
  let active = null, sends = 0, verified = null;
  const attempt = { status: 'needs_client_trust', createdSessionId: null,
    supportedSecondFactors: [{ strategy: 'email_code', emailAddressId: 'email_1' }],
    prepareSecondFactor: async p => { assert.deepEqual(p, { strategy: 'email_code', emailAddressId: 'email_1' }); sends++; return attempt; },
    attemptSecondFactor: async p => { verified = p; return { status: 'complete', createdSessionId: 'sess_trusted' }; } };
  const client = { signIn: { create: async () => ({ attemptFirstFactor: async () => attempt }) } };
  installClerk(client, id => { active = id; });
  const { auth } = await import('../auth.js?test=device-trust');
  assert.deepEqual(await auth.signIn('a@example.test', 'password'), { needsDeviceTrust: true });
  await auth.resendDeviceTrustCode();
  await auth.verifyDeviceTrust('654321');
  assert.equal(sends, 2);
  assert.deepEqual(verified, { strategy: 'email_code', code: '654321' });
  assert.equal(active, 'sess_trusted');
});

test('password recovery uses Clerk reset-code strategy and activates its completed session', async () => {
  let active = null, createParams = null, attemptParams = null;
  const flow = { identifier: null, status: null,
    create: async p => { createParams = p; flow.identifier = p.identifier; flow.status = 'needs_first_factor'; return flow; },
    attemptFirstFactor: async p => { attemptParams = p; return { status: 'complete', createdSessionId: 'sess_reset' }; } };
  const client = { signIn: flow };
  installClerk(client, id => { active = id; });
  const { auth } = await import('../auth.js?test=password-reset');
  assert.deepEqual(await auth.resetPassword('a@example.test', '', ''), { needsCode: true });
  assert.deepEqual(createParams, { strategy: 'reset_password_email_code', identifier: 'a@example.test' });
  assert.deepEqual(await auth.resetPassword('a@example.test', '123456', 'new-password'), { complete: true });
  assert.deepEqual(attemptParams, { strategy: 'reset_password_email_code', code: '123456', password: 'new-password' });
  assert.equal(active, 'sess_reset');
});

test('account password update and logout use Clerk-owned user/session operations', async () => {
  let updated = null, signedOut = 0;
  const client = { user: { updatePassword: async p => { updated = p; return {}; } }, signOut: async () => { signedOut++; } };
  installClerk(client);
  const { auth } = await import('../auth.js?test=account');
  await auth.updatePassword('old-password', 'new-password');
  await auth.signOut();
  assert.deepEqual(updated, { currentPassword: 'old-password', newPassword: 'new-password' });
  assert.equal(signedOut, 1);
});

test('Google OAuth enables Clerk sign-up transfer and calls the public redirect callback handler', async () => {
  let redirectParams = null, callbackParams = null, callbackReached = null, callbackNavigationOverride;
  const client = { signIn: { authenticateWithRedirect: async p => { redirectParams = p; } } };
  const scripts = installClerk(client);
  const { auth } = await import('../auth.js?test=oauth-callback');
  const clerk = await auth.init();
  clerk.handleRedirectCallback = async (params, navigate) => {
    callbackParams = params;
    callbackNavigationOverride = navigate;
    // The pinned SDK handles sign-in/signup continuation and activates the chosen session.
    callbackReached = true;
    clerk.session = { getToken: async () => 'google-session' };
  };
  await auth.google();
  assert.deepEqual(redirectParams, {
    strategy: 'oauth_google', redirectUrl: '/sso-callback', redirectUrlComplete: '/', continueSignUp: true,
  });
  await auth.completeGoogle();
  assert.equal(callbackReached, true);
  assert.equal(callbackNavigationOverride, undefined);
  assert.equal(await auth.token(), 'google-session');
  assert.deepEqual(callbackParams, {
    signInUrl: '/sign-in', signUpUrl: '/sign-up',
    signInFallbackRedirectUrl: '/', signUpFallbackRedirectUrl: '/',
    signInForceRedirectUrl: '/', signUpForceRedirectUrl: '/',
  });
  assert.equal(scripts(), 1);
});
