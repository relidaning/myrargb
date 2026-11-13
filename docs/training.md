# Title training

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
