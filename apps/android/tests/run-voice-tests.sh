#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
VOICE_TEST_DIR=$(mktemp -d)
trap 'rm -rf "$VOICE_TEST_DIR"' EXIT HUP INT TERM
JAVAC=${JAVA_HOME:+$JAVA_HOME/bin/}javac
JAVA=${JAVA_HOME:+$JAVA_HOME/bin/}java
"$JAVAC" --release 17 -d "$VOICE_TEST_DIR" app/src/main/java/ai/chintu/core/VoiceCommand.java app/src/main/java/ai/chintu/core/DestinationMatcher.java tests/VoiceCommandTest.java
"$JAVA" -cp "$VOICE_TEST_DIR" VoiceCommandTest
