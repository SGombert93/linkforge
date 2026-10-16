const logout = document.getElementById('logout');
logout?.addEventListener('click', async () => {
  const response = await fetch('/auth/logout', {method:'POST', headers:{'X-CSRF-Token':logout.dataset.csrf}});
  if (response.ok) location.href='/admin'; else alert('Session expired. Reload this page.');
});
