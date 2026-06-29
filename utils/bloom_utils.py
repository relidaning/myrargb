import re
from bloom_filter import BloomFilter

_PUNCT_RE = re.compile(r'[^a-z0-9]+')


class BloomUtils:
    def __init__(self):
        self.instance = BloomFilter(filename="./data/bf.bin")

    def add(self, item: str) -> None:
        self.instance.add(normalize(item))

    def hasItem(self, item: str) -> bool:
        normed = normalize(item)
        if not normed:
            return True
        return normed in self.instance


def normalize(s: str) -> str:
    """Normalize a title for dedup: lowercase, collapse all non-alphanumeric to space.

    'a.b.c', 'a b c', 'a-b-c', 'a_b_c' all become 'a b c'.
    """
    s = _PUNCT_RE.sub(' ', s.lower())
    return s.strip()


if __name__ == "__main__":
    bf = BloomUtils()
    print(bf.hasItem("test"))
    bf.add("test")
    print(bf.hasItem("test"))
