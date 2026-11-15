# Title training

## Try individual predictions

Open `/model/playground` (the **Try a title** link in Movies/TV navigation).
Enter a filename, select the application model and/or experimental models, and
click **Predict title**. Results show the extracted title and elapsed time per
model; cold model loads are included in that time. Ctrl/Cmd+Enter submits too.
The page works without external JavaScript/CDN dependencies and does not store
submitted text, predictions or catalog corrections.

`POST /model/predict` accepts JSON:

```json
{"text":"The.Matrix.1999.1080p.BluRay.x264-GROUP","models":["current","candidate-1"]}
```

Omitting `models` selects `current`. The response has `status` and `results`,
each containing `model`, `label`, `title` and `duration_ms`. IDs come only from
the displayed model choices; arbitrary model paths are rejected. Empty/invalid
requests and overlong inputs return 400, busy models return 409, unavailable
local models return 503, and unexpected failures return 500 without internal
details. Empty generated titles are displayed explicitly.

The application model uses the same loaded instance and operation lock as
normal predictions/training. The playground restores its previous training/eval
mode after inference. Experimental models load locally and one is cached at a
time; requests run serially. No training, catalog writes, model replacement or
checkpoint saves are performed. Selection of another model here does not change
the application's configured model.

`prediction_models_path` points to the existing comparison TOML allowlist;
candidate rows after its application row supply labels/paths. The current model
always uses `model_path`. `prediction_max_chars` (4096) and
`prediction_max_input_tokens` (256) reject excessive input rather than silently
truncating it. These settings use normal TOML/environment precedence
(`MYRARGB_PREDICTION_*`). Paths resolve from project root.

Payload-free completed-request events append to daily UTC application logs,
including failures and elapsed seconds. One `prediction_playground` event counts
each API request, including validation failures; per-model attempts use
`prediction_playground:<model-id>`. Text and generated titles are omitted. Reuse
the existing log visualization to inspect volume, failure rate, duration and
throughput:

```sh
.venv/bin/python scripts/consumer_report.py --group prediction_playground --days 7 --output /tmp/prediction-metrics.html
```

This report aggregates completed requests by UTC hour, reports malformed/limited
coverage and unavailable observations, and leaves raw logs out of the HTML.
Transformers/T5 remains the inference implementation; switching to an
incompatible model requires changing the prediction adapter, not just its path.

## Fresh-base experiment: first step

The isolated experiment configuration is `config/experiment.toml`; this does not
use the application's `model_path`. Prepare its data with:

```sh
.venv/bin/python scripts/prepare_experiment.py
```

Paths in this TOML resolve from the project root. `--config PATH` explicitly
selects another TOML; experiment settings do not inherit application or secret
environment configuration. Preparation reads the JSONL/JSON export only; it does
not access the database. It removes repeated inputs with identical cleaned labels,
rejects conflicting labels, and groups normalized target titles including TV
season/episode variants before partitioning. Fractions apply to title groups,
so row fractions may differ. Grouping is conservative: same-name titles can be
held together even across years, and aliases with different names need manual review.

The output contains fixed `validation.jsonl`, `test.jsonl`, the training pool,
nested exact-row-count training subsets and a manifest with source/output SHA-256
hashes and group assignments. Sizes exceeding the available pool are omitted;
the full pool is included as an additional size. Existing output is rejected to
protect frozen partitions; select a new output path for a deliberate new split.
An interrupted preparation directory must be reviewed before retrying with a new
path; absence of `manifest.json` means preparation did not complete.

`src/model/experiment_base.py` supplies the fresh-base loader: it rejects local
model paths, other model IDs and mutable revisions before importing ML code.
It loads model/tokenizer from the public T5 ID at a 40-character commit SHA,
without stored Hub credentials or application checkpoint fallback.

The independent runner uses the pinned public `base_revision` in the TOML:

```sh
.venv/bin/python scripts/run_experiment.py baseline
.venv/bin/python scripts/run_experiment.py train
```

