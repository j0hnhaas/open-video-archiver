Open Video Archiver — Windows Portable
=====================================

Run:
  OpenVideoArchiver.exe

No Python installation is required on the target computer.

Portable runtime layout:
  OpenVideoArchiver.exe
  _internal\
  tools\ffmpeg\
  tools\deno\
  LICENSE
  SOFTWARE.md

Bundled tool folders are placed on the application's process-local PATH at
startup. The portable application prefers its bundled FFmpeg/FFprobe and Deno
tools over system-wide copies.

Open Video Archiver documents acquisition provenance and archive integrity. It
does not independently certify authorship, source authenticity or legal
admissibility.

Source:
  https://github.com/j0hnhaas/open-video-archiver

Copyright:
  (c) 2026, John G. Haas

Redistribution note
-------------------
This portable package can include third-party software such as Python, Qt /
PySide6, yt-dlp, FFmpeg, Deno and curl_cffi. See SOFTWARE.md and the upstream
projects for the applicable licenses and redistribution obligations. Review
those obligations before publishing a public binary release.


Privacy and network use
-----------------------
Open Video Archiver contains no project telemetry or analytics. Network access is
used to inspect and retrieve resources explicitly requested by the user. The
entered source URL is preserved as part of the archive provenance.

Uninstallation
--------------
This is a portable package. Close Open Video Archiver and delete this extracted
directory to remove it. No Windows service or system-wide installer is used.

Code signing
------------
See CODE_SIGNING.md in the source repository and the release notes for the
signing status of the specific release. Bundled upstream binaries are not signed
with the Open Video Archiver signing identity.
