from utils.settings import settings
from utils.titles import title_key
from bloom_filter import BloomFilter



class BloomUtils:
    def __init__(self):
        settings.bloom_path.parent.mkdir(parents=True, exist_ok=True)
        self.instance = BloomFilter(filename=str(settings.bloom_path))

    def add(self, item: str) -> None:
        self.instance.add(normalize(item))

    def hasItem(self, item: str) -> bool:
        normed = normalize(item)
        if not normed:
            return True
        return normed in self.instance


def normalize(s: str) -> str:
    return title_key(s)


if __name__ == "__main__":
    bf = BloomUtils()
    print(bf.hasItem("test"))
    bf.add("test")
    print(bf.hasItem("test"))
