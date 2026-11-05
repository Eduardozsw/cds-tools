import pytest

from cds_tools import config, study


@pytest.mark.parametrize(
    "term, expected",
    [
        ("2 Semestre 2026", "2026.2"),
        ("1º Semestre 2026", "2026.1"),
        ("1 º semestre de 2025", "2025.1"),
        ("Módulo Especial", "Módulo Especial"),
    ],
)
def test_period_name(term, expected):
    assert study.period_name(term) == expected


def test_subject_name_drops_class_suffix():
    assert (
        study.subject_name({"id": "_1_1", "name": "Cálculo Diferencial e Integral II - 80h_T1_26_2"})
        == "Cálculo Diferencial e Integral II"
    )


@pytest.mark.parametrize("rel", ["../fora.txt", "2026.2/../../fora.txt", "/etc/passwd"])
def test_resolve_stays_inside_study_dir(rel):
    with pytest.raises(ValueError):
        study.resolve(rel)


def test_save_and_read_summary():
    (config.STUDY_DIR / "2026.2" / "Cálculo II").mkdir(parents=True)
    rel = study.save_summary("2026.2/Cálculo II", "Unidade 1: limites", "# Limites\n")
    assert rel == "2026.2/Cálculo II/resumos/Unidade 1_ limites.md"
    assert study.read(rel)["text"] == "# Limites\n"


def test_save_summary_requires_period_and_subject():
    (config.STUDY_DIR / "2026.2").mkdir(parents=True)
    with pytest.raises(ValueError):
        study.save_summary("2026.2", "x", "y")
