from cryptography.fernet import Fernet

from copilot_suporte.acessos import Acessos, ler_importacao


def test_senha_cifrada_volta_igual_e_nao_aparece_no_texto_guardado(monkeypatch):
    monkeypatch.setenv("ELO_CHAVE_ACESSOS", Fernet.generate_key().decode())
    acessos = Acessos(banco=None)
    cifrada = acessos.cifrar("s3nh@-de-teste")
    assert "s3nh@" not in cifrada
    assert acessos.decifrar(cifrada) == "s3nh@-de-teste"
    assert acessos.cifrar("") is None and acessos.decifrar(None) == ""


def test_importa_csv_com_ponto_e_virgula_e_nomes_variados():
    linhas = ler_importacao("Cliente;Sistema;IP;Login;Senha;Obs\nACME;VPN;10.0.0.1;suporte;x;só via VPN\n")
    assert linhas == [{"cliente": "ACME", "nome": "VPN", "endereco": "10.0.0.1", "usuario": "suporte",
                       "senha": "x", "observacao": "só via VPN"}]


def test_importa_json_agrupado_por_cliente():
    linhas = ler_importacao('{"LARCO": [{"nome": "AnyDesk", "usuario": "1 234 567"}]}')
    assert linhas == [{"cliente": "LARCO", "nome": "AnyDesk", "usuario": "1 234 567"}]
