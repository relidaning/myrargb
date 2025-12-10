from enum import Enum

class Workflow(Enum):
  TRAINING = 0
  FILTERING = 1
  SCORING = 2
  QUERYING = 3