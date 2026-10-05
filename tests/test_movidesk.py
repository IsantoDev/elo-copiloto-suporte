import pytest
import requests

from copilot_suporte.movidesk import ClienteMovidesk, juntar_sem_duplicar

ANTIGO = {"id": 7, "lastUpdate": "2026-09-01T10:00:00"}
NOVO = {"id": 7, "lastUpdate": "2026-09-02T10:00:00"}
OUTRO = {"id": 8, "lastUpdate": "2026-09-01T10:00:00"}


# ---------- deduplicação ----------

def test_juntar_mantem_a_versao_mais_nova():
    resultado = juntar_sem_duplicar([ANTIGO, OUTRO], [NOVO])
    assert sorted(t["id"] for t in resultado) == [7, 8]
    assert next(t for t in resultado if t["id"] == 7) == NOVO


def test_juntar_nao_depende_da_ordem_das_listas():
    resultado = juntar_sem_duplicar([NOVO], [ANTIGO, OUTRO])
    assert next(t for t in resultado if t["id"] == 7) == NOVO


# ---------- cliente da API, com uma sessão falsa ----------

class RespostaFalsa:
    def __init__(self, status, dados=None, headers=None):
        self.status_code = status
        self.ok = 200 <= status < 300
        self.headers = headers or {}
        self._dados = dados if dados is not None else []

    def json(self):
        return self._dados


class SessaoFalsa:
    """Devolve as respostas da lista, uma por chamada, e anota os parâmetros recebidos."""

    def __init__(self, respostas):
        self.respostas = list(respostas)
        self.chamadas = []

    def get(self, url, params, timeout):
        self.chamadas.append(params)
        resposta = self.respostas.pop(0)
        if isinstance(resposta, Exception):
            raise resposta
        return resposta


def cliente_com(respostas, esperas):
    return ClienteMovidesk("token-falso", sessao=SessaoFalsa(respostas), dormir=esperas.append)


def test_sem_token_falha_logo():
    with pytest.raises(RuntimeError, match="MOVIDESK_TOKEN"):
        ClienteMovidesk("")


def test_429_espera_o_retry_after_e_tenta_de_novo():
    esperas = []
    cliente = cliente_com([RespostaFalsa(429, headers={"retry-after": "7"}), RespostaFalsa(200, [{"id": 1}])], esperas)
    assert cliente.buscar_pagina("tickets", top=5) == [{"id": 1, "_empresa": None, "_solicitante": None}]
    assert esperas == [7]


def test_401_para_na_primeira_tentativa():
    esperas = []
    cliente = cliente_com([RespostaFalsa(401)], esperas)
    with pytest.raises(RuntimeError, match="401"):
        cliente.buscar_pagina("tickets", top=5)
    assert esperas == []


def test_erro_de_rede_nao_vaza_o_token():
    falha = requests.ConnectionError("https://api.movidesk.com/public/v1/tickets?token=token-falso")
    esperas = []
    cliente = cliente_com([falha, falha, falha], esperas)
    with pytest.raises(RuntimeError) as erro:
        cliente.buscar_pagina("tickets", top=5)
    assert "token-falso" not in str(erro.value)
    assert len(esperas) == 3


def test_paginacao_para_na_pagina_incompleta_e_respeita_o_limite():
    esperas = []
    paginas = [RespostaFalsa(200, [{"id": i} for i in range(2)]), RespostaFalsa(200, [{"id": 9}])]
    cliente = cliente_com(paginas, esperas)

    tickets = cliente.buscar_todos(por_pagina=2)

    assert [t["id"] for t in tickets] == [0, 1, 9]
    assert esperas == [6]  # uma espera entre as duas páginas (limite de 10 requisições por minuto)
    assert [c["$skip"] for c in cliente._sessao.chamadas] == [0, 2]


# ---------- empresa e quem abriu: só o necessário chega ao banco ----------

from copilot_suporte.movidesk import reduzir_pessoas  # noqa: E402


def test_reduzir_pessoas_guarda_so_empresa_e_primeiro_nome():
    t = reduzir_pessoas({
        "id": 1,
        "clients": [{"businessName": "Lisiane Souza Lima", "personType": 1, "profileType": 2,
                     "organization": {"businessName": "TRAPEZIO "}}],
        "createdBy": {"businessName": "Lisiane Souza Lima", "personType": 1, "profileType": 2},
    })
    assert t == {"id": 1, "_empresa": "TRAPEZIO", "_solicitante": "Lisiane"}


def test_agente_que_abriu_em_nome_do_cliente_nao_conta_como_solicitante():
    t = reduzir_pessoas({
        "clients": [{"businessName": "edson ramos", "personType": 1, "profileType": 2,
                     "organization": {"businessName": "PAX NACIONAL"}}],
        "createdBy": {"businessName": "Willian Agente", "personType": 1, "profileType": PERFIL_AGENTE},
    })
    assert (t["_empresa"], t["_solicitante"]) == ("PAX NACIONAL", "Edson")


def test_cliente_cadastrado_como_empresa():
    t = reduzir_pessoas({"clients": [{"businessName": "Empresa Exemplo", "personType": 2, "profileType": 2}]})
    assert (t["_empresa"], t["_solicitante"]) == ("Empresa Exemplo", None)


from copilot_suporte.movidesk import PERFIL_AGENTE  # noqa: E402
