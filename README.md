# GitLab Monitor for Home Assistant

[![CI/CD](https://github.com/lukislp/gitlab-monitor-hacs/actions/workflows/ci-cd.yml/badge.svg)](https://github.com/lukislp/gitlab-monitor-hacs/actions/workflows/ci-cd.yml)
[![Release](https://img.shields.io/github/v/release/lukislp/gitlab-monitor-hacs)](https://github.com/lukislp/gitlab-monitor-hacs/releases)
[![License: MIT](https://img.shields.io/github/license/lukislp/gitlab-monitor-hacs)](LICENSE)
[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz/)
[![Coverage](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/lukislp/gitlab-monitor-hacs/main/.github/badges/coverage.json)](https://github.com/lukislp/gitlab-monitor-hacs/actions/workflows/ci-cd.yml)

A custom integration that monitors GitLab repositories — on **gitlab.com** or any **self-hosted** GitLab instance. Fully UI-configurable (config flow), no YAML required and no external Python dependencies.

## Features

- Connect via **instance URL + Personal Access Token** (scope `read_api` is sufficient; the GitLab API does not support username/password login)
- Project picker: select from your memberships **or** add any project by path (`group/project`) or numeric ID
- One **device per repository** with these entities:

| Entity | Type | Notes |
|---|---|---|
| Pipeline status | Sensor (enum) | latest pipeline; attributes: ref, SHA, source, timings, web URL, triggered by |
| Pipeline duration | Sensor | disabled by default |
| Pipeline failed | Binary sensor (problem) | `on` when the latest pipeline failed |
| Pipeline running | Binary sensor (running) | `on` while a pipeline is created/pending/running |
| Open issues | Sensor | |
| Open merge requests | Sensor | |
| Branches | Sensor | |
| Stars / Forks | Sensors | |
| Pipeline coverage | Sensor (%) | from the latest pipeline |
| Latest issue | Sensor | state = title; attributes: number, author, labels, URL |
| Latest merge request | Sensor | state = title; attributes: number, branches, author, URL |
| Latest deployment | Sensor | state = environment; attributes: status, ref, SHA |
| Contributors / Members | Sensors | |
| Commits | Sensor | total commit count (needs Reporter role) |
| Last commit | Sensor (timestamp) | attributes: title, message, author, short SHA, URL |
| Last activity | Sensor (timestamp) | attributes: project metadata |
| Latest release / Releases count | Sensors | count disabled by default |
| Latest tag / Tags count | Sensors | disabled by default |
| Repository size / Storage size | Sensors | disabled by default (needs Reporter role) |
| Fetches (30 days) | Sensor | disabled by default (needs Maintainer role) |
| Refresh | Button | triggers an immediate update |

- Configurable polling interval (1–1440 min, default 1) via the **Options** dialog
- Add/remove projects at any time via **Options** — no restart needed
- **Reauth flow** when the token expires, **Reconfigure** to change URL/token/SSL
- Optional SSL verification toggle for self-hosted instances with self-signed certificates
- Diagnostics download with redacted token
- English and German translations

## Installation

### Manual
1. Copy the folder `custom_components/gitlab_monitor` into your Home Assistant `config/custom_components/` directory (result: `config/custom_components/gitlab_monitor/`).
2. Restart Home Assistant.
3. Go to **Settings → Devices & Services → Add Integration** and search for **GitLab Monitor**.

### HACS (custom repository)
In HACS, add `https://github.com/lukislp/gitlab-monitor-hacs` as a custom repository of type *Integration*, then install **GitLab Monitor** and restart Home Assistant.

## Integration icon (Devices & Services)

The integration ships its own icon under `custom_components/gitlab_monitor/brand/` — Home
Assistant 2026.3+ picks it up automatically (local brand images take priority over the
central brands CDN), no [home-assistant/brands](https://github.com/home-assistant/brands)
submission needed. The design is an original one, deliberately not the trademarked GitLab
tanuki.

## Setup

1. Create a token in GitLab: **User Settings → Access Tokens** → scope `read_api` (a project or group access token works too).
2. In the config flow, enter the GitLab URL (default `https://gitlab.com`) and the token.
3. Select the projects to monitor. Projects you are not a member of (e.g. public repos) can be added in the free-text field, one per line, e.g. `gitlab-org/gitlab`.

## Automation example

```yaml
triggers:
  - trigger: state
    entity_id: binary_sensor.group_repo_pipeline_failed
    to: "on"
actions:
  - action: notify.mobile_app_phone
    data:
      title: "CI failed"
      message: >
        Pipeline on {{ state_attr('sensor.group_repo_pipeline_status', 'ref') }}
        failed: {{ state_attr('sensor.group_repo_pipeline_status', 'web_url') }}
```

## Notes

- API calls per project and update cycle: ~13. With many projects, increase the polling interval to stay well within GitLab rate limits.
- On gitlab.com, exact counts are unavailable for collections larger than 10 000 items; the sensor then reports a lower bound (`count_is_exact: false` attribute).
