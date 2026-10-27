#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
TEST_DIR=$(mktemp -d)
trap 'rm -rf "$TEST_DIR"' EXIT HUP INT TERM
JAVAC=${JAVA_HOME:+$JAVA_HOME/bin/}javac
JAVA=${JAVA_HOME:+$JAVA_HOME/bin/}java
"$JAVAC" -d "$TEST_DIR" app/src/main/java/ai/chintu/core/*.java tests/CoreTest.java
"$JAVA" -cp "$TEST_DIR" CoreTest
"$JAVAC" -d "$TEST_DIR" app/src/main/java/ai/chintu/core/ChatTranscript.java tests/ChatTranscriptTest.java
"$JAVA" -cp "$TEST_DIR" ChatTranscriptTest
