"""Load only the public T5 base at an immutable, explicitly pinned revision."""
import re

BASE_MODEL = 'google-t5/t5-small'


def load_fresh_base(model_id, revision):
    if model_id != BASE_MODEL:
        raise ValueError('base_model must be google-t5/t5-small; local checkpoints are forbidden')
    if not isinstance(revision, str) or not re.fullmatch(r'[0-9a-f]{40}', revision):
        raise ValueError('base_revision must be an immutable 40-character Hub commit SHA')
    from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
    # token=False prevents implicit use of stored Hub credentials. No application
    # model wrapper, local-model fallback, optimizer or Trainer resume state is used.
    kwargs = dict(revision=revision, token=False, trust_remote_code=False)
    tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL, **kwargs)
    model = AutoModelForSeq2SeqLM.from_pretrained(BASE_MODEL, **kwargs)
    return tokenizer, model
