import threading

from copilot_suporte.documentos import dividir, html_para_texto
from copilot_suporte.geracao import AGORA, DEPOIS, FilaDeGeracao
from copilot_suporte.privacidade import mascarar
from copilot_suporte.usuarios import conferir_senha, hash_senha


# ---------- privacidade ----------

def test_mascarar_email_cpf_cnpj_telefone():
    texto = "joao@empresa.com.br, CPF 123.456.789-09, CNPJ 12.345.678/0001-90, fone (11) 98765-4321"
    saida = mascarar(texto)
    for dado in ("joao@", "123.456", "12.345.678", "98765"):
        assert dado not in saida
    assert "[e-mail]" in saida and "[CPF]" in saida and "[CNPJ]" in saida and "[telefone]" in saida


def test_mascarar_nome_na_saudacao():
    assert mascarar("Olá Seven, seu chamado foi resolvido") == "Olá [cliente], seu chamado foi resolvido"
    assert mascarar("Bom dia, Maria!") == "Bom dia, [cliente]!"


def test_mascarar_nao_mexe_em_numeros_de_cupom():
    assert mascarar("cupons 000123327, 000123372") == "cupons 000123327, 000123372"


# ---------- senhas ----------

def test_senha_confere_e_hash_tem_sal():
    guardado = hash_senha("segredo-forte")
    assert conferir_senha("segredo-forte", guardado)
    assert not conferir_senha("errada", guardado)
    assert hash_senha("segredo-forte") != guardado


# ---------- documentos ----------

def test_dividir_respeita_o_tamanho_e_sobrepoe():
    texto = "\n\n".join(f"parágrafo {i} " + "x" * 300 for i in range(10))
    trechos = dividir(texto, tamanho=1000, sobreposicao=100)
    assert len(trechos) > 1
    assert all(len(t) <= 1000 + 400 for t in trechos)


def test_html_para_texto_ignora_script_e_pega_o_titulo():
    titulo, texto = html_para_texto("<html><title>Artigo</title><script>x()</script><p>Olá</p><p>mundo</p></html>")
    assert titulo == "Artigo"
    assert "x()" not in texto and "Olá" in texto and "mundo" in texto


# ---------- fila de geração ----------

def test_fila_gera_o_pedido_do_analista_antes_da_pregeracao():
    liberar = threading.Event()
    ordem = []

    def gerar(ticket_id, **_):
        if ticket_id == 1:
            liberar.wait(2)  # segura o primeiro enquanto a fila enche
        ordem.append(ticket_id)

    fila = FilaDeGeracao(gerar)
    fila.pedir(1, DEPOIS)
    while fila.posicao(1) != 0:
        pass
    fila.pedir(2, DEPOIS)
    fila.pedir(3, AGORA)
    assert fila.posicao(3) < fila.posicao(2)
    liberar.set()
    while len(ordem) < 3:
        pass
    assert ordem == [1, 3, 2]


def test_pedido_do_analista_interrompe_a_pregeracao_e_ela_volta_para_a_fila():
    from copilot_suporte.llm import Cancelado

    comecou = threading.Event()
    ordem = []

    def gerar(ticket_id, cancelado, escrever):
        if ticket_id == 1 and not ordem:
            comecou.set()
            while not cancelado():  # gerando devagar, até alguém precisar da GPU
                pass
            ordem.append("1 interrompido")
            raise Cancelado()
        escrever("texto")
        ordem.append(ticket_id)

    fila = FilaDeGeracao(gerar)
    fila.pedir(1, DEPOIS)
    comecou.wait(2)
    fila.pedir(2, AGORA)
    while len(ordem) < 3:
        pass
    assert ordem == ["1 interrompido", 2, 1]


def test_pergunte_pausa_a_pregeracao():
    feitos = []
    fila = FilaDeGeracao(lambda tid, **_: feitos.append(tid))
    with fila.interativo():
        fila.pedir(7, DEPOIS)
        threading.Event().wait(0.2)
        assert feitos == []  # ninguém usa a GPU em segundo plano durante o Pergunte
    threading.Event().wait(0.3)
    assert feitos == [7]


