# Scripted critic verdicts

One `CriticVerdict` per fixture deck, written by hand from the known defects (docs/agents/README.md, P1, P6, P7,
P8, P10) and the calibration grades in `recur_deck/prompts/critic.md`. The fake Claude returns them in place of
a live Opus call (`tests/test_known_defects.py::test_d12_*`, `tests/test_critic.py`). The Ai2000 verdict says
`mail_ready: true` on purpose: major findings must force it to false.
