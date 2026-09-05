import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const source = readFileSync(new URL('../../assets/ui.js', import.meta.url), 'utf8');
const start = source.indexOf('function getForm() {');
const end = source.indexOf('\nfunction applyConfig(', start);
assert.ok(start >= 0 && end > start, 'exercise the production form serializer');
const formSource = source.slice(start, end);

function serializePower(value) {
  const run = new Function(
    'getValueIf', 'getCheckedIf', 'currentQosPreset', 'lastCfg',
    'getPassphraseValue', 'passphraseDirty', 'filterConfigForMode',
    `${formSource}; return JSON.stringify(getForm());`,
  );
  return JSON.parse(run(
    (id) => id === 'tx_power' ? value : undefined,
    () => undefined, 'off', {}, () => '', false, (payload) => payload,
  ));
}

for (const [input, expected] of [
  ['', null], ['  ', null], ['0', 0], ['20', 20], ['30', 30],
  ['20.5', 20.5], ['-1', -1], ['31', 31],
  ['Infinity', 'Infinity'], ['NaN', 'NaN'], ['20oops', '20oops'],
]) {
  test(`TX power form preserves ${JSON.stringify(input)} without truncation or implicit Auto`, () => {
    assert.equal(serializePower(input).tx_power, expected);
  });
}

test('an absent TX power control does not overwrite the saved preference', () => {
  assert.equal(Object.hasOwn(serializePower(undefined), 'tx_power'), false);
});
