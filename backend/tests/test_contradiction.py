from conftest import _bow

import contradiction
from search import clean_query

PASSAGE = ("We use the draft lottery to identify causal effects. We find no evidence that military service "
           "adversely affects food insecurity among veterans. The share of veterans fell from 32% in 1982 to 21% in 2017.")


def check(query):
    return contradiction.check(query, _bow(query), PASSAGE)


def test_faithful_sentences_pass():
    assert check("The draft lottery shows no evidence that military service adversely affects veterans food insecurity.") == ""
    assert check("The share of veterans dropped from 32% in 1982 to 21% in 2017.") == ""


def test_negation_direction_and_numbers():
    assert check("Military service adversely affects food insecurity among veterans.") == "negation"
    assert check("The share of veterans fell from 2% in 1982 to 5% in 2017.") == "numbers"
    assert check("The share of veterans rose from 21% in 1982 to 32% in 2017.") in {"direction", "numbers"}
    assert contradiction._numbers_disagree("passée de 21 % en 1982 à 32 % en 2017", "passée de 32 % en 1982 à 21 % en 2017")


def test_idioms_are_not_negations():
    assert not contradiction._NEG_RE.search("The effect can be estimated without bias, not only in theory.")
    assert contradiction._NEG_RE.search("Les autocars n'ont jamais été rentables.")


def test_percent_signs_survive_query_cleaning():
    assert clean_query("passée de 32 % en 1982 à 21 % en 2017") == "passée de 32 % en 1982 à 21 % en 2017"
    assert clean_query(r"a 32\% rise % a LaTeX comment") == "a 32% rise"


def test_numbers_tolerate_rounding_spaces_and_tables():
    assert not contradiction._numbers_disagree("6,3 milliards, soit 42 000 euros par salarié",
                                               "6,3 milliards de subventions pour 150 000 salariés, soit environ 42 000 euros")
    assert not contradiction._numbers_disagree("un peu plus de 91 % avec lui", "50.0 percent without it and 91.1 percent with it")
    assert contradiction._numbers_disagree("passant de 21 % à 32 %", "43 % des hommes contre 33 % en 2017")
    assert not contradiction._numbers_disagree("about 391,000 workers, 8% of employment",
                                               "2010 3 868 275 7.1% 2011 3 898 264 6.8% 2012 3 954 247 6.2% 2013 4 012 251 6.3%")
