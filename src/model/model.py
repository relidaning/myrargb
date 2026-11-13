import os
from threading import Lock
from copy import deepcopy
from datetime import datetime, timezone
from uuid import uuid4
from model.training_data import prepare_split, read_manifest, write_json, title_accuracy
from utils.settings import settings
import logging
from typing import List
from db_model import Movie

logger = logging.getLogger(__name__)

class MyRargbModel:
    def __init__(
        self, model_name=None, local_model_path=None
    ):
        from transformers import AutoTokenizer, AutoModelForSeq2SeqLM

        model_name = model_name or settings.model_name
        local_model_path = str(local_model_path or settings.model_path)
        self.model_name = model_name
        self.local_model_path = local_model_path
        if os.path.exists(local_model_path):
            self.tokenizer = AutoTokenizer.from_pretrained(local_model_path)
            self.model = AutoModelForSeq2SeqLM.from_pretrained(local_model_path)
        else:
            self.tokenizer = AutoTokenizer.from_pretrained(model_name)
            self.model = AutoModelForSeq2SeqLM.from_pretrained(model_name)

    def tokenize(self, batch):
        padding = "max_length"
        max_length = settings.training_max_length
        inputs = self.tokenizer(
            batch["noisy"],
            padding=padding,
            truncation=True,
            max_length=max_length,
        )
        labels = self.tokenizer(
            text_target=batch["clean"],
            padding=padding,
            truncation=True,
            max_length=max_length,
        )
        label_ids = labels["input_ids"]
        # VERY IMPORTANT
        label_ids = [
            [token if token != self.tokenizer.pad_token_id else -100 for token in label]
            for label in label_ids
        ]
        inputs["labels"] = label_ids

        return inputs

    def predict(self, item: Movie) -> Movie | None:
        if not item:
            return None

        device = self.model.device
        input = self.tokenizer(f"clean the title: {item.filename}", return_tensors="pt")
        input = {k: v.to(device) for k, v in input.items()}
        output = self.model.generate(
            **input,
            max_new_tokens=64,
            min_new_tokens=settings.generation_min_new_tokens,
            num_beams=4,
            early_stopping=True,
        )
        title = self.tokenizer.decode(output[0], skip_special_tokens=True)
        logger.info(f"# Original: {item.filename} --> Predicted: {title} ")
        if not title:
            logger.info(f"x No title generated for: {item.filename}, skipping update.")
            return None

        return Movie(id=item.id, title=title)

    def compute_metrics(self, prediction):
        import numpy as np

        predictions, labels = prediction
        if isinstance(predictions, tuple):
            predictions = predictions[0]
        pad = self.tokenizer.pad_token_id
        predictions = np.where(np.asarray(predictions) == -100, pad, predictions)
        labels = np.where(np.asarray(labels) == -100, pad, labels)
        return title_accuracy(
            self.tokenizer.batch_decode(predictions, skip_special_tokens=True),
            self.tokenizer.batch_decode(labels, skip_special_tokens=True),
        )

    def train(self, items: List[Movie]):
        from datasets import Dataset
        from transformers import Seq2SeqTrainer, Seq2SeqTrainingArguments, EarlyStoppingCallback, set_seed

        split = prepare_split(items, settings.training_seed, settings.training_validation_fraction,
                              read_manifest(settings.training_split_path))
        set_seed(settings.training_seed)
        write_json(settings.training_split_path, split.manifest)
        run_name = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + uuid4().hex[:8]
        run_path = settings.training_output_path / run_name
        report = split.summary()
        report['split_path'] = str(settings.training_split_path)
        report['seed'] = settings.training_seed
        write_json(run_path / 'split.json', split.manifest)
        write_json(run_path / 'report.json', report)
        logger.info('Training with %s examples; %s fixed validation examples (%s previously trained).',
                    len(split.train), len(split.validation), len(split.previously_trained_validation_ids))
        if split.previously_trained_validation_ids:
            logger.warning('Some validation examples were used in older training runs; '
                           'these metrics are not a clean unseen-data benchmark.')

        def tokenize_rows(rows):
            dataset = Dataset.from_list(rows)
            return dataset.map(self.tokenize, batched=True, remove_columns=dataset.column_names)

        generation_config = deepcopy(self.model.generation_config)
        generation_config.min_new_tokens = settings.generation_min_new_tokens
        training_args = Seq2SeqTrainingArguments(
            output_dir=str(run_path),
            num_train_epochs=settings.training_epochs,
            learning_rate=settings.training_learning_rate,
            weight_decay=settings.training_weight_decay,
            per_device_train_batch_size=settings.training_batch_size,
            logging_dir=str(settings.training_log_path / run_name),
            logging_strategy="epoch",
            eval_strategy="epoch",
            save_strategy="epoch",
            load_best_model_at_end=True,
            metric_for_best_model="eval_loss",
            greater_is_better=False,
            save_total_limit=2,
            seed=settings.training_seed,
            data_seed=settings.training_seed,
            predict_with_generate=True,
            generation_config=generation_config,
            generation_max_length=settings.training_max_length,
            generation_num_beams=settings.training_num_beams,
            report_to="none",
        )
        trainer = Seq2SeqTrainer(
            model=self.model,
            args=training_args,
            train_dataset=tokenize_rows(split.train),
            eval_dataset=tokenize_rows(split.validation),
            processing_class=self.tokenizer,
            compute_metrics=self.compute_metrics,
            callbacks=[EarlyStoppingCallback(early_stopping_patience=settings.training_early_stopping_patience)],
        )
        report['baseline_metrics'] = trainer.evaluate(metric_key_prefix='baseline')
        write_json(run_path / 'report.json', report)
        trainer.train()
        # Trainer reloads the lowest-validation-loss checkpoint before this save.
        report['final_metrics'] = trainer.evaluate()
        report['best_checkpoint'] = trainer.state.best_model_checkpoint
        report['best_validation_loss'] = trainer.state.best_metric
        report['run_path'] = str(run_path)
        self.model = trainer.model
        self.model.save_pretrained(self.local_model_path)
        self.tokenizer.save_pretrained(self.local_model_path)
        write_json(run_path / 'report.json', report)
        return report


class LazyModel:
    """Load ML dependencies and weights once, when a model operation is requested."""

    def __init__(self):
        self._instance = None
        self._lock = Lock()
        self._operation_lock = Lock()

    def _get(self):
        with self._lock:
            if self._instance is None:
                self._instance = MyRargbModel()
            return self._instance

    def predict(self, item):
        with self._operation_lock:
            return self._get().predict(item)

    def train(self, items):
        if not self._operation_lock.acquire(blocking=False):
            raise RuntimeError('The model is busy; retry training after the current operation finishes.')
        try:
            return self._get().train(items)
        finally:
            self._operation_lock.release()


model = LazyModel()
