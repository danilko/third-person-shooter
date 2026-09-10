plugins {
    id("com.utopia-rise.godot-jvm") version "1.0.0-dev3"
}

repositories {
    mavenCentral()
}

kotlin {
    jvmToolchain(17)
}

// ── Headless unit tests for the engine-free networking logic (com.openworld.game.net) ──
// The Godot runtime can't instantiate native types (StreamPeerBuffer, CharacterBody3D),
// so only the pure prediction/interpolation/queue algorithms are tested here — the parts
// whose correctness (constant-speed playback, reconciliation convergence, in-order command
// consumption) the F6/F7 manual pass can't measure. See NETWORK_REWRITE_PLAN.md Phase 8.
dependencies {
    testImplementation(platform("org.junit:junit-bom:5.10.2"))
    testImplementation("org.junit.jupiter:junit-jupiter")
    testRuntimeOnly("org.junit.platform:junit-platform-launcher")
}

tasks.withType<Test>().configureEach {
    useJUnitPlatform()
}

godot {
    // ---------Setup-----------------

    // Where .gdj registration files for DEPENDENCY classes land. Since 0.17 a project's own
    // classes are attached by their .java source path and never get a .gdj, so this directory
    // stays empty for this project (it has no registered external dependencies) -- it is kept
    // pointed at the existing, gitignored `gdj/` so one appearing there is immediately visible.
    registrationFilesDirectory.set(projectDir.resolve("gdj"))

	// Leave dependency .gdj generation on; it costs nothing while there are no such dependencies.
	disableGdj.set(false)

    // defines whether the script registration files should be generated hierarchically according to the classes package path or flattened into `registrationFilesDirectory`
    // (renamed from isRegistrationFileHierarchyEnabled -> registrationFilesLayoutMode, an enum now)
    //registrationFilesLayoutMode.set(godot.entrygenerator.settings.RegistrationFileLayoutMode.FLAT)

    // defines whether your scripts should be registered with their fqName or their simple name (can help with resolving script name conflicts)
    // (renamed from isFqNameRegistrationEnabled -> registrationNameMode, an enum now)
    //registrationNameMode.set(godot.entrygenerator.settings.RegisteredNameMode.SIMPLE_NAME)

    // ---------Android----------------

    // NOTE: Make sure you read: https://godot-kotl.in/en/stable/user-guide/exporting/#android as not all jvm libraries are compatible with android!
    // IMPORTANT: Android export should to be considered from the start of development!
    //isAndroidExportEnabled.set(ANDROID_ENABLED)
    //d8ToolPath.set(File("D8_TOOL_PATH"))
    //androidCompileSdkDir.set(File("ANDROID_COMPILE_SDK_DIR"))

    // --------IOS and Graal------------

    // NOTE: this is an advanced feature! Read: https://godot-kotl.in/en/stable/user-guide/advanced/graal-vm-native-image/
    // IMPORTANT: Graal Native Image needs to be considered from the start of development!
    //isGraalNativeImageExportEnabled.set(IS_GRAAL_VM_ENABLED)
    //graalVmDirectory.set(File("GRAAL_VM_DIR"))
    //windowsDeveloperVCVarsPath.set(File("WINDOWS_DEVELOPER_VS_VARS_PATH"))
    //isIOSExportEnabled.set(IS_IOS_ENABLED)

	// --------Library authors------------

	// library setup. See: https://godot-kotl.in/en/stable/develop-libraries/
    //classPrefix.set("MyCustomClassPrefix")
    //projectName.set("LibraryProjectName")
    //projectName.set("LibraryProjectName")
}
