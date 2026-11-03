import test from 'node:test';
import assert from 'node:assert/strict';

async function load(pathname, tag) {
  globalThis.location = { pathname, search: '', hash: '' };
  return import(`../i18n.js?test=${tag}`);
}

test('language and app path come from the URL prefix', async () => {
  const es = await load('/es/sign-in', 'es-path');
  assert.equal(es.lang, 'es');
  assert.equal(es.appPath, '/sign-in');
  assert.equal(es.L('/sign-up'), '/es/sign-up');
  const en = await load('/en/', 'en-path');
  assert.equal(en.lang, 'en');
  assert.equal(en.appPath, '/');
  assert.equal(en.L('/'), '/en/');
});

test('English pages are never translated', async () => {
  const { t, translateText } = await load('/en/', 'en-noop');
  assert.equal(t('Dashboard'), 'Dashboard');
  assert.equal(translateText('Campaigns'), 'Campaigns');
  assert.equal(t('Imported {n} new campaign(s).'.replace('Imported', 'Connection works. Imported'), { n: 3 }), 'Connection works. Imported 3 new campaign(s).');
});

test('Spanish: exact strings, whitespace, placeholders, patterns and fallbacks', async () => {
  const { t, translateText } = await load('/es/', 'es-text');
  assert.equal(t('Dashboard'), 'Panel');
  assert.equal(translateText('  Campaigns \n'), '  Campañas \n');
  assert.equal(translateText('Pick at least\n      one platform'), 'Elige al menos una plataforma');   // template whitespace is collapsed
  assert.equal(translateText('A phrase with no entry'), 'A phrase with no entry');   // unknown text is left alone
  assert.equal(t('Connection works. Imported {n} new campaign(s).', { n: 3 }), 'La conexión funciona. Se importaron 3 campaña(s) nueva(s).');
  assert.equal(t('Scene cost: ${c}', { c: 0.04 }), 'Costo de escena: $0.04');
  assert.equal(translateText('CTR 0.50% is low; refresh headline/creative in AI Studio.'), 'El CTR de 0.50 % es bajo; renueva el titular o la creatividad en AI Studio.');
  assert.equal(translateText('Higgsfield error 503'), 'Error de Higgsfield 503');
  assert.equal(t('Something never translated'), 'Something never translated');
  assert.equal(t('Hi {name}', { name: 'Ada' }), 'Hi Ada');
});

test('every Spanish entry keeps the same {placeholders} as its English key', async () => {
  const src = (await import('node:fs')).readFileSync(new URL('../i18n.js', import.meta.url), 'utf8');
  const block = src.slice(src.indexOf('const ES = {'), src.indexOf('// Messages built on the server'));
  const names = s => [...s.matchAll(/\{(\w+)\}/g)].map(m => m[1]).sort().join(',');
  const pairs = [...block.matchAll(/'((?:[^'\\]|\\.)*)':\s*'((?:[^'\\]|\\.)*)'/g)];
  assert.ok(pairs.length > 250, `found ${pairs.length} entries`);
  for (const [, key, value] of pairs) assert.equal(names(value), names(key), `placeholder mismatch for: ${key}`);
});
