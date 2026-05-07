# MyRargb

![image](./myrargb.png)

[Rargb](https://rargb.to/) is a resources shared website, I often download TV shows and movies there.

Unexpectedly there are too many duplicated entries and no pictures showed up, make me feel less intuitive.

So I built this app with the help of ChatGPT.

Here is how it works:
- Crawl movies/tv shows from rargb
- use the llm [t5-small](https://huggingface.co/google-t5/t5-small)(which is used for sequence-to-sequence/text-to-text language model built by google) to extract the title
- fetch the score for it from imdb

## Usages

```bash
cd myrargb
uv sync
python app.py
```
