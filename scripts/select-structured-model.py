"""Select an explicitly requested model without exposing or replacing its credential."""
import argparse
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from platform_server.store import Store
from platform_server.installation import ROOT

if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--config-id',required=True)
    parser.add_argument('--expected-model',required=True)
    parser.add_argument('--model',required=True)
    args=parser.parse_args()
    store=Store(ROOT/'.runtime/standalone/platform')
    model=store.get('model','system',args.config_id)
    if not model or model['brain']!='structured' or model['purpose']!='chat':
        raise SystemExit('The specified structured chat configuration does not exist')
    if model['model'] not in {args.expected_model,args.model}:
        raise SystemExit('Configuration changed concurrently; refusing to overwrite it')
    model['model']=args.model
    store.put('model','system',model)
    store.audit('maintenance:codex','model.select.requested',model['id'],{'model':args.model})
    print('Selected model:',args.model)
