# WeChat Codex Reply Loop

[中文说明](README.md)

An experimental Mac companion service that uses an already signed-in Codex CLI and an installed [iPhone Use](https://github.com/zhongerxin/iPhone-use) to run a contextual reply loop in iPhone WeChat.

In send mode, the loop follows: **detect unread chats → generate replies in queue order → verify and send → return to the chat list → repeat**.

**This is developer-facing source, not yet an installable Codex plugin or an unattended product.** Defaults are preview mode and an empty contact allowlist. A real File Transfer Assistant send was verified once. Ordinary contact identity calibration and a complete two-contact device test remain unfinished. See [validation](docs/VALIDATION.md).

## Behavior

- Process unread conversations from the bottom of each observed list snapshot upward; preserve that queue despite reply-induced list reordering. New input goes to the tail.
- Exclude muted and unknown-mute conversations. Version one supports explicitly allowed text-only personal chats; all groups, service accounts and non-text input are excluded.
- Generate up to two replies concurrently while keeping device operations and sends in queue order.
- Recheck identity, latest input, mute state and manual drafts before sending. Persist a checkpoint before submission; uncertain sends require human reconciliation, never automatic replay.

## Requirements

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

The Mac launcher `启动微信助手.command` also starts preview mode. Relative config paths resolve relative to the config file. `allowed` contains calibrated local contact IDs, not arbitrary display names. `plugin_path` may point to an installed iPhone Use 0.3.8; otherwise discovery is automatic. `persona_path` sets reply style. Optional `model` selects a Codex model; otherwise the CLI chooses it, which may differ from the current chat.

Replies use a separate ephemeral `codex exec` session with existing login, no additional API key and no inherited Codex chat history. Each request includes at most 20 messages / 8,000 characters from that conversation, and returns at most 300 characters. Generation tools are disabled. Context is sent to the Codex service and consumes account usage; account data policies still apply.

`calibrate` reads basic list markers only. Ordinary contacts require developer-assisted identity, mute-switch and navigation calibration as described in [CALIBRATION.md](docs/CALIBRATION.md), currently in Chinese. Changing `verified` alone is not a valid calibration. Only after actual verification and allowlisting may `run --send` enable sending for that run. Preview enters chats and can mark them read. Keep exclusive use of the phone during operation; software locks cannot prevent human touches.

A fresh contact or changed identity calibration must complete preview first. Run normally, inspect `status` until `initial_preview_complete` is true, review the tasks/drafts, then stop and start with `--send`. The service refuses an unpreviewed contact. Persona files are limited to 4,000 characters and 16 KiB.

In another terminal with the same config, use `status`, `pause`, `resume` and `stop`. Pausing cancels future work but cannot recall a phone action already submitted. `needs_attention` requires inspecting the phone. `resolve JOB_ID --outcome sent|handled|abandoned` updates records only; it never sends. `rebaseline CONTACT_ID` reads a fresh baseline. `retry JOB_ID` only accepts failed or ambiguous jobs, never uncertain/sending/sent ones. Restart defaults to preview.

Startup first reconciles uncertain attempts by reading only. Unique new outgoing evidence updates the record; inconclusive evidence stays uncertain and requires attention, with no resubmission. Rebaseline retires old active versions. Retry refuses when a fresh input boundary cannot be reconstructed.

State and drafts remain in a private local SQLite directory and private Unix socket. `cleanup` removes expired historical observations and completed jobs, while unresolved work and current baselines remain; see [SECURITY.md](SECURITY.md). Upstream iPhone Use artifacts are stored separately and are not removed by this service.

## Contributing and release

See [CONTRIBUTING.md](CONTRIBUTING.md), [release preparation](docs/RELEASING.md) and [security/data handling](SECURITY.md). Public issues and pull requests must use synthetic, redacted data. GitHub CI is offline only; release scripts check tracked files/history and archive committed source without local state. Nothing is automatically pushed or published.

[MIT](LICENSE), with upstream notices in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Independent community project, unaffiliated with Tencent, WeChat, Apple or OpenAI.
