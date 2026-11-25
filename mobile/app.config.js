// Expo loads mobile/.env.local before evaluating this file. No account IDs or
// signing credentials need to be committed when another developer builds it.
module.exports = ({ config }) => {
  const bundleIdentifier = process.env.APP_BUNDLE_IDENTIFIER || config.ios.bundleIdentifier;
  const appleTeamId = process.env.APPLE_TEAM_ID;
  return {
    ...config,
    ios: {...config.ios, bundleIdentifier, ...(appleTeamId ? {appleTeamId} : {})},
    android: {...config.android, package: process.env.ANDROID_PACKAGE || bundleIdentifier},
  };
};
