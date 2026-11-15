"""Compare application and experimental models without changing their weights."""
import argparse
from pathlib import Path
import sys
import tomllib

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from model.experiment_runner import load_config
from model.experiment_compare import compare


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,default=ROOT/'config/model-comparison.toml')
    parser.add_argument('--protocol',choices=['application','experiment'])
    args=parser.parse_args()
    config=tomllib.loads(args.config.read_text())
    protocol=args.protocol or config.pop('default_protocol','application')
    config.pop('default_protocol',None)
    config.update(config.pop('protocols')[protocol])
    config['protocol']=protocol
    compare(config,load_config(ROOT/config['experiment_config']))
