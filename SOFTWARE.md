# Software used by Open Video Archiver

This repository coordinates several independent software components.

| Component | Provider / project | Role | License | Official availability |
|---|---|---|---|---|
| Open Video Archiver | John G. Haas / repository contributors | Documented archive workflow | MIT | https://github.com/j0hnhaas/open-video-archiver |
| Python | Python Software Foundation and contributors | Runtime and standard library | Python Software Foundation License Version 2, plus applicable historical compatible licenses | https://www.python.org/ |
| yt-dlp | yt-dlp project and contributors | Metadata retrieval and media/subtitle download | The Unlicense | https://github.com/yt-dlp/yt-dlp |
| yt-dlp-ejs | yt-dlp project and contributors | External JavaScript challenge solver scripts | The Unlicense | https://github.com/yt-dlp/ejs |
| Deno | Deno authors / Deno Land Inc. | JavaScript runtime used by yt-dlp EJS challenge solving | MIT | https://deno.com/ |
| curl_cffi | curl_cffi project and contributors | HTTP browser-impersonation support used by yt-dlp where required | MIT | https://github.com/lexiforest/curl_cffi |
| FFmpeg | FFmpeg project and contributors | Media merging and metadata/thumbnail processing | Build-dependent; commonly LGPL/GPL depending on configuration | https://ffmpeg.org/ |

## Open Video Archiver license

Open Video Archiver is distributed under the MIT License.

Copyright (c) 2026 John G. Haas.

The copyright and permission notice must be retained in copies or substantial portions of the software.

## FFmpeg note

The effective license of a particular FFmpeg binary depends on its build configuration and enabled components.

Open Video Archiver therefore records the exact `ffmpeg -version` banner and build configuration in every archive's own `SOFTWARE.md`.

## Per-archive provenance

Every successful archive session records the versions actually used on that machine, including:

- Open Video Archiver version
- Python version
- yt-dlp version
- yt-dlp-ejs version
- Deno runtime version and path
- curl_cffi version
- FFmpeg version and executable filename
- FFmpeg build configuration
- provider/project and license information
- whether a browser was used for retrieval

Open Video Archiver deliberately avoids storing unnecessary private absolute paths in generated archive provenance.

At present, retrieval is performed directly by yt-dlp; the generated record therefore states `Browser: not used`.
