import pytest

from copilot_suporte.documentos import LIMITE_BYTES, guardar, nome_seguro


def test_nome_seguro_tira_pastas_e_caracteres_proibidos():
    assert nome_seguro("../../etc/passwd.md") == "passwd.md"
    assert nome_seguro(r"C:\pasta\guia:caixa?.pdf") == "guiacaixa.pdf"
    assert nome_seguro("") == "documento"


@pytest.mark.parametrize("nome, conteudo, mensagem", [
    ("virus.exe", b"MZ", "Formato"),
    ("vazio.pdf", b"", "vazio"),
    ("grande.txt", b"x" * (LIMITE_BYTES + 1), "25 MB"),
], ids=["formato", "vazio", "grande"])
def test_guardar_recusa_antes_de_gravar(tmp_path, nome, conteudo, mensagem):
    with pytest.raises(ValueError, match=mensagem):
        guardar(None, tmp_path, nome, conteudo)
    assert not any(tmp_path.iterdir())
