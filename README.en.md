# WeChat Codex Reply Loop

[中文说明](README.md)

An experimental Mac companion service that uses an already signed-in Codex CLI and an installed [iPhone Use](https://github.com/zhongerxin/iPhone-use) to run a contextual reply loop in iPhone WeChat.

In send mode, the loop follows: **detect unread chats → generate replies in queue order → verify and send → return to the chat list → repeat**.

**Version 0.2.0 is an experimental companion service. An installable Codex plugin and sustained unattended device acceptance remain pending.** Defaults are preview mode and an empty allowlist. Assisted device sends to personal and group chats have been verified. The repaired automatic loop has synthetic integration coverage; sustained unattended device acceptance remains pending. See [validation](docs/VALIDATION.md).

## 0.2.0 update — 2026-10-24

Repairs are built on the shared engine, preserving the other App interfaces and outreach rules. Unread work is persisted before opening a chat. Repeated avatar/profile navigation is removed; mute filtering uses list markers only. Explicitly configured groups now share the reply queue and retain speaker attribution. Sending recognizes `发送` or `send` with any keyboard and clicks the element rather than treating keyboard-relative rectangles as screen coordinates. Search return navigation, large-list budgets and long state-directory control sockets are repaired. See [CHANGELOG](CHANGELOG.md). This version includes the complete service source; real Qingteng, Jimu and Soul UI adapters remain pending.

## Behavior

- Process unread conversations from the bottom of each observed list snapshot upward; preserve that queue despite reply-induced list reordering. New input goes to the tail.
- Open only allowed unread personal or group chats, saving the task before clearing the badge. Read chats without existing work are not opened. Exclude service accounts and non-text input; filter mute status from the list without entering details.
- Generate up to two replies concurrently while keeping device operations and sends in queue order.
- Reuse the unique list-row/title binding instead of revisiting avatars. Check the current title, latest input and exact draft. Submit once; require one new outgoing bubble and an empty composer. A transient pending indicator permits two additional read-only checks, never another submit.

## Shared engine and manual outreach

The existing engine now includes App adapter interfaces, per-App daily scope and one queue for replies and proactive tasks. **Only WeChat has a real UI adapter. Qingteng, Jimu and Soul are planned; their real UI adapters remain unavailable. Selecting any of them returns `adapter_not_available` before constructing a phone connection.** Existing configurations default to `app_id: wechat` and retain the original allowlist, calibration and preview requirements.

Start each outreach round manually. Each target receives at most three proactive messages including the first, with at least 7,200 seconds between confirmed sends if unanswered. Incoming replies, observed manual messages, scope removal or a clear stop request end that target's round. No inactivity-based start, daily auto-start or automatic new round is scheduled. All task types reuse `persona_path`; replies take priority and device operations remain serial.

The non-WeChat policy defaults to all existing conversations when no list is configured for that local calendar day. An explicitly saved empty list sends to nobody. These rules are exercised with synthetic adapters; they never replace the WeChat allowlist.

```sh
PYTHONPATH=src python3 -m wechat_autoreply demo --scenario multi-app
```

See [multi-App commands](docs/MULTI_APP.md). Discovery snapshots expire after 300 seconds. After restart, existing follow-ups wait a fresh conservative two-hour window; missed intervals never cause a burst. Partial discovery cannot be used as an implicit complete contact list.

## Device requirements

Real device operation requires macOS, Python 3.9+, Codex CLI authentication and a working iPhone Use installation with its device build/signing/connection prerequisites. External software is not bundled. Tested integration: iPhone Use 0.3.8, Codex CLI 0.164.0, Chinese WeChat at 440 × 956 points in portrait. Other layouts are unverified and fail closed.

WeChat must remain foreground and the phone interactive. This is not an iOS background listener. A configured two-second polling interval is a delay between scans, not a response-time guarantee; page reads and AI calls take additional time. Coverage is limited to the configured number of observed pages.

Pagination requires a verified list top and two matching bounded passes. Reordering or timeout stops the scan; this adds latency.

## Offline first

From the checkout:

```sh
git clone https://github.com/s1y3r9e1w1l222/wechat-codex-reply-loop.git
cd wechat-codex-reply-loop
PYTHONPATH=src python3 -m wechat_autoreply demo
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

The demo and tests use synthetic data without a phone, online AI or credentials. Optional editable installation: `python3 -m pip install -e .`, exposing `wechat-reply`; build tools may need network access.

## Configuration and controls

```sh
cp config.example.json config.json
PYTHONPATH=src python3 -m wechat_autoreply --config config.json run
```

The Mac launcher `启动微信助手.command` also starts preview mode and includes common Homebrew/Node/Codex paths for its child process. Relative config paths resolve relative to the config file. `allowed` contains local conversation IDs mapped to unique list names; group IDs also belong in calibration `group_keys`. `plugin_path` may point to an installed iPhone Use 0.3.8; otherwise discovery is automatic. `persona_path` sets reply style. Optional `model` selects a Codex model; otherwise the CLI chooses it, which may differ from the current chat.

Replies use a separate ephemeral `codex exec` session with existing login, no additional API key and no inherited Codex chat history. Each request includes at most 20 messages / 8,000 characters from that conversation, and returns at most 300 characters. Generation tools are disabled. Context is sent to the Codex service and consumes account usage; account data policies still apply.

`calibrate` reads list markers without opening contacts. Verify the layout, markers and unique name mapping as described in [CALIBRATION.md](docs/CALIBRATION.md). Changing `verified` alone is not calibration. Avatar and mute-detail probes are no longer run. Preview persists unread work before opening it, so cleared badges do not erase pending replies. Existing tasks may reopen a read chat to finish or reconcile; this does not trigger a new reply from old history. Keep exclusive use of the phone during operation.

A fresh contact or changed identity calibration must complete preview first. Run normally, inspect `status` until `initial_preview_complete` is true, review the tasks/drafts, then stop and start with `--send`. The service refuses an unpreviewed contact. Persona files are limited to 4,000 characters and 16 KiB.

In another terminal with the same config, use `status`, `pause`, `resume` and `stop`. Pausing cancels future work but cannot recall a phone action already submitted. `needs_attention` requires inspecting the phone. `resolve JOB_ID --outcome sent|handled|abandoned` updates records only; it never sends. `rebaseline CONTACT_ID` reads a fresh baseline. `retry JOB_ID` only accepts failed or ambiguous jobs, never uncertain/sending/sent ones. Restart defaults to preview.

Startup first reconciles uncertain attempts by reading only. Unique new outgoing evidence updates the record; inconclusive evidence stays uncertain and requires attention, with no resubmission. Rebaseline retires old active versions. Retry refuses when a fresh input boundary cannot be reconstructed.

State and drafts remain in a private local SQLite directory and private Unix socket. `cleanup` removes expired historical observations and completed jobs, while unresolved work and current baselines remain; see [SECURITY.md](SECURITY.md). Upstream iPhone Use artifacts are stored separately and are not removed by this service.

Long state paths use a short same-user private control endpoint under `/tmp`; chat data stays in the configured directory. Read budgets are bounded and local to this service. Unlabelled custom keyboard controls, inconsistent history, connection loss and unknown unread boundaries still require attention; broader device stability is not claimed.

## Contributing and release

The source distribution includes runtime code, configuration examples, dependency declarations, tests and documentation. Local chats, screenshots, real contact mappings and private configuration are excluded. Configure your own device and account as described above.

See [CONTRIBUTING.md](CONTRIBUTING.md), [release preparation](docs/RELEASING.md) and [security/data handling](SECURITY.md). Public issues and pull requests must use synthetic, redacted data. GitHub CI is offline only; release scripts check tracked files/history and archive committed source without local state. Nothing is automatically pushed or published.

[MIT](LICENSE), with upstream notices in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Independent community project, unaffiliated with Tencent, WeChat, Apple or OpenAI.
