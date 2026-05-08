from bloom_filter import BloomFilter


class BloomUtils:
    def __init__(self):
        self.instance = BloomFilter(filename="./data/bf.bin")

    def add(self, item: str) -> None:
        self.instance.add(deal_string(item))

    def hasItem(self, item: str) -> bool:
        item = deal_string(item)
        if item == "":
            return True
        else:
            return item in self.instance


def deal_string(s: str) -> str:
    return s.replace(".", " ").lower().strip()


if __name__ == "__main__":
    bf = BloomUtils()
    print(bf.hasItem("test"))
    bf.add("test")
    print(bf.hasItem("test"))
