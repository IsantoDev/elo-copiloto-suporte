from copilot_suporte.copiloto import planner


def test_tira_o_protocolo_repetido_e_o_traco_depois_do_cliente():
    p = planner(" [Pax Nacional] - Erro ORA-00905 no relatório - [202609003155]", None)
    assert p == {"titulo": "[Pax Nacional] Erro ORA-00905 no relatório", "url": None}


def test_protocolo_sem_colchetes():
    assert planner("[ACME] Boletos em lote - 202609003116", None)["titulo"] == "[ACME] Boletos em lote"


def test_guarda_a_url_quando_veio_crua():
    url = "https://tasks.office.com/empresa/Home/Task/abc"
    assert planner(None, url) == {"titulo": None, "url": url}


def test_link_do_teams_colado_vira_link_direto_do_card():
    teams = ("https://teams.microsoft.com/l/entity/com.microsoft.teamspace.tab.planner/mytasks?context=%7B%22subEntityId%22%3A"
             "%22%2Fv1%2Fplan%2FhL3rsz6Lfk6m48yeY2MgFmUAHik6%2Fview%2Fboard%2Ftask%2FDfUvdyMgFEep3Rv9sWWybmUAHZQr%22%7D")
    url = planner("[ACME] Algo", teams)["url"]
    assert "/plan/hL3rsz6Lfk6m48yeY2MgFmUAHik6/" in url and "/task/DfUvdyMgFEep3Rv9sWWybmUAHZQr" in url


def test_card_ligado_traz_coluna_e_responsaveis():
    from datetime import date

    linha = {"card_id": "abc123abc123abc123abc1", "card_plano_id": "hL3rsz6Lfk6m48yeY2MgFmUAHik6", "card_titulo": "[ACME] Card",
             "card_coluna": "Em Desenvolvimento", "card_status": "In progress", "card_progresso": 50,
             "card_responsaveis": "Ana Souza;Bruno Lima", "card_prazo": date(2026, 10, 10), "card_concluido_em": None}
    p = planner("[ACME] Card", None, linha)
    assert p["coluna"] == "Em Desenvolvimento" and p["responsaveis"] == ["Ana Souza", "Bruno Lima"]
    assert p["prazo"] == "2026-10-10" and not p["concluido"] and "/task/abc123abc123abc123abc1" in p["url"]


def test_sem_planner():
    assert planner(None, None) is None
    assert planner(" - ", None) is None


def test_casar_pelo_protocolo_e_pelo_titulo():
    from copilot_suporte.planner import casar

    cards = [{"id": "c1", "titulo": "[ACME] Boletos em lote", "criado_em": "2026-09-01", "protocolos": ["202609003116"]},
             {"id": "c2", "titulo": "[Trapezio] - Smart pos com venda travada.", "criado_em": "2026-09-02", "protocolos": []}]
    tickets = [{"ticket_id": 3116, "protocolo": "202609003116", "planner_titulo": None},
               {"ticket_id": 2616, "protocolo": "202607002616", "planner_titulo": "Smart pos com venda travada."},
               {"ticket_id": 1, "protocolo": "202601000001", "planner_titulo": "curto"}]
    assert casar(tickets, cards) == {3116: ("c1", "protocolo"), 2616: ("c2", "titulo")}


def test_ler_json_do_power_automate(tmp_path):
    import json

    from copilot_suporte.planner import ler_json

    arquivo = tmp_path / "planner.json"
    arquivo.write_text(json.dumps({"plano": "P1", "nome": "Demandas", "colunas": [{"id": "b1", "name": "Em Desenvolvimento"}],
        "tarefas": [{"id": "t1", "title": "[X] Algo - 202609003116", "bucketId": "b1", "percentComplete": 50, "priority": 3,
                     "dueDateTime": "2026-10-10T03:00:00Z", "createdDateTime": "2026-09-01T12:00:00Z"}]}), encoding="utf-8")
    lido = ler_json(arquivo)
    c = lido["cards"][0]
    assert lido["plano_id"] == "P1" and c["coluna"] == "Em Desenvolvimento" and c["status"] == "In progress"
    assert c["prazo"] == "2026-10-10" and c["protocolos"] == ["202609003116"] and c["prioridade"] == "Important"


def test_ler_json_guarda_os_ids_dos_responsaveis(tmp_path):
    import json

    from copilot_suporte.planner import ler_json

    arquivo = tmp_path / "planner.json"
    arquivo.write_text(json.dumps({"plano": "P1", "colunas": [], "tarefas": [
        {"id": "t1", "title": "x", "percentComplete": 0, "assignments": {"u1": {}, "u2": {}}}]}), encoding="utf-8")
    assert ler_json(arquivo)["cards"][0]["ids_responsaveis"] == ["u1", "u2"]
