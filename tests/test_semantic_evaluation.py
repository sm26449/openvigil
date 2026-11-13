"""Evaluarea nu trebuie sa transforme rezultate neetichetate in precizie aparent perfecta."""
import importlib.util
from pathlib import Path

spec=importlib.util.spec_from_file_location('semantic_evaluate',Path(__file__).parents[1]/'scripts/semantic_evaluate.py')
evaluate=importlib.util.module_from_spec(spec);spec.loader.exec_module(evaluate)


def test_unknown_judgments_do_not_produce_accuracy():
    row={'day':'2026-11-13','cam':1,'fn':'a.jpg'}
    assert evaluate.measure({'reviewed':False},[row])['precision_at_k'] is None
    assert evaluate.measure({'reviewed':True},[row])['status']=='partially_judged'
    assert evaluate.measure({'reviewed':True},[])['precision_at_k'] is None
    key=evaluate.asset_id(row)
    result=evaluate.measure({'reviewed':True,'positive_assets':[key,'known-but-missing']},[row])
    assert result['precision_at_k']==1 and result['known_positive_recall_at_k']==.5
