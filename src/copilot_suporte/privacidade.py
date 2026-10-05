"""Mascaramento de dados pessoais antes de mostrar um texto ou enviá-lo ao LLM."""

import re

PADROES = (
    (re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+"), "[e-mail]"),
    (re.compile(r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b"), "[CNPJ]"),
    (re.compile(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b|\b\d{3}\.?\d{3}\.?\d{3}-\d{2}\b"), "[CPF]"),
    (re.compile(r"(?<!\d)(\(?\d{2}\)?[\s-]?)?9?\d{4}[\s-]\d{4}(?!\d)"), "[telefone]"),
    # "Olá Fulano," / "Bom dia, Fulana!": o nome do cliente na saudação
    (
        re.compile(r"\b(olá|ola|oi|bom dia|boa tarde|boa noite|prezad[oa]|car[oa])([ ,]+)[A-ZÀ-Ú][\wà-ú]+", re.IGNORECASE),
        r"\1\2[cliente]",
    ),
)


def mascarar(texto: str | None) -> str:
    """Troca e-mail, CPF, CNPJ, telefone e o nome da saudação por marcadores."""
    if not texto:
        return ""
    for padrao, troca in PADROES:
        texto = padrao.sub(troca, texto)
    return texto.replace("\xa0", " ")
