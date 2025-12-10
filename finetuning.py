import os
from datasets import Dataset
from transformers import AutoTokenizer, AutoModelForSeq2SeqLM, Trainer, TrainingArguments
from crawl_rargb import crawl_rargb
from db import db
from workflow import Workflow

class MyRargbModel():
  
  
  def __init__(self, model_name="t5-small", local_model_path='./my_finetuned_t5'):
    self.model_name = model_name
    self.local_model_path = local_model_path
    if os.path.exists(local_model_path):
      self.tokenizer = AutoTokenizer.from_pretrained(local_model_path)
      self.model = AutoModelForSeq2SeqLM.from_pretrained(local_model_path)
    else:
      self.tokenizer = AutoTokenizer.from_pretrained(model_name)
      self.model = AutoModelForSeq2SeqLM.from_pretrained(model_name)
      
  
  def tokenize(self, batch):
      inputs = self.tokenizer(batch["noisy"], padding=True, truncation=True)
      with self.tokenizer.as_target_tokenizer():
          labels = self.tokenizer(batch["clean"], padding=True, truncation=True)
      inputs["labels"] = labels["input_ids"]
      return inputs
    
    
  def train(self):
    data = []
    items = db.get_items()
    for item in items:
      data.append({'id': item['id'], 'noisy': item['filename'], 'clean': item['title']})
    dataset = Dataset.from_list(data)
    dataset = dataset.train_test_split(test_size=0.2)    
    
    train_dataset = dataset['train'].map(self.tokenize, batched=True)
    eval_dataset = dataset['test'].map(self.tokenize, batched=True)
    
    # Training
    training_args = TrainingArguments(
        output_dir="./results",
        num_train_epochs=2,
        per_device_train_batch_size=2,
        logging_dir="./logs",
    )

    trainer = Trainer(
        model=self.model,
        args=training_args,
        train_dataset=train_dataset,
        eval_dataset=eval_dataset,
        tokenizer=self.tokenizer,
    )

    trainer.train()
    
    # Save model
    self.model.save_pretrained(self.local_model_path)
    # Save tokenizer
    self.tokenizer.save_pretrained(self.local_model_path)


  def filter(self):
    items = db.get_items(workflow=Workflow.FILTERING)
    for item in items:
      inputs = self.tokenizer(item['filename'], return_tensors="pt")
      output = self.model.generate(**inputs)
      title = self.tokenizer.decode(output[0], skip_special_tokens=True)
      print(f'# Original: {item["filename"]} --> Predicted: {title} ')
      
      hits = db.get_items(workflow=Workflow.QUERYING, sql=f'and lower(title) = "{title.lower()}"')
      if len(hits)>0:
        print(f'# Found existing title "{title}" in DB, skipping update.')
        db.del_item(item['id'])
        continue
      
      db.update_item({'id': item['id'], 'title': title})

model = MyRargbModel()
if __name__ == "__main__":
  model.filter()