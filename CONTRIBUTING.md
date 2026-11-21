# Contributing to Thirdeye / DrishtiGuide

Thanks for helping. This is an assistive-technology project, so correctness, honesty about uncertainty and accessibility matter more than features.

## Ground rules

- **Never invent what the camera cannot show.** Changes to prompts or routing must keep the "admit uncertainty" and safety-refusal behaviour.
- **Keep Malayalam output natural and short.** Prefer 1-3 sentences. If you are not a Malayalam speaker, say so in the pull request so a native speaker can review.
- **Accessibility first.** Every interactive Android view needs a label, a 48dp touch target and must not rely on colour alone.
- **No secrets in the repo.** `.env`, tokens and `debug/` are git-ignored. Do not paste real tokens into issues.

## Development setup

```sh
python -m venv .venv && .venv/bin/pip install -r requirements.txt httpx
.venv/bin/python -m unittest discover -s tests        # server tests (offline)
cd android && ./gradlew testDebugUnitTest assembleDebug   # app tests + APK
```

Live checks against Sarvam are opt-in and cost API credits: `DRISHTIGUIDE_LIVE=1 python -m unittest discover -s tests -p test_live.py`.

## Pull requests

1. Open an issue first for anything larger than a small fix.
2. Add or update tests. Server logic that picks a route, parses model output or composes Malayalam text needs a unit test.
3. Keep changes focused, and keep both test suites green. CI runs them on every push.
4. Describe how you verified it, ideally on a real phone, and include `/debug` or History screenshots for behaviour changes (blur anything private).

## Reporting bugs

Use the bug template. Include the phone model and Android version, whether the screen was on, and the matching `/debug` entry or History card.
