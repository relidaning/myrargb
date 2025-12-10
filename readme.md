# MyRargb

[Rargb](https://rargb.to/) is a resources shared website, I'd like downloading the tv series and movies there.

Unexpectedly there is too much text describe the quality and resources which I don't care, what I care is how to pick up a target to put on a great show. Therefore, the scores of the resource in imdb is important to me.

So I built this app with the help of ChatGPT.

Here is how it works:

Crawl movies/tv shows from rargb, use the llm [t5-small](https://huggingface.co/google-t5/t5-small)(which is used for sequence-to-sequence/text-to-text language model built by google) to extract the title, then fetch the score for it in imdb according to the title. 

Providing the UI, poster...
# Usages

```
cd myrargb
python app.py
```

# TBC