Each command loads a new model/tokenizer directly from that revision. Training
creates a new optimizer/scheduler and explicitly disables checkpoint resume.
The application model loader and database are never imported. Baseline and
trial artifacts go into unique `runs/<run-id>/` folders inside the configured
experiment output, including a resolved configuration, library versions, source
provenance, validation hash, before/after metrics, epoch history, elapsed time,
measured peak allocated GPU memory, and (for training) saved model/checkpoints.
No test evaluation command is exposed during tuning. Baseline mode evaluates
validation only; train mode independently repeats the same baseline before fitting.
The preparation command itself does not load any model or run training.

The initial trial uses 100 training rows, three epochs, learning rate 0.0001,
device batch 2 and accumulation 4 (effective batch 8 on one GPU). These are smoke
trial settings, not tuned results. Tune `config/experiment.toml`, or pass
`--config PATH`; CLI selection overrides the default file. Experiment settings
intentionally do not inherit application environment variables. GPU is used if
available, otherwise CPU. Checkpoints are selected by maximum validation title
accuracy; tied scores retain the earlier checkpoint. Evaluation uses four beams,
maximum generation length 64 and minimum one new token. Inputs truncate at 256
tokens, labels at 64; revisit those bounds after token-length analysis if needed.

The runner verifies source/split hashes before loading weights. A changed frozen
file fails the run. Every completed or failed trial refreshes `charts.html` in
the experiment output from the last seven available daily logs (bounded to 32 MiB).
It displays before-training metrics at epoch zero and records failed-run status.
For a snapshot while training, run `scripts/training_report.py --output
data/experiments/t5-261114/charts.html` from the project root. No external chart
service receives metrics or examples. Generated reports contain configuration
and numeric metrics; keep them local. JSON lifecycle events append daily in UTC;
there is no automatic log retention. Failure reports contain exception class
and duration; detailed diagnostic tracebacks remain on the command's stderr.

Technology: standard-library JSON/TOML plus the shared title-normalization
functions own preparation; the manifest is the contract for the future runner.
Changing grouping or source data requires a new split output, not an in-place
rewrite. Preparation lifecycle events join the daily UTC training log.

## Experiment charts

### Compare the application checkpoint

```sh
.venv/bin/python scripts/compare_models.py
.venv/bin/python scripts/compare_models.py --protocol experiment
```

`config/model-comparison.toml` selects local checkpoints and a common decoding
protocol: task prefix from the experiment, four beams, greedy beam selection
without sampling, early stopping, maximum 64 new tokens and minimum one new
token. It compares the application's default `data/my_finetuned_t5` checkpoint
with all three seeds of the new 1e-4/effective-batch-16 configuration. Both
sides are evaluated again on the same frozen validation file, rather than
comparing scores from different protocols. This differs slightly from the
earlier Trainer evaluation's maximum-length and beam-stopping defaults.
`--protocol experiment` uses maximum total generation length 64 and
`early_stopping=false`, matching those earlier experiment settings. The default
application protocol uses maximum 64 new tokens and `early_stopping=true`.
Both protocols are defined alongside model paths in the same TOML; the CLI
protocol selection overrides `default_protocol`.

Models/tokenizers load locally with network and stored Hub credentials disabled.
The command neither trains nor saves models, imports the application model
wrapper, starts services, nor evaluates the test set. It verifies weight hashes
before/after evaluation and writes results plus weight provenance to
`data/experiments/t5-261114/comparisons/<id>/report.json`. `comparison.html` in
that directory visualizes numeric completion events from daily UTC logs. Each
model is released before the next is loaded. Runtime settings live in the TOML;
`--config PATH` selects another file, with storage paths anchored to project root.

The default application checkpoint is only a proxy for a running model unless
the live process/configuration has been verified separately. The existing
checkpoint's training exposure to validation titles is unknown, so its score
is a descriptive comparison, not an unbiased unseen-title benchmark. The new
models were also selected using validation. Keep any deployment decision
separate from this read-only comparison. Generated reports contain numeric
scores and model paths, not source examples or generated titles.

### Seed confirmation

```sh
.venv/bin/python scripts/sweep_experiment.py --config config/seed-sweep.toml
```

