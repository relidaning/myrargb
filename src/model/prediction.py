"""One-off title extraction, sharing the application's inference protocol."""


class PredictionBusyError(RuntimeError):
    pass


def predict_text(tokenizer,model,text,max_input_tokens,min_new_tokens):
    import torch
    inputs=tokenizer('clean the title: '+text,return_tensors='pt',return_token_type_ids=False)
    if inputs['input_ids'].shape[-1]>max_input_tokens:
        raise ValueError('This text is too long for prediction. Try a shorter filename.')
    inputs={key:value.to(model.device) for key,value in inputs.items()}
    was_training=model.training
    try:
        model.eval()
        with torch.inference_mode():
            output=model.generate(**inputs,max_new_tokens=64,min_new_tokens=min_new_tokens,
                                  num_beams=4,early_stopping=True)
        return tokenizer.decode(output[0],skip_special_tokens=True)
    finally:
        model.train(was_training)
