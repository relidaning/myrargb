from enum import Enum


class Workflow(Enum):
    TRAINING = 0
    PREDICT = 1
    SCORING = 2
    QUERYING = 3
    NONE = 4
