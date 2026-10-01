from bm25 import Bm25Index, tokenize


def test_tokenize_lowercases_and_splits_words():
    assert tokenize("Le Délai de Retours ?!") == ["le", "délai", "de", "retours"]


def test_empty_corpus_scores_are_empty():
    assert Bm25Index([]).scores("n'importe quoi") == []


def test_document_matching_query_ranks_first():
    index = Bm25Index(
        [
            "La réponse est locale.",
            "Le délai de retour est de trente jours.",
            "Aucun terme commun ici.",
        ]
    )
    scores = index.scores("délai retour")

    assert scores[1] == max(scores)
    assert scores[2] == 0.0


def test_unmatched_query_yields_zero_scores():
    index = Bm25Index(["un texte"])

    assert index.scores("zzzz") == [0.0]


def test_shorter_document_with_same_term_ranks_higher():
    index = Bm25Index(["gamma et beaucoup d'autres mots dans ce long texte", "gamma"])

    assert index.scores("gamma")[1] > index.scores("gamma")[0]
