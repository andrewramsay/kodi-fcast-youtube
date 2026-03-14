# kodi-fcast-youtube

**NOTE**: this plugin requires a modified version of the Grayjay Android app, it **WILL NOT WORK** with the stock version. See below for details. 

## What is this?

A Kodi plugin that allows the [Grayjay Android app](https://gitlab.futo.org/videostreaming/grayjay) to cast YouTube videos to a Kodi device using the [FCast](https://fcast.org/#what-is-fcast) protocol.


## Why?

I used to use the official YouTube app with a Chromecast attached to my Kodi device to cast videos, and wanted to do the same thing with Kodi+Grayjay instead. 

I initially tried [this plugin](https://github.com/c4valli/kodi-fcast-receiver) which did work sometimes but being a general FCast receiver it unsurprisingly often had issues with YouTube playback.

## Is this useful?

Only if the following things are true:

 * You would like to cast YouTube (and *only* YouTube) videos from the Grayjay Android app to a Kodi device
 * You are comfortable building your own slightly modified copy of the Android app. The way the plugin is implemented requires a change to the structure of the FCast `play` messages the app normally sends to a connected client.

## Why is a modified Grayjay app required?

The `kodi-fcast-receiver` plugin linked above attempts to playback requested videos by using the [InputStream Adaptive](https://github.com/xbmc/inputstream.adaptive) plugin. This probably works fine for non-YouTube sources, but struggles to play most YouTube videos I tested it with. 

However Kodi also has a dedicated and frequently updated [YouTube plugin](https://github.com/anxdpanic/plugin.video.youtube), and I thought it would be ideal if I could just hand off playback requests and have it deal with the obstacles YouTube creates for unofficial clients. 

Unfortunately the Grayjay app only sends a DASH manifest to the connected FCast receiver, and this doesn't seem to contain any references to the original video URL or ID.

To get around this, I made a couple of very small modifications to the Grayjay app's casting code. The changes replace the contents of one of the normal message fields with the YouTube video ID.

All this plugin does is receive these slightly modified FCast messages, construct an appropriate URL targeting the Kodi YouTube plugin, and tell Kodi to play from that source. 

The YouTube plugin then kicks in and handles the actual playback.

## How do I use it?

1. Install the addon zip file from the [releases](https://github.com/andrewramsay/kodi-fcast-youtube/releases) page
2. Build your own patched version of Grayjay (backup/export your user data first!)

## How do I build a patched version of Grayjay?

The changes required to the Grayjay source code are minimal, see the diff below. 
```diff
diff --git a/app/src/main/java/com/futo/platformplayer/casting/StateCasting.kt b/app/src/main/java/com/futo/platformplayer/casting/StateCasting.kt
index 4c3e3323..fdf6d5e2 100644
--- a/app/src/main/java/com/futo/platformplayer/casting/StateCasting.kt
+++ b/app/src/main/java/com/futo/platformplayer/casting/StateCasting.kt
@@ -655,7 +655,8 @@ abstract class StateCasting {
 
         Logger.i(TAG, "Direct dash cast to casting device (videoUrl: $videoUrl, audioUrl: $audioUrl).");
         Logger.v(TAG) { "Dash manifest: $content" };
-        ad.loadContent("application/dash+xml", content, resumePosition, video.duration.toDouble(), speed, metadataFromVideo(video));
+        ad.loadContent("application/dash+xml", video.id.value.orEmpty(), resumePosition, video.duration.toDouble(), speed, metadataFromVideo(video));
 
         return listOf(videoUrl ?: "", audioUrl ?: "", subtitlesUrl ?: "", videoSource?.getVideoUrl() ?: "", audioSource?.getAudioUrl() ?: "", subtitlesUri.toString());
     }
@@ -1282,7 +1283,7 @@ abstract class StateCasting {
         }
 
         Logger.i(TAG, "added new castDash handlers (dashPath: $dashPath, videoPath: $videoPath, audioPath: $audioPath).");
-        ad.loadVideo(if (video.isLive) "LIVE" else "BUFFERED", "application/dash+xml", dashUrl, resumePosition, video.duration.toDouble(), speed, metadataFromVideo(video));
+        ad.loadVideo(if (video.isLive) "LIVE" else "BUFFERED", "application/dash+xml", video.id.value.orEmpty(), resumePosition, video.duration.toDouble(), speed, metadataFromVideo(video));
 
         return listOf()
     }

```

I only update my copy of the app every few months, so the changes below may not work on the latest version. The commit I'm currently using is `d1336c711a4d475e34165656add4194ba5f68cef` from March 3rd 2026.


Current builds of Grayjay require the [FCast Sender SDK](https://github.com/futo-org/fcast/tree/master/sdk/sender). This requires some additional work to configure before you can build Grayjay itself:

1. Create a new directory and clone both the [FCast SDK](https://github.com/futo-org/fcast/) and the [fcast-sdk-jitpack](https://gitlab.futo.org/videostreaming/fcast-sdk-jitpack) repos into it. 
2. Make sure you have all the required tools as described in the [Sender SDK README](https://github.com/futo-org/fcast/blob/master/sdk/sender/README.md) (you could also try using the provided `Dockerfiles`, I haven't tested this)
3. Run `cargo install cargo-ndk`
4. Go to the `fcast` repo path and run `cargo build -p xtask`
5. Run the build command from the README: `cargo xtask kotlin build-android-library --release --src-dir ../fcast-sdk-jitpack/src` (change the `--src-dir` path if needed)
6. Go to the `fcast-sdk-jitpack` repo and run `./gradlew publishToMavenLocal`. If this fails you might need to define the `ANDROID_HOME` environment variable to point to the location of your SDK

As for actually compiling Grayjay itself, I'm not going to try and provide detailed instructions for this because Android builds are so prone to breakage there wouldn't be much point. 

In theory you should "just" have to run a normal build. With Android Studio, try `Build > Generate App Bundles or APKs > Generate APKs`.

A few hopefully helpful notes:
 * If you have the stock version of the app installed already, remove it before trying to install the modified version as they won't be compatible (remember to backup/export your settings so you can import them back into the modified app)
 * Be sure to select an `unstableRelease` build, using the default `unstableDebug` target will work but will run very slowly
 * You'll need to generate your own [keystore file](https://developer.android.com/studio/publish/app-signing#generate-key) to sign the generated APK
 * You'll also need to modify Grayjay's `app/build.gradle` file to tell it to load the keystore properties from a different path (it defaults to `/opt/key.properties`). For an example of what this file should contain, see [here](https://developer.android.com/studio/publish/app-signing#secure-shared-keystore)

## Known problems

I haven't bothered trying to make this particularly robust. Basic playback and controls are working, but it can be buggy at times.

I would strongly recommend using an app like [Yatse](https://play.google.com/store/apps/details?id=org.leetzone.android.yatsewidgetfree&hl=en_GB) to control seeking/playback/volume once a video is actually playing. What I do is to use the YouTube app to trigger playback, then switch to Yatse for volume control (Grayjay doesn't seem to register hardware volume button presses) and seeking forward/backward since it's more reliable and responsive that way.

There are also a lot of debug/logging statements scattered through the code, and some events will also generate notifications. If you don't want these they should be easy to strip out.

As stated above, the plugin doesn't do any playback handling itself so if you encounter issues with that your first step should be to go and install [the latest release](https://github.com/anxdpanic/plugin.video.youtube/releases) of the YouTube plugin (it is often updated quite frequently so always worth checking if you have the latest version!).


