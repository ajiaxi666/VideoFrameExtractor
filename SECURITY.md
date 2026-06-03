# Security

VideoFrameExtractor is a local Windows desktop tool. It does not require a cloud service to process videos.

## Sensitive Files

Do not upload or commit:

- original videos
- exported frames
- detection result JSON files for private projects
- feature cache files
- packaged output from `release/`
- local environment folders
- credentials or private configuration

Detection caches and exported images can reveal the content of a video project. Treat them as project data, not as harmless temporary files.

## Reporting

Report security or privacy issues through the maintainer channel used for this project. If the repository issue tracker is public, avoid posting private video details or sensitive file paths in public issues.

When reporting, include:

- affected version
- short reproduction steps
- expected behavior
- actual behavior
- whether private media or project data may be exposed

Please avoid attaching private videos or exported frames unless the maintainer explicitly asks for a sanitized sample.
