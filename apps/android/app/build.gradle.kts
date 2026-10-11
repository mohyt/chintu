import java.util.Properties

plugins { id("com.android.application"); id("org.jetbrains.kotlin.android") }

// Single source of truth for the app version: the VERSION file at the repository root.
val chintuVersion: String = rootProject.file("../../VERSION").readText().trim()

// Release signing material comes from CHINTU_* environment variables (CI) or, when those are
// absent, from apps/android/keystore.properties (git-ignored; local developer copy). When neither
// is present the release build type still configures so debug tasks keep working; only the
// release signing step fails, with an explanation.
data class ReleaseSigning(val storeFile: File, val storePassword: String, val keyAlias: String, val keyPassword: String)

val releaseSigning: ReleaseSigning? = run {
    val env = listOf("CHINTU_KEYSTORE", "CHINTU_KEYSTORE_PASSWORD", "CHINTU_KEY_ALIAS", "CHINTU_KEY_PASSWORD")
        .associateWith { System.getenv(it)?.takeIf { value -> value.isNotBlank() } }
    if (env.values.all { it != null }) {
        return@run ReleaseSigning(
            storeFile = rootProject.file(env.getValue("CHINTU_KEYSTORE")!!),
            storePassword = env.getValue("CHINTU_KEYSTORE_PASSWORD")!!,
            keyAlias = env.getValue("CHINTU_KEY_ALIAS")!!,
            keyPassword = env.getValue("CHINTU_KEY_PASSWORD")!!,
        )
    }
    val propertiesFile = rootProject.file("keystore.properties")
    if (!propertiesFile.isFile) return@run null
    val properties = Properties().apply { propertiesFile.inputStream().use { load(it) } }
    val required = listOf("storeFile", "storePassword", "keyAlias", "keyPassword")
    val missing = required.filter { properties.getProperty(it).isNullOrBlank() }
    if (missing.isNotEmpty()) {
        throw GradleException("${propertiesFile.path} is missing ${missing.joinToString()}")
    }
    // storeFile may be absolute or relative to apps/android/.
    ReleaseSigning(
        storeFile = rootProject.file(properties.getProperty("storeFile")),
        storePassword = properties.getProperty("storePassword"),
        keyAlias = properties.getProperty("keyAlias"),
        keyPassword = properties.getProperty("keyPassword"),
    )
}

android {
    useLibrary("android.test.base", false)
    useLibrary("android.test.runner", false)
    namespace = "ai.chintu"
    compileSdk = 35
    defaultConfig {
        applicationId = "ai.chintu"
        minSdk = 31
        targetSdk = 35
        versionCode = 4
        versionName = chintuVersion
        testInstrumentationRunner = "android.test.InstrumentationTestRunner"
    }
    signingConfigs {
        create("release") {
            releaseSigning?.let {
                storeFile = it.storeFile
                storePassword = it.storePassword
                keyAlias = it.keyAlias
                keyPassword = it.keyPassword
            }
            enableV1Signing = true
            enableV2Signing = true
        }
    }
    buildTypes {
        release {
            isMinifyEnabled = false
            isShrinkResources = false
            isDebuggable = false
            signingConfig = signingConfigs.getByName("release")
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

// Fail only when a release is actually signed, not at configuration time.
tasks.matching { it.name == "validateSigningRelease" || it.name == "packageRelease" }.configureEach {
    doFirst {
        if (releaseSigning == null) {
            throw GradleException(
                "Release signing is not configured. Set CHINTU_KEYSTORE, CHINTU_KEYSTORE_PASSWORD, " +
                    "CHINTU_KEY_ALIAS and CHINTU_KEY_PASSWORD, or create apps/android/keystore.properties " +
                    "with storeFile, storePassword, keyAlias and keyPassword (see scripts/package/build-apk.sh)."
            )
        }
        if (!releaseSigning.storeFile.isFile) {
            throw GradleException("Release keystore not found: ${releaseSigning.storeFile}")
        }
    }
}

kotlin { compilerOptions { jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17) } }

dependencies {
    // Google code scanner: system-provided QR scanning UI, no camera permission or bundled ML model.
    implementation("com.google.android.gms:play-services-code-scanner:16.1.0")
}
