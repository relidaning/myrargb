from bloom_filter import BloomFilter


class BloomUtils:
    def __init__(self):
        self.instance = BloomFilter(filename="./bf.bin")

    def add(self, item: str) -> None:
        self.instance.add(item)

    def hasItem(self, item: str) -> bool:
        return item in self.instance


if __name__ == "__main__":
    bf = BloomUtils()
    print(bf.hasItem("test"))
    bf.add("test")
    print(bf.hasItem("test"))
