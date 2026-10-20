import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

// The upload key for Play stays outside the repo. release.sh explains the file.
val signingFile = file(
    System.getenv("WHISPR_MIC_SIGNING")
        ?: "${System.getProperty("user.home")}/.whispr-mic/upload.properties",
)

android {
    namespace = "net.huuhka.whisprmic"
    compileSdk = 36

    defaultConfig {
        applicationId = "net.huuhka.whisprmic"
        minSdk = 30
        targetSdk = 36
        // Play needs a higher versionCode for every upload; release.sh passes the commit count.
        versionCode = (findProperty("versionCode") as String?)?.toInt() ?: 1
        versionName = "0.1.0"
    }

    signingConfigs {
        if (signingFile.exists()) {
            create("release") {
                val signing = Properties().apply { signingFile.inputStream().use(::load) }
                storeFile = signingFile.resolveSibling(signing.getProperty("storeFile"))
                storePassword = signing.getProperty("storePassword")
                keyAlias = signing.getProperty("keyAlias")
                keyPassword = signing.getProperty("keyPassword")
            }
        }
    }

    buildTypes {
        release {
            signingConfig = signingConfigs.findByName("release")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

dependencies {
    testImplementation("junit:junit:4.13.2")
    // Android's org.json is a stub in local unit tests.
    testImplementation("org.json:json:20250517")
}
