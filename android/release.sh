#!/bin/zsh
# Builds a signed app bundle (.aab) for Google Play from the committed code.
#
#   android/release.sh          build here
#   android/release.sh m1       build on another Mac over SSH and copy the bundle back
#
# The versionCode is the commit count, so every release from a newer commit is
# higher, as Play requires. The bundle lands in android/build/release/.
#
# Signing uses an upload key that never goes in the repo. On the machine that
# builds, ~/.whispr-mic/upload.properties (or $WHISPR_MIC_SIGNING) holds
#
#   storeFile=upload.jks          relative to the properties file
#   storePassword=...
#   keyAlias=upload
#   keyPassword=...
#
# Make the key once with
#   keytool -genkeypair -keystore ~/.whispr-mic/upload.jks -storetype PKCS12 \
#     -alias upload -keyalg RSA -keysize 4096 -validity 10000 -dname "CN=Whispr Mic"
# and keep a copy of both files somewhere safe, like a password manager.
set -euo pipefail

host=${1:-}
# Where the android directory goes on the build host, relative to its home directory.
remote_dir=${WHISPR_MIC_BUILD_DIR:-Code/Omat/openwhispr-foundry/android}
android=${0:A:h}
cd $android

if [[ -n $(git status --porcelain -- .) ]]; then
  print -u2 "android/ has uncommitted changes; commit them first so the release matches a commit."
  exit 1
fi
version_code=$(git rev-list --count HEAD)
out=$android/build/release/whispr-mic-$version_code.aab
mkdir -p ${out:h}

build="./gradlew -q testDebugUnitTest bundleRelease -PversionCode=$version_code"
check='[[ -f ${WHISPR_MIC_SIGNING:-$HOME/.whispr-mic/upload.properties} ]] ||
  { print -u2 "No upload key: see the top of release.sh"; exit 1; }'
bundle=app/build/outputs/bundle/release/app-release.aab

if [[ -n $host ]]; then
  rsync -a --delete --exclude build --exclude .gradle --exclude local.properties ./ $host:$remote_dir/
  ssh $host "zsh -lc '$check; cd $remote_dir && rm -f $bundle && $build'"
  scp -q $host:$remote_dir/$bundle $out
else
  eval $check
  rm -f $bundle
  eval $build
  cp $bundle $out
fi
print "Built versionCode $version_code: $out"
