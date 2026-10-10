plugins { id("com.android.application"); id("org.jetbrains.kotlin.android") }
android {
    useLibrary("android.test.base", false)
    useLibrary("android.test.runner", false)
    namespace = "ai.chintu"
    compileSdk = 35
    defaultConfig {
        applicationId = "ai.chintu"
        minSdk = 31
        targetSdk = 35
        versionCode = 3
        versionName = "0.3.0"
        testInstrumentationRunner = "android.test.InstrumentationTestRunner"
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

kotlin { compilerOptions { jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17) } }

dependencies {
    // Google code scanner: system-provided QR scanning UI, no camera permission or bundled ML model.
    implementation("com.google.android.gms:play-services-code-scanner:16.1.0")
}
