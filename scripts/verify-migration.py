"""Run migrated algorithms in disposable storage, never in either project's business database."""
import os
from pathlib import Path
import sys
import tempfile
import pytest

root=Path(__file__).resolve().parents[1]
os.chdir(root)
with tempfile.TemporaryDirectory(prefix='three-brain-migration-') as temporary:
    os.environ['STRUCTURED_DATA_DIR']=str(Path(temporary)/'structured')
    os.environ['PLATFORM_INSTALLATION_DIR']=str(Path(temporary)/'installation')
    os.environ['JWT_SECRET']='isolated-migration-regression-secret-not-for-deployment'
    sys.path[:0]=[str(root/'services/structured'),str(root/'services/cnts/python')]
    names=['test_cnts_storage_adapter.py','test_runtime_profiles.py','test_decimal_aggregate.py','test_import_progress.py','test_registration_sources.py','test_storage_maintenance.py','test_time_format.py','test_growth_plan.py']
    try:
        result=pytest.main(['-o','addopts=','--tb=short','-q',*[str(root/'services/structured/tests'/n) for n in names],str(root/'services/cnts/tests')])
    finally:
        from engine.storage.database import engine
        engine.dispose()
        import logging
        logging.shutdown()
sys.exit(result)
