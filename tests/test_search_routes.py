"""Contract HTTP al cautarii unificate: rutare intrebari si filtre inainte de transport."""
from flask import Flask
import pytest

import ask
import routes_analytics as routes


@pytest.fixture
def client(monkeypatch):
    app=Flask(__name__)
    app.register_blueprint(routes.bp)
    monkeypatch.setattr(routes.semantic,'enabled',lambda:True)
    monkeypatch.setattr(routes,'_visible_ids',lambda:{2})
    monkeypatch.setattr(routes,'get_cameras',lambda:[{'id':2,'name':'Poarta'}])
    monkeypatch.setattr(routes.ai_client,'remote',lambda:False)
    return app.test_client()


def test_search_question_uses_same_answer_and_days(client,monkeypatch):
    seen=[]
    def answer(q,**kw):
        seen.append(kw)
        return {'raspuns':'test','aparitii':[],'period':'ieri'}
    monkeypatch.setattr(ask,'answer',answer)
    a=client.get('/api/search',query_string={'q':'cand a trecut masina?','days':7,'cam':2})
    b=client.get('/api/ask',query_string={'q':'cand a trecut masina?','days':7,'cam':2})
    assert a.status_code == b.status_code == 200
    assert a.json['raspuns'] == b.json['raspuns'] and a.json['kind'] == 'question'
    assert a.json['results'] == [] and seen[0] == seen[1]
    assert seen[0]['visible'] == {2} and seen[0]['days'] == 7


def test_selected_camera_is_intersected_before_search(client,monkeypatch):
    seen=[]
    def search(q,**kw):seen.append(kw);return []
    monkeypatch.setattr(routes.semantic,'search',search)
    assert client.get('/api/search?q=masina&cam=1').status_code == 200
    assert seen[0]['visible'] == set()


def test_question_errors_are_http_400(client,monkeypatch):
    monkeypatch.setattr(ask,'answer',lambda *a,**kw:{'error':'invalid','aparitii':[]})
    assert client.get('/api/search?q=cand%3F').status_code == 400
