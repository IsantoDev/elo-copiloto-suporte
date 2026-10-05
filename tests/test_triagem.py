from datetime import datetime, timedelta, timezone

from copilot_suporte.triagem import Vizinho, eh_urgente, nome_urgencia, risco_sla, tipo_de_rascunho, votar

AGORA = datetime(2026, 10, 2, 12, tzinfo=timezone.utc)


def v(sim, modulo="PDV", urgencia="3 - Média", horas=None, solucao="ok", item=None):
    return Vizinho(1, sim, modulo=modulo, item=item, urgencia=urgencia, horas_resolucao=horas, solucao_publica=solucao)


def test_nome_urgencia_tira_o_numero():
    assert nome_urgencia("2 - Alta") == "Alta"
    assert nome_urgencia(None) is None


def test_votar_pondera_pela_similaridade():
    s = votar([v(0.9, "PDV"), v(0.8, "PDV"), v(0.5, "Fiscal")], "modulo")
    assert s.valor == "PDV"
    assert s.confianca == round(1.7 / 2.2, 2)
    assert s.votos == 2


def test_votar_sem_vizinhos_suficientes_nao_sugere():
    assert votar([v(0.9), v(0.8)], "modulo") is None


def test_votar_urgencia_usa_o_nome():
    assert votar([v(0.9, urgencia="2 - Alta")] * 3, "urgencia").valor == "Alta"


def test_servico_junta_modulo_e_item():
    assert v(0.9, "PDV Fixo", item="Impressão").servico == "PDV Fixo › Impressão"


def test_risco_estourado_quando_o_prazo_passou():
    r = risco_sla([], AGORA - timedelta(days=3), AGORA - timedelta(hours=2), AGORA)
    assert r.nivel == "estourado"


def test_risco_sem_prazo_e_pausado():
    assert risco_sla([], AGORA, None, AGORA).nivel == "sem_prazo"
    assert risco_sla([], AGORA, AGORA + timedelta(days=1), AGORA, pausado=True).nivel == "pausado"


def test_risco_alto_quando_os_parecidos_levam_mais_que_o_prazo():
    vizinhos = [v(0.8, horas=h) for h in (100, 120, 150, 30)]
    r = risco_sla(vizinhos, AGORA - timedelta(hours=10), AGORA + timedelta(hours=38), AGORA)  # prazo total 48 h
    assert r.nivel == "alto"
    assert r.probabilidade == 0.75


def test_risco_condiciona_no_tempo_ja_decorrido():
    # aberto há 40 h, prazo de 100 h: os parecidos resolvidos em 2 h já não contam
    vizinhos = [v(0.8, horas=h) for h in (2, 2, 2, 60, 70, 80)]
    r = risco_sla(vizinhos, AGORA - timedelta(hours=40), AGORA + timedelta(hours=60), AGORA)
    assert r.probabilidade == 0.0
    assert r.base == 3


def test_rascunho_de_solucao_so_com_limiar_e_concordancia():
    tipo, base = tipo_de_rascunho([v(0.9, "PDV"), v(0.88, "PDV"), v(0.7, "Fiscal")], 0.85)
    assert tipo == "solucao"
    assert len(base) == 2


def test_rascunho_de_analise_quando_os_vizinhos_discordam():
    tipo, _ = tipo_de_rascunho([v(0.9, "PDV"), v(0.88, "Fiscal"), v(0.87, "Caixa")], 0.85)
    assert tipo == "analise"


def test_rascunho_de_analise_abaixo_do_limiar():
    tipo, base = tipo_de_rascunho([v(0.7), v(0.6)], 0.85)
    assert tipo == "analise"
    assert len(base) == 2


def test_rascunho_ignora_vizinhos_sem_solucao():
    tipo, base = tipo_de_rascunho([v(0.95, solucao=None)], 0.85)
    assert (tipo, base) == ("analise", [])


def test_urgente_por_urgencia_ou_risco():
    baixo = risco_sla([], AGORA, None, AGORA)
    assert eh_urgente("2 - Alta", baixo)
    assert not eh_urgente("Média", baixo)
    assert eh_urgente("Média", risco_sla([], AGORA - timedelta(days=2), AGORA - timedelta(hours=1), AGORA))


def test_situacao_separa_a_espera_pelo_motivo():
    from copilot_suporte.triagem import situacao

    assert situacao("New", "Novo", None) == "novo"
    assert situacao("InAttendance", "Em atendimento", "Analise do Especialista") == "atendimento"
    assert situacao("Stopped", "Aguardando", "Time de produto") == "produto"
    assert situacao("Stopped", "Em Desenvolvimento", None) == "produto"
    assert situacao("Stopped", "Aguardando", "Retorno do cliente") == "cliente"
    assert situacao("Stopped", "Aguardando", "Time Comercial") == "outros"
