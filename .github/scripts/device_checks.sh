#!/usr/bin/env bash
# Disposable GitHub-hosted emulator; test keys never sign distributed releases.
set -euo pipefail
api="${1:?Android API required}"
sdk_tools="$ANDROID_HOME/cmdline-tools/latest/bin"
adb="$ANDROID_HOME/platform-tools/adb"
avd="Fairy_Test_Compat_CI_${api}"
serial="emulator-5556"
image="system-images;android-${api};google_apis;x86_64"
if [ "$api" = 37 ]; then image="system-images;android-37.1;google_apis_ps16k;x86_64"; fi
output="artifacts/compatibility/api${api}"
mkdir -p "$output"

"$sdk_tools/sdkmanager" "$ANDROID_PLATFORM" "build-tools;$ANDROID_BUILD_TOOLS" emulator "$image"
echo no | "$sdk_tools/avdmanager" create avd --name "$avd" --package "$image" --force
if [ -e /dev/kvm ]; then sudo chmod a+rw /dev/kvm; fi
"$ANDROID_HOME/emulator/emulator" -avd "$avd" -port 5556 -no-window -no-audio \
  -no-boot-anim -no-snapshot -gpu swiftshader_indirect -memory 2048 \
  > "$output/emulator.log" 2>&1 &
emulator_pid=$!
cleanup() {
  "$adb" -s "$serial" emu kill >/dev/null 2>&1 || true
  kill "$emulator_pid" >/dev/null 2>&1 || true
}
trap cleanup EXIT
ready=false
for attempt in $(seq 1 120); do
  if [ "$("$adb" -s "$serial" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" = 1 ]; then
    ready=true
    break
  fi
  sleep 2
done
if [ "$ready" != true ]; then tail -80 "$output/emulator.log"; exit 1; fi
"$adb" -s "$serial" shell input keyevent KEYCODE_WAKEUP
"$adb" -s "$serial" shell wm dismiss-keyguard

./gradlew --no-daemon --stacktrace :app:assembleDebug :app:assembleDebugAndroidTest :app:assembleRelease
"$adb" -s "$serial" install app/build/outputs/apk/debug/app-debug.apk
"$adb" -s "$serial" install app/build/outputs/apk/androidTest/debug/app-debug-androidTest.apk
python3 tools/run_native_checks.py --adb "$adb" --serial "$serial" --output "$output/native.txt"

# Exercise R8/resource-shrunk output with an ephemeral key on the same clean AVD.
keystore="$RUNNER_TEMP/compat-test.p12"
keytool -genkeypair -noprompt -keystore "$keystore" -storetype PKCS12 \
  -storepass android -keypass android -alias androidtestkey -keyalg RSA -keysize 2048 \
  -validity 2 -dname "CN=Android Compatibility Test"
build_tools="$ANDROID_HOME/build-tools/$ANDROID_BUILD_TOOLS"
"$build_tools/zipalign" -f -P 16 4 app/build/outputs/apk/release/app-release-unsigned.apk "$output/aligned.apk"
"$build_tools/apksigner" sign --ks "$keystore" --ks-pass pass:android --key-pass pass:android \
  --v4-signing-enabled false --out "$output/release.apk" "$output/aligned.apk"
"$build_tools/apksigner" verify "$output/release.apk"
python3 tools/release_compatibility_test.py --adb "$adb" --serial "$serial" \
  --apk "$output/release.apk" --replace-test-app --output "$output/release"
