# Jev screening evaluation

The eval harness arrives in Phase 11. It will hold:
- a golden set of anonymised, labelled resumes
- the metrics: Spearman against manual ratings, precision@10, knockout accuracy, calibration, tokens/cost per application, and the detection rate on injection fixtures
- reports under `eval/reports/`

`make eval` uses the `FakeJevClient`. `make eval-live` is opt-in and is the only thing that ever calls the live Jev API; CI never does.
