const { withXcodeProject, withDangerousMod, withEntitlementsPlist } = require('expo/config-plugins');
const fs = require('fs');
const path = require('path');
const name = 'AddToStack';
function addTarget(project, bundleId, appleTeamId) {
  let entry = Object.entries(project.pbxNativeTargetSection()).find(([, t]) => t.name?.replaceAll('"', '') === name);
  let target;
  if (entry) target = { uuid: entry[0], pbxNativeTarget: entry[1] };
  else {
    target = project.addTarget(name, 'app_extension', name, `${bundleId}.share`);
    project.addBuildPhase([], 'PBXSourcesBuildPhase', 'Sources', target.uuid);
    project.addBuildPhase([], 'PBXFrameworksBuildPhase', 'Frameworks', target.uuid);
    project.addBuildPhase([], 'PBXResourcesBuildPhase', 'Resources', target.uuid);
  }
  const list = project.pbxXCConfigurationList()[target.pbxNativeTarget.buildConfigurationList];
  for (const ref of list.buildConfigurations) {
    Object.assign(project.pbxXCBuildConfigurationSection()[ref.value].buildSettings, {
      INFOPLIST_FILE: `${name}/Info.plist`, CODE_SIGN_ENTITLEMENTS: `${name}/AddToStack.entitlements`,
      SWIFT_VERSION: '5.0', IPHONEOS_DEPLOYMENT_TARGET: '16.0', TARGETED_DEVICE_FAMILY: '"1,2"',
      APPLICATION_EXTENSION_API_ONLY: 'YES', GENERATE_INFOPLIST_FILE: 'NO',
      ...(appleTeamId ? { DEVELOPMENT_TEAM: appleTeamId } : {}),
      CURRENT_PROJECT_VERSION: '1', MARKETING_VERSION: '1.0.0', CODE_SIGN_STYLE: 'Automatic',
    });
  }
  const main = project.getFirstTarget().uuid;
  const attributes = project.getFirstProject().firstProject.attributes;
  attributes.TargetAttributes ||= {};
  for (const id of [main, target.uuid]) {
    const settings = attributes.TargetAttributes[id] ||= {};
    settings.ProvisioningStyle = 'Automatic';
    if (appleTeamId) settings.DevelopmentTeam = appleTeamId;
    settings.SystemCapabilities ||= {};
    settings.SystemCapabilities['com.apple.ApplicationGroups'] = { enabled: 1 };
  }
  const group = project.getFirstProject().firstProject.mainGroup;
  for (const [file, id] of [[`${name}/ShareViewController.swift`, target.uuid], [`${name}/ShareQueue.swift`, target.uuid],
    ['ReadingShareQueue/ShareQueue.swift', main], ['ReadingShareQueue/ReadingShareQueue.swift', main], ['ReadingShareQueue/ReadingShareQueueBridge.m', main]]) {
    if (!project.hasFile(file)) project.addSourceFile(file, { target: id }, group);
  }
}
module.exports = config => {
  const group = `group.${config.ios.bundleIdentifier}.sharing`;
  config = withEntitlementsPlist(config, config => {
    config.modResults['com.apple.security.application-groups'] = [...new Set([...(config.modResults['com.apple.security.application-groups'] || []), group])];
    return config;
  });
  config = withDangerousMod(config, ['ios', async config => {
    const root = config.modRequest.projectRoot, native = config.modRequest.platformProjectRoot;
    const dest = path.join(native, name), main = path.join(native, 'ReadingShareQueue');
    fs.mkdirSync(dest, { recursive: true }); fs.mkdirSync(main, { recursive: true });
    const copy = (from, to) => fs.writeFileSync(to, fs.readFileSync(from, 'utf8').replaceAll('group.com.articlestacks.mobile.sharing', group));
    for (const file of ['ShareViewController.swift', 'ShareQueue.swift', 'Info.plist', 'AddToStack.entitlements']) copy(path.join(root, 'share-extension', file), path.join(dest, file));
    copy(path.join(root, 'share-extension/ShareQueue.swift'), path.join(main, 'ShareQueue.swift'));
    for (const file of ['ReadingShareQueue.swift', 'ReadingShareQueueBridge.m']) copy(path.join(root, 'native', file), path.join(main, file));
    return config;
  }]);
  return withXcodeProject(config, config => {
    addTarget(config.modResults, config.ios.bundleIdentifier, config.ios.appleTeamId);
    return config;
  });
};
module.exports.addTarget = addTarget;
