# Third-party notices

## iPhone Use

Upstream: <https://github.com/zhongerxin/iPhone-use>

Integration tested against installed plugin version **0.3.8**. The adapter in `src/wechat_autoreply/phone.py` reuses the installed Runtime, shared operation lock and session protocol, with integration logic adapted from that MIT-licensed project. Its copyright and full license are retained in [LICENSES/iphone-use-MIT.txt](LICENSES/iphone-use-MIT.txt).

iPhone Use must be installed separately. This repository does not redistribute its Runtime, WebDriverAgent, binaries, phone builds, signing material or screen viewer. Dependencies distributed with iPhone Use retain their own licenses; consult the upstream LICENSE and THIRD_PARTY_NOTICES before redistributing that project. Do not assume this repository's MIT license covers those external artifacts.

## Other external software and services

Python is required; this source package has no third-party Python runtime dependencies. Setuptools is used only for optional package building.

Codex CLI, the user's Codex account, Xcode, iOS and WeChat are external prerequisites, not bundled components. Their use and service terms remain separate. No trademark license, affiliation or endorsement is implied.

GitHub Actions in `.github/workflows/ci.yml` reference official checkout and setup-python releases by commit SHA; those actions execute on GitHub and are not copied into this package.