This confirms three tied configurations (5e-5/batch 8, 1e-4/batch 16,
1e-4/batch 32) with training seeds 42, 123 and 2026. The 720 training rows,
150 validation rows, five-epoch budget, decoding settings and public base
revision remain fixed. `training_seed` changes the training/data-sampling random
streams; it never regenerates the frozen split or selects different examples.
Seeds are repetitions for measuring variability, not hyperparameters to choose
by their best score. Seed 42 participated in candidate selection, so these
three-seed averages are still validation results, not an unbiased test estimate.

Matching completed fresh-base reports can be reused across sweep IDs when all
configuration values except `sweep_id` and `trial_id`, plus the manifest hash,
match exactly. This reuses the three seed-42 reports; six models are trained
from the pinned base. Changed seeds, learning rates, partitions or other settings
cannot reuse a mismatched result. New trials get fresh optimizers and never
resume previous checkpoints. Reuse events record the original run ID.

The new sweep directory contains `comparison.html` with per-seed values, mean,
sample standard deviation (n−1), range and mean strict accuracy, plus
`summary.json` with the same observations and run IDs. SD is in percentage
points in the HTML; JSON stores proportions. Dot/mean/SD charts derive from daily
completion logs and refresh after every trial. Partial groups show their actual
completed/expected counts; missing values are unavailable, and duplicate log
events do not add extra repetitions. Three seeds give only a preliminary
variability estimate. Test data remains excluded from evaluation and selection.

### Running the initial screen

```sh
.venv/bin/python scripts/sweep_experiment.py
```

`config/sweep.toml` selects the base experiment configuration and the screening
grid: 720 rows, five epochs, seed 42, learning rates 5e-5/1e-4/3e-4, and effective
batches 8/16/32 on one GPU. Device batch remains 2; accumulation changes. Every
child process initializes the pinned base and a fresh optimizer, verifies frozen
file hashes, repeats validation baseline evaluation, and writes its own model.
Equal epochs control data exposure, not update count: larger batches make fewer
optimizer steps, which are recorded in reports and the comparison table.

The content-addressed `data/experiments/t5-261114/sweeps/<id>/` directory contains
the saved plan, complete TOML configuration per trial and `comparison.html`.
The comparison heatmap/table is generated from numeric completion events in daily
logs and refreshed after each trial. Epoch curves remain at the experiment's
`charts.html`. Runs are serial; each child exits to release its model/GPU state.
The sweep's file lock prevents two copies of the same sweep from running together.
No external plotting service or application service is started.

Re-run the same command to recover from interruption: successful reports are
reused only when the complete trial configuration and frozen manifest hash match.
Failed/incomplete trials restart from base weights, without resuming checkpoints.
Changing a grid, setting or manifest creates a new sweep ID. Failures remain
visible, remaining settings are attempted, and any failure yields a nonzero exit.
Keep the run reports and logs: removing logs makes comparison observations
unavailable until the sweep command reconstructs completion events from reports.
This is a single-seed screen, not a robust estimate of the best hyperparameters;
confirm finalists across seeds before running a dataset-size comparison. Do not
choose hyperparameters using the test partition.

Generate a local offline report (no model loading or external chart service):

```sh
.venv/bin/python scripts/training_report.py --days 7
```

Open `data/training/report.html` in a browser. Re-run the command to refresh it;
`--run RUN_ID` filters one run and `--output PATH` selects the output location.
Daily UTC logs at `logs/YYYY-MM-DD.log` contain payload-free `training_event`
JSON records. Charts show training/validation loss and generated validation
accuracy by epoch, with a before/after table and recorded run settings. Records
append and roll over by UTC date; no automatic retention is applied. The reader
scans at most 32 MiB over 1–31 selected days and reports malformed records and
truncation. Empty metrics are unavailable, never zero. Reports are snapshots;
older runs without these events have no epoch observations in the charts.

The before-training score belongs to the **loaded model**, possibly an existing
fine-tuned checkpoint. A fresh-T5 experiment must explicitly load fresh base
weights. Every independent trial must start from the same base revision. Hold
validation/test membership fixed across training sizes and hyperparameter runs,
group related title releases together, and exclude test results from tuning.
The existing application training flow does not implement a separate test set.
The isolated runner above is separate from it; an automated multi-trial sweep
is not yet implemented.

