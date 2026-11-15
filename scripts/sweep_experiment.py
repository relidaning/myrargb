"""Run or resume a serial fresh-base hyperparameter screen on one GPU."""
import argparse
import fcntl
from pathlib import Path
import subprocess
import sys
import tomllib

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from model.experiment_runner import load_config,verify_data
from model.experiment_data import digest
from model.experiment_metrics import load_events,record_event
from model.experiment_sweep import (build_plan,successful_report,toml_text,render_comparison,
                                    render_seed_comparison,seed_statistics)
from model.training_data import write_json


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,default=ROOT/'config/sweep.toml')
    args=parser.parse_args()
    grid=tomllib.loads(args.config.read_text())
    base=load_config(ROOT/grid['base_config'])
    base.update({key:grid[key] for key in ['training_size','epochs']})
    if 'training_seed' in grid:
        base['training_seed']=grid['training_seed']
    directory,manifest=verify_data(base)
    plan=build_plan(base,grid,digest(directory/'manifest.json'))
    seed_mode=plan.get('kind')=='seed_confirmation'
    output=directory/'sweeps'/plan['sweep_id']
    output.mkdir(parents=True,exist_ok=True)
    with (output/'sweep.lock').open('a') as lock:
        try:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit('This sweep is already running')
        write_json(output/'plan.json',plan)
        failures=0
        for index,trial in enumerate(plan['trials'],1):
            config=trial['configuration']
            path=output/(trial['id']+'.toml')
            path.write_text(toml_text(config))
            load_config(path)  # validate generated settings before starting a child
            report=successful_report(directory,config,plan['manifest_sha256'],ignore_tracking=seed_mode)
            reused=report is not None
            if report is None:
                print(f'Trial {index}/{len(plan["trials"])}: {trial["id"]}',flush=True)
                result=subprocess.run([sys.executable,str(ROOT/'scripts/run_experiment.py'),'train','--config',str(path)],cwd=ROOT)
                report=successful_report(directory,config,plan['manifest_sha256'],ignore_tracking=seed_mode) if result.returncode==0 else None
            if report is not None:
                metrics={**report['final_metrics'],'duration_seconds':report['duration_seconds'],
                         'optimizer_steps':report.get('optimizer_steps')}
                record_event(plan['sweep_id'],'sweep_trial_completed',metrics,
                             {'sweep_id':plan['sweep_id'],'trial_id':trial['id'],'run_id':report['run_id'],'reused':reused})
                print(f"RESULT {trial['id']}: title_match={metrics['eval_title_match']:.4f}",flush=True)
            else:
                failures+=1
                record_event(plan['sweep_id'],'sweep_trial_failed',{},
                             {'sweep_id':plan['sweep_id'],'trial_id':trial['id']})
                print(f"FAILED {trial['id']}; next invocation retries from base weights",flush=True)
            events,bad,limited=load_events(sorted((ROOT/'logs').glob('????-??-??.log'))[-7:])
            renderer=render_seed_comparison if seed_mode else render_comparison
            (output/'comparison.html').write_text(renderer(plan,events),encoding='utf-8')
            if seed_mode:
                write_json(output/'summary.json',dict(sweep_id=plan['sweep_id'],
                    settings=seed_statistics(plan,events),test_evaluated=False,
                    malformed_log_events=bad,log_scan_limited=limited))
        print(output/'comparison.html',flush=True)
        return 1 if failures else 0


if __name__=='__main__':
    sys.exit(main())