# ---------- rascunho em texto com seções ----------

def test_ler_secoes_da_solucao():
    from copilot_suporte.copiloto import ler_secoes

    texto = (
        "## Resumo\nO PDV trava no cartão.\n## Passos\n1. Refazer o vínculo do PinPad\n- Reprocessar a venda\n"
        "## Resposta ao cliente\nOlá, ajustamos."
    )
    c = ler_secoes(texto, "solucao")
    assert c == {"resumo": "O PDV trava no cartão.", "passos": ["Refazer o vínculo do PinPad", "Reprocessar a venda"],
                 "resposta_cliente": "Olá, ajustamos."}


def test_ler_secoes_tolera_texto_incompleto():
    from copilot_suporte.copiloto import ler_secoes

    c = ler_secoes("## O que pode ser\n- Caixa fechado duas vezes\n## Onde olh", "analise")
    assert c["hipoteses"] == ["Caixa fechado duas vezes"]
    assert c["perguntar"] == [] and c["resumo"] == ""


# ---------- medição ----------

def test_medicao_soma_etapas_e_resume_por_rota():
    from copilot_suporte import medicao

    cronometro = medicao.iniciar()
    with medicao.etapa("banco"):
        pass
    with medicao.etapa("banco"):
        pass
    assert cronometro["contagem"]["banco"] == 2
    assert 'banco;dur=' in medicao.server_timing(cronometro, 0.01)
    for ms in (10, 20, 30, 40, 1000):
        medicao.registrar({"tipo": "requisicao", "rota": "GET /api/teste", "ms": ms, "etapas": {"banco": ms / 2}})
    rota = next(r for r in medicao.resumo()["rotas"] if r["rota"] == "GET /api/teste")
    assert rota["n"] == 5 and rota["p50"] == 30 and rota["p95"] == 1000


def test_marcador_nao_encontrei_so_vale_sozinho():
    from copilot_suporte.copiloto import NAO_ENCONTREI, _inicio_de_marcador, sem_marcador, so_marcador

    assert so_marcador(NAO_ENCONTREI) and so_marcador(f"  {NAO_ENCONTREI}.\n")
    ressalva = f"Corrija o JSON [1]. {NAO_ENCONTREI} um script específico, mas o passo a passo está no guia [2]."
    assert not so_marcador(ressalva)
    assert NAO_ENCONTREI not in sem_marcador(ressalva) and "não encontrei um script" in sem_marcador(ressalva)
    assert so_marcador("NAO_ENC", parcial=True) and not so_marcador("Para resolver", parcial=True)
    assert _inicio_de_marcador("Veja o guia. NAO_EN") == 6 and _inicio_de_marcador("Veja o guia.") == 0


def test_marcador_com_acento_ou_espaco():
    from copilot_suporte.copiloto import _inicio_de_marcador, sem_marcador, so_marcador

    assert so_marcador("NÃO_ENCONTREI") and so_marcador("NAO ENCONTREI.")
    assert sem_marcador("Passos [1]. NÃO_ENCONTREI scripts SQL.") == "Passos [1]. não encontrei scripts SQL."
    assert _inicio_de_marcador("Passos. NÃO_ENC") == 7


def test_pausar_pregeracao_descarta_e_recusa_a_de_segundo_plano():
    import threading

    from copilot_suporte.geracao import AGORA, DEPOIS, FilaDeGeracao

    liberar = threading.Event()
    fila = FilaDeGeracao(lambda tid, cancelado, escrever: liberar.wait(5))
    fila.pedir(1, AGORA)  # ocupa a fila
    fila.pedir(2, DEPOIS)
    fila.pausar(True)
    assert fila.posicao(2) is None  # a de segundo plano saiu da fila
    fila.pedir(3, DEPOIS)
    assert fila.posicao(3) is None  # e não entra outra enquanto pausada
    fila.pedir(4, AGORA)
    assert fila.posicao(4) is not None  # o pedido do analista continua valendo
    liberar.set()
