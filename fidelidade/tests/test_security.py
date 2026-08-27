"""Testes das funções de segurança do CPF (puras, sem banco)."""

from app.domain.security import hash_cpf, mask_cpf

PEPPER = "test-pepper"
CPF = "123.456.789-09"
CPF_DIGITS = "12345678909"


def test_same_cpf_same_hash():
    assert hash_cpf(CPF, pepper=PEPPER) == hash_cpf(CPF, pepper=PEPPER)


def test_normalization_ignores_formatting():
    # CPF formatado e só-dígitos devem gerar o mesmo hash.
    assert hash_cpf(CPF, pepper=PEPPER) == hash_cpf(CPF_DIGITS, pepper=PEPPER)


def test_different_cpfs_different_hashes():
    assert hash_cpf("11111111111", pepper=PEPPER) != hash_cpf(
        "22222222222", pepper=PEPPER
    )


def test_different_pepper_changes_hash():
    assert hash_cpf(CPF, pepper="a") != hash_cpf(CPF, pepper="b")


def test_hash_does_not_contain_raw_cpf():
    h = hash_cpf(CPF, pepper=PEPPER)
    assert CPF_DIGITS not in h
    assert CPF not in h


def test_hash_is_hex_sha256():
    h = hash_cpf(CPF, pepper=PEPPER)
    assert len(h) == 64
    int(h, 16)  # levanta ValueError se não for hex


def test_mask_cpf():
    assert mask_cpf(CPF) == "123****09"
    assert mask_cpf(CPF_DIGITS) == "123****09"


def test_mask_cpf_short_input():
    assert mask_cpf("123") == "***"
