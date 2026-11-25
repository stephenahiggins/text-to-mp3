const assert = require('node:assert/strict');
const test = require('node:test');
const configure = require('../app.config');
const base = require('../app.json').expo;

test('signing belongs to the person building the app', () => {
  const saved = {...process.env};
  try {
    delete process.env.APPLE_TEAM_ID;
    delete process.env.APP_BUNDLE_IDENTIFIER;
    delete process.env.ANDROID_PACKAGE;
    assert.equal(configure({config: base}).ios.appleTeamId, undefined);
    process.env.APPLE_TEAM_ID = 'ABCDE12345';
    process.env.APP_BUNDLE_IDENTIFIER = 'org.example.reader';
    const config = configure({config: base});
    assert.equal(config.ios.appleTeamId, 'ABCDE12345');
    assert.equal(config.ios.bundleIdentifier, 'org.example.reader');
    assert.equal(config.android.package, 'org.example.reader');
    assert.equal(base.ios.appleTeamId, undefined);
    assert.notEqual(base.ios.bundleIdentifier, config.ios.bundleIdentifier);
  } finally {
    for (const key of ['APPLE_TEAM_ID', 'APP_BUNDLE_IDENTIFIER', 'ANDROID_PACKAGE']) {
      if (saved[key] === undefined) delete process.env[key]; else process.env[key] = saved[key];
    }
  }
});
