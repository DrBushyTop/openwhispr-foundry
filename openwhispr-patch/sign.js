// Signs a packed OpenWhispr app with a local identity, with hardened runtime
// and OpenWhispr's own entitlements. Run from the OpenWhispr build directory
// so @electron/osx-sign resolves from its node_modules.
//
//   node sign.js <path/to/App.app> <identity name> <entitlements.plist>
const { signAsync } = require(require.resolve("@electron/osx-sign", { paths: [process.cwd()] }));

const [app, identity, entitlements] = process.argv.slice(2);
if (!app || !identity || !entitlements) {
  console.error("usage: node sign.js <App.app> <identity> <entitlements.plist>");
  process.exit(2);
}

signAsync({
  app,
  identity,
  // osx-sign looks identities up with `security find-identity -v` under the
  // default trust policy, where a self-signed cert trusted only for code
  // signing shows as CSSMERR_TP_NOT_TRUSTED and is skipped. codesign itself
  // checks with the code-signing policy, which the cert passes.
  identityValidation: false,
  platform: "darwin",
  preAutoEntitlements: false,
  optionsForFile: () => ({ hardenedRuntime: true, entitlements }),
})
  .then(() => console.log(`Signed ${app} with "${identity}"`))
  .catch((err) => {
    console.error("Signing failed:", err.message || err);
    process.exit(1);
  });
