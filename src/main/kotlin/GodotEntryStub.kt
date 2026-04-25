// This file exists solely to trigger Kotlin compilation so the godot-jvm
// plugin's generated Entry class (build/generated/classgraph/...) gets
// compiled into main.jar and registered with the ServiceLoader.
// Without at least one .kt source file, compileKotlin is NO-SOURCE and
// the Entry class never appears in the JAR, breaking all script registrations.
