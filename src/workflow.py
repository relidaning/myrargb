from enum import Enum


class Workflow(Enum):
    TRAINING = "10"
    PREDICT = "20"
    SCORING = "30"
    QUERYING = "40"
    DEDUPLICATION = "50"
    NONE = "00"
