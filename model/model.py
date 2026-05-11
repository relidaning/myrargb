import os
from datasets import Dataset
from transformers import (
    AutoTokenizer,
    AutoModelForSeq2SeqLM,
    Trainer,
    TrainingArguments,
    EarlyStoppingCallback,
)
import logging
from typing import List
from utils.bloom_utils import BloomUtils
from db_model import Movie
from db.repository import MovieRepository

logger = logging.getLogger(__name__)

movieRepository = MovieRepository()


class MyRargbModel:
    def __init__(
        self, model_name="t5-small", local_model_path="./data/my_finetuned_t5"
    ):
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
        max_length = 64
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
            min_new_tokens=4,
            num_beams=4,
            early_stopping=True,
        )
        title = self.tokenizer.decode(output[0], skip_special_tokens=True)
        logger.info(f"# Original: {item.filename} --> Predicted: {title} ")
        if not title:
            logger.info(f"x No title generated for: {item.filename}, skipping update.")
            return None

        return Movie(id=item.id, title=title)

    def train(self, items: List[Movie]):
        data = []
        for item in items:
            data.append(
                {
                    "id": item.id,
                    "noisy": f"clean the title: {item.filename}",
                    "clean": item.title_accurate,
                }
            )
        dataset = Dataset.from_list(data)
        dataset = dataset.train_test_split(test_size=0.2)

        train_dataset = dataset["train"].map(self.tokenize, batched=True)
        eval_dataset = dataset["test"].map(self.tokenize, batched=True)

        # Training
        training_args = TrainingArguments(
            output_dir="./data/results",
            num_train_epochs=50,
            learning_rate=2e-5,
            weight_decay=0.01,
            per_device_train_batch_size=2,
            logging_dir="./logs",
            eval_strategy="epoch",
            save_strategy="epoch",
            metric_for_best_model="eval_loss",
            greater_is_better=False,
            save_total_limit=2,
        )

        trainer = Trainer(
            model=self.model,
            args=training_args,
            train_dataset=train_dataset,
            eval_dataset=eval_dataset,
            processing_class=self.tokenizer,
            callbacks=[EarlyStoppingCallback(early_stopping_patience=3)],
        )

        trainer.train()

        # Save model
        self.model.save_pretrained(self.local_model_path)
        # Save tokenizer
        self.tokenizer.save_pretrained(self.local_model_path)


model = MyRargbModel()
