plugins {
    id("com.android.application")
}

val licenseAssets = layout.buildDirectory.dir("generated/licenseAssets")
val generateLicenseAssets = tasks.register<Sync>("generateLicenseAssets") {
    from(listOf(rootProject.file("LICENSE"), rootProject.file("NOTICE"))) {
        into("licenses")
    }
    into(licenseAssets)
}

android {
    namespace = "com.fairytrick.fairymahjong"
    compileSdk = 37
    buildToolsVersion = "36.0.0"

    defaultConfig {
        applicationId = "com.fairytrick.fairymahjong"
        minSdk = 26
        targetSdk = 37
        versionCode = 5
        versionName = "0.1.4"
        testInstrumentationRunner = "com.fairytrick.fairymahjong.ReliabilityInstrumentation"
    }

    buildTypes {
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"))
            vcsInfo { include = false }
            // Intentionally unsigned: F-Droid builds and signs its own release.
        }
    }

    dependenciesInfo {
        // Keep encrypted dependency metadata out of release packages.
        includeInApk = false
        includeInBundle = false
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    lint {
        abortOnError = true
        checkReleaseBuilds = true
    }

    sourceSets.getByName("main").assets.directories.add(licenseAssets.get().asFile.path)
}

tasks.named("preBuild").configure { dependsOn(generateLicenseAssets) }

kotlin {
    compilerOptions {
        jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17)
    }
}

configurations.configureEach {
    resolutionStrategy.failOnDynamicVersions()
    resolutionStrategy.failOnChangingVersions()
}

dependencies {
    testImplementation("junit:junit:4.13.2")
}

tasks.register<Test>("testHintMemory") {
    group = "verification"
    description = "Runs the difficult hint regression with a 64 MiB heap."
    // AGP registers variant test tasks later. Resolve their compiled inputs only when
    // Gradle builds this task's dependency graph, without executing the full test suite.
    val ordinaryTests = providers.provider { tasks.named<Test>("testDebugUnitTest").get() }
    testClassesDirs = files(ordinaryTests.map { it.testClassesDirs })
    classpath = files(ordinaryTests.map { it.classpath })
    dependsOn(ordinaryTests.map { it.taskDependencies.getDependencies(it) })
    maxHeapSize = "64m"
    useJUnit()
    filter { includeTestsMatching("com.fairytrick.fairymahjong.HintMemoryTest") }
    testLogging {
        events("failed")
        exceptionFormat = org.gradle.api.tasks.testing.logging.TestExceptionFormat.FULL
    }
}
