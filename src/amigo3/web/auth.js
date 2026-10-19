/* Optional GitHub identity shared by the browser pages. No credentials in JS storage. */
window.amigoAuth = (async () => {
  const response = await fetch('/api/me', {cache: 'no-store'});
  if (!response.ok) throw new Error('Unable to check sign-in status');
  const auth = await response.json();
  if (!auth.login_enabled) return auth;
  const returnTo = location.pathname + location.search + location.hash;
  const loginUrl = '/auth/github?return_to=' + encodeURIComponent(returnTo);
  const nav = document.querySelector('.site-nav');
  if (nav) {
    const account = document.createElement('span'); account.className = 'account-controls';
    if (auth.user) {
      const name = document.createElement('span'); name.textContent = '@' + auth.user.login;
      const logout = document.createElement('button'); logout.type = 'button'; logout.textContent = 'Sign out';
      logout.onclick = async () => {
        logout.disabled = true;
        try {
          const result = await fetch('/auth/logout', {method: 'POST'});
          if (!result.ok) throw new Error('Sign out failed');
          // Remove this login's local drafts as well as its in-memory server sessions.
          const prefix = 'amigo3-chat-connection-' + auth.session_key;
          try {
            for (const key of Object.keys(sessionStorage)) if (key.startsWith(prefix)) sessionStorage.removeItem(key);
          } catch { /* Storage is optional. */ }
          location.reload();
        } catch (error) { logout.disabled = false; logout.textContent = 'Retry sign out'; logout.title = error.message; }
      };
      account.append(name, logout);
    } else {
      const login = document.createElement('a'); login.href = loginUrl; login.textContent = 'Sign in with GitHub';
      account.append(login);
    }
    nav.append(account);
  }
  const prompt = document.querySelector('#github-login');
  if (prompt && !auth.user) { prompt.hidden = false; prompt.querySelector('a').href = loginUrl; }
  if (new URLSearchParams(location.search).get('login_error') === 'cancelled') {
    const notice = document.createElement('p'); notice.className = 'login-notice'; notice.setAttribute('role', 'status');
    notice.textContent = 'GitHub sign-in was cancelled. You can sign in again whenever you’re ready.';
    document.querySelector('main')?.prepend(notice);
  }
  return auth;
})();
// Ordinary browsing continues if identity lookup fails. Chat separately fails closed.
window.amigoAuth.catch(() => {});
window.addEventListener('pageshow', event => { if (event.persisted) location.reload(); });
