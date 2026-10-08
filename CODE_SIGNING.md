# Code signing policy

Open Video Archiver is preparing its release process for the SignPath Foundation Open Source Code Signing program.

The current public Windows release, **v1.0.0**, is unsigned. Until the project has been accepted and signing is activated, release notes will continue to identify unsigned builds explicitly.

When signing is enabled, the project will use:

> **Free code signing provided by SignPath.io, certificate by SignPath Foundation.**

## Project and repository

- Project: Open Video Archiver
- Source repository: https://github.com/j0hnhaas/open-video-archiver
- License: MIT
- Maintainer: John G. Haas (@j0hnhaas)

## Code-signing roles

Open Video Archiver is currently a single-maintainer project. The signing roles therefore currently coincide:

- **Authors / committers:** John G. Haas (@j0hnhaas)
- **Reviewers:** John G. Haas (@j0hnhaas)
- **Approvers:** John G. Haas (@j0hnhaas)

Contributions from people without direct commit access are reviewed before merge. Every release-signing request requires manual approval in SignPath.

All accounts used for repository and signing administration are expected to use multi-factor authentication.

## Privacy policy

**This program will not transfer any information to other networked systems unless specifically requested by the user or the person installing or operating it.**

Open Video Archiver does not contain project telemetry, advertising, analytics or background usage reporting.

Network communication occurs when the user asks the program to inspect or retrieve an online-video source. The underlying open-source components, including yt-dlp, may communicate with the requested source and with network services that are technically required by that source. The entered source URL is intentionally preserved as archive provenance.

Open Video Archiver avoids intentionally storing credentials, cookies, authentication tokens or unnecessary absolute local paths in its archive manifest. Users should not provide secret-bearing URLs unless they intentionally want the URL preserved in the archive record.

## Release and origin policy

Release-signing candidates are built by GitHub Actions on GitHub-hosted Windows runners from the public source repository.

The signing pipeline is designed so that:

1. source and build scripts are version-controlled in this repository;
2. the release candidate is built on a GitHub-hosted runner;
3. the unsigned candidate is uploaded as a GitHub Actions artifact before any signing request;
4. that GitHub artifact ID is submitted to SignPath;
5. SignPath origin verification can bind the signing request to the repository, workflow and commit;
6. the signing request requires manual approval;
7. the signed result is verified before a release ZIP and SHA-256 checksum are produced.

The implementation is in `.github/workflows/release-signing.yml`.

## What may be signed

The Open Video Archiver signing identity is only for binaries built from source maintained in this repository.

The intended signed binary is:

- `OpenVideoArchiver.exe`

The portable distribution also contains upstream open-source software. In particular, FFmpeg/FFprobe, Deno, Python/Qt runtime files and other third-party libraries are **not** to be signed with the Open Video Archiver signing identity merely because they are included in the package.

The SignPath Artifact Configuration must enforce the Open Video Archiver product name and version metadata and restrict signing to the project's own binary or binaries.

## Release dependency controls

The GitHub release workflow uses `requirements-release.txt` for pinned top-level Python release dependencies.

The external FFmpeg and Deno archives used by the release workflow are pinned to explicit versions and verified against SHA-256 values in `tools/prepare_release_tools.ps1`.

Any version update to release dependencies or bundled external tools is therefore a source-controlled change.

## Uninstallation

### Windows portable

Close Open Video Archiver and delete the extracted Open Video Archiver directory. The portable package does not install a Windows service or a system-wide application.

### Source / pip installation

Run:

```powershell
python -m pip uninstall open-video-archiver
```

The optional installation helper may add the Python user Scripts directory to the user's `PATH`. Because that directory can be shared by other Python applications, remove it from `PATH` only if it is no longer required by anything else.