Technology boundary: Transformers callbacks emit numeric training events;
Python's standard library generates SVG/HTML from those logs. Replacing the
renderer does not require retraining; changing the event schema requires updating
the reader. No additional charting dependencies are needed.

The **Train** button uses every valid corrected title, including examples from
previous runs. The badge counts only new or edited corrections that have not
been trained, excluding reserved validation examples. The button is disabled
when this count is zero; its tooltip reports pending validation corrections
separately. Starting a run still includes previous training examples. There is no
1,000-row limit. Empty targets and missing filenames are skipped and recorded in the run
report. Input filenames retain their original punctuation; corrected target
titles replace periods with spaces, collapse whitespace, and trim both ends.
Catalog titles are never rewritten by training preparation.

## Stable validation

The first run sorts normalized title groups, shuffles them with `training_seed`,
and reserves approximately `training_validation_fraction` of the groups for
validation. Title groups use the shared lowercase/no-whitespace/no-period key;
TV episode and season suffixes are removed for grouping. Every release of one
title belongs to one side, even across resolutions or years.

Membership is saved at `training_split_path` (`data/training/split.json`). Later
runs reuse those validation title groups; new titles join training, while new
releases of reserved titles remain in validation. Keep this manifest across
runs. Changing the seed or validation fraction requires deliberately choosing a
new manifest path. Editing a target into a different normalized title changes
its group; an old model's exposure cannot be undone by changing the split.

Previously trained examples may be in the initial validation set because older
runs used random splits. Reports list `previously_trained_validation_ids`, and
the app logs this limitation. Their validation results are useful for comparing
runs, but are not an unbiased unseen-title benchmark. To measure fresh-title
generalization, reserve newly corrected titles that the model has never trained
on in a separately managed evaluation set.

## Checkpoints and metrics

Training uses Transformers `Seq2SeqTrainer`, generates predictions for evaluation,
and stops after `training_early_stopping_patience` epochs without improvement.
The checkpoint with the lowest validation loss is restored before saving to
`model_path`, using `load_best_model_at_end=True`.

Every run has its own directory under `training_output_path`, containing
checkpoints, a split manifest copy, and `report.json`. The report records row IDs,
counts, skipped examples, baseline metrics from before training, metrics after
restoring the best checkpoint, and the best checkpoint path/loss.
Training logs use a corresponding run directory under `training_log_path`.

Generated metrics range from 0 to 1:

- `exact_match`: case-sensitive title accuracy after whitespace normalization.
- `title_match`: accuracy using the shared lowercase/no-space/no-period key.
- `eval_loss`: token prediction loss, used for checkpoint selection and stopping.

Validation and skipped rows are not newly marked trained. Successful training
marks only the training rows, and preserves corrections edited while a run was
in progress. Previously set flags are retained. Prediction and training share
an operation lock so inference cannot change model mode during training.

## Configuration and technology

Edit the commented defaults in `config/defaults.toml`, use a selected TOML file
with `MYRARGB_CONFIG`, or override with `MYRARGB_TRAINING_<SETTING>` environment
variables. Environment overrides take precedence; relative paths resolve from
the project root. Defaults retain the existing 50-epoch maximum, learning rate
0.00002, weight decay 0.01, batch size 2, token length 64, and patience 3.
The default seed is 42, validation fraction 0.2, and generation beam count 4.
Generation metrics add evaluation time compared with loss-only evaluation.

The title extraction model remains Transformers T5, with dependency versions in
`pyproject.toml`/`uv.lock`. Configuration changes tune this implementation;
replacing it with an incompatible model architecture requires adapter changes
and retraining. The JSON split is preprocessing state, separate from model
weights; deleting it changes the evaluation contract.

`generation_min_new_tokens = 1` controls the minimum output length for both
live prediction and generated-title evaluation. Edit it in `config/defaults.toml`
or override `MYRARGB_GENERATION_MIN_NEW_TOKENS`. A token is a tokenizer unit,
not necessarily a whole word. The setting takes effect when the app starts;
changing it requires no retraining.
