import { L, appPath } from './i18n.js?v=brand-1';

const SDK_VERSION = '6.40.1';
let initPromise;
let clerk;
let pendingSignIn;
let pendingSignUp;
let signingOut = false;

function frontendHost(publishableKey) {
  if (!/^(pk_test|pk_live)_/.test(publishableKey)) throw new Error('Clerk is not configured.');
  const encoded = publishableKey.split('_')[2];
  const decoded = atob(encoded.replace(/-/g, '+').replace(/_/g, '/')).replace(/\$$/, '');
  if (!decoded || /[/:]/.test(decoded)) throw new Error('Clerk configuration is invalid.');
  return `https://${decoded}`;
}

async function init() {
  if (initPromise) return initPromise;
  initPromise = (async () => {
    const response = await fetch('/api/auth/config');
    if (!response.ok) throw new Error(response.status === 503 ? 'Authentication is not configured yet.' : 'Could not load authentication settings.');
    const config = await response.json();
    const host = frontendHost(config.publishableKey);
    if (config.frontendApi && config.frontendApi !== host) throw new Error('Clerk configuration does not match this application.');
    if (!window.Clerk) {
      await new Promise((resolve, reject) => {
        const script = document.createElement('script');
        script.src = `${host}/npm/@clerk/clerk-js@${SDK_VERSION}/dist/clerk.browser.js`;
        script.setAttribute('data-clerk-publishable-key', config.publishableKey);
        script.crossOrigin = 'anonymous';
        script.async = true;
        script.onload = resolve;
        script.onerror = () => reject(new Error('Could not load the authentication service.'));
        document.head.append(script);
      });
    }
    clerk = typeof window.Clerk === 'function' ? new window.Clerk(config.publishableKey) : window.Clerk;
    await clerk.load({ signInUrl: L('/sign-in'), signUpUrl: L('/sign-up'), afterSignOutUrl: L('/sign-in') });
    let hadSession = Boolean(clerk.session);
    clerk.addListener?.(state => {
      const hasSession = Boolean(state.session);
      if (hadSession && !hasSession) {
        window.__youradsMe = null;
        window.dispatchEvent(new Event('yourads:signed-out'));
        if (!signingOut && !['/sign-in', '/sign-up', '/forgot-password'].includes(appPath)) location.replace(L('/sign-in'));
      }
      hadSession = hasSession;
    });
    return clerk;
  })().catch(error => { initPromise = null; throw error; });
  return initPromise;
}

const publicClient = () => init();
const requireComplete = async (c, attempt) => {
  if (attempt.status !== 'complete' || !attempt.createdSessionId)
    throw new Error('Authentication is waiting for another required step.');
  await c.setActive({ session: attempt.createdSessionId });
};

async function token() {
  const client = await init();
  return client.session ? client.session.getToken() : null;
}

async function signIn(email, password) {
  const c = await init();
  const flow = await c.client.signIn.create({ identifier: email });
  const attempt = await flow.attemptFirstFactor({ strategy: 'password', password });
  if (attempt.status === 'needs_client_trust' || attempt.status === 'needs_second_factor') {
    const factor = attempt.supportedSecondFactors?.find(item => item.strategy === 'email_code');
    if (!factor) throw new Error('This device requires a verification method that is not enabled for this app.');
    pendingSignIn = attempt;
    await pendingSignIn.prepareSecondFactor({ strategy: 'email_code', emailAddressId: factor.emailAddressId });
    return { needsDeviceTrust: true };
  }
  if (attempt.status !== 'complete') throw new Error('Sign-in needs another step. Contact your administrator.');
  await requireComplete(c, attempt);
  return { complete: true };
}

async function verifyDeviceTrust(code) {
  const c = await init();
  if (!pendingSignIn) throw new Error('Your sign-in verification expired. Start again.');
  const attempt = await pendingSignIn.attemptSecondFactor({ strategy: 'email_code', code });
  pendingSignIn = null;
  await requireComplete(c, attempt);
}

async function resendDeviceTrustCode() {
  await init();
  if (!pendingSignIn) throw new Error('Your sign-in verification expired. Start again.');
  const factor = pendingSignIn.supportedSecondFactors?.find(item => item.strategy === 'email_code');
  if (!factor) throw new Error('The verification code cannot be resent. Restart sign-in.');
  await pendingSignIn.prepareSecondFactor({ strategy: 'email_code', emailAddressId: factor.emailAddressId });
}

async function signUp(email, password, name) {
  const c = await init();
  const [firstName, ...last] = (name || '').trim().split(/\s+/);
  const params = { emailAddress: email, password };
  if (firstName) params.firstName = firstName;
  if (last.length) params.lastName = last.join(' ');
  const flow = await c.client.signUp.create(params);
  if (flow.status === 'complete') { await requireComplete(c, flow); return { complete: true }; }
  pendingSignUp = flow;
  await pendingSignUp.prepareEmailAddressVerification({ strategy: 'email_code' });
  return { needsVerification: true };
}

async function verifySignup(code) {
  const c = await init();
  if (!pendingSignUp) throw new Error('Your sign-up verification expired. Start again.');
  const flow = await pendingSignUp.attemptEmailAddressVerification({ code });
  pendingSignUp = null;
  await requireComplete(c, flow);
}

async function resendSignupCode() {
  await init();
  if (!pendingSignUp) throw new Error('Your sign-up verification expired. Start again.');
  await pendingSignUp.prepareEmailAddressVerification({ strategy: 'email_code' });
}

async function resetPassword(email, code, password) {
  const c = await init();
  let flow = c.client.signIn;
  if (flow.identifier !== email || flow.status !== 'needs_first_factor')
    flow = await flow.create({ strategy: 'reset_password_email_code', identifier: email });
  if (!code) {
    return { needsCode: true };
  }
  if (!password) return { needsPassword: true };
  flow = await flow.attemptFirstFactor({ strategy: 'reset_password_email_code', code, password });
  await requireComplete(c, flow);
  return { complete: true };
}

async function google({ continueSignUp = true } = {}) {
  const c = await init();
  await c.client.signIn.authenticateWithRedirect({
    strategy: 'oauth_google', redirectUrl: L('/sso-callback'), redirectUrlComplete: L('/'),
    continueSignUp,
  });
}

async function completeGoogle() {
  const c = await init();
  return c.handleRedirectCallback({
    signInUrl: L('/sign-in'), signUpUrl: L('/sign-up'),
    signInFallbackRedirectUrl: L('/'), signUpFallbackRedirectUrl: L('/'),
    signInForceRedirectUrl: L('/'), signUpForceRedirectUrl: L('/'),
  });
}

async function updatePassword(currentPassword, newPassword) {
  const c = await init();
  const attempt = await c.user.updatePassword({ currentPassword, newPassword });
  if (attempt?.status && attempt.status !== 'complete') throw new Error('Please verify your identity in Clerk and try again.');
  return attempt;
}

async function updateProfile(name) {
  const c = await init();
  const [firstName, ...last] = name.trim().split(/\s+/);
  return c.user.update({ firstName: firstName || '', lastName: last.join(' ') });
}

async function setProfileImage(file) {
  const c = await init();
  return c.user.setProfileImage({ file });
}

async function signOut() {
  const c = await init();
  signingOut = true;
  try { await c.signOut(); } finally { signingOut = false; }
}

export const auth = { init, token, signIn, signUp, verifySignup, resendSignupCode, resetPassword,
  google, completeGoogle, updatePassword, updateProfile, setProfileImage, signOut, verifyDeviceTrust, resendDeviceTrustCode, get clerk() { return clerk; } };
export { publicClient };
