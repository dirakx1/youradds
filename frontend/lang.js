// Remember the visitor's language choice so un-prefixed URLs (/, /about/ ...) redirect to it next time.
document.addEventListener('click', e => {
  const a = e.target.closest('a[data-setlang]');
  if (a) document.cookie = `lang=${a.dataset.setlang};path=/;max-age=31536000;samesite=lax`;
});
